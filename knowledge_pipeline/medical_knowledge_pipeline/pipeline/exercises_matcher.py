# -*- coding: utf-8 -*-
"""习题册真题 ↔ 复习资料知识点 匹配 + 同域校验 + 反向索引写库。

把「习题册作为权威信号(is_exam_hit)判重点」这一步固化为正式管线模块,
替代以往的一次性实验脚本。角色与设计见策划笔记
`_meta/matcher-习题册匹配模块方案.md`。

流程:
    1. 从 SQLite 读复习资料知识点 title(knowledge_points 表) + 习题册真题 question(exercises 表)
    2. 全量 embedding(qwen3.7-text-embedding, batch≤10),算 title×question 余弦相似度
    3. 每个 question 取最相似的 title(≥ 阈值,默认 0.85) → 命中对
    4. 同域校验(疾病类型词表):真题含类型词而 title 完全不含 Q 的任何类型词 → 拦截"同域不同病"
    5. 写库:命中对 set is_exam_hit=1 + 写 reverse_index(point_id, exam_question, exam_answer),事务化
    --dry-run 只统计不写库;--clear 幂等重跑(先清空 reverse_index + 复位 is_exam_hit)

嵌入 key 从 knowledge_pipeline/.env 读 DASHSCOPE_API_KEY(base_url=dashscope),与 dedup.py 语义层一致。
匹配阈值/疾病类型词表在 config.py 集中管理(DISEASE_TYPE_WORDS / EXERCISE_MATCH_THRESHOLD)。
"""

import argparse
import importlib.util
import os
import sys
import time

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")

# --------------------------------------------------------------------------- #
# 路径与配置
# --------------------------------------------------------------------------- #
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)            # .../medical_knowledge_pipeline
PIPELINE_ROOT = os.path.dirname(ROOT_DIR)         # .../knowledge_pipeline


def _load_module(name, path):
    """按文件路径加载 Python 模块(与 extractor 一致,不受"pipeline 非包"限制)。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kb_store = _load_module("kb_store", os.path.join(SCRIPT_DIR, "kb", "kb_store.py"))
_config_mod = _load_module("config", os.path.join(SCRIPT_DIR, "config.py"))


def _config(name, default):
    return getattr(_config_mod, name, default)


DISEASE_TYPE_WORDS = _config("DISEASE_TYPE_WORDS", None)
EXERCISE_MATCH_THRESHOLD = _config("EXERCISE_MATCH_THRESHOLD", 0.85)


def _load_env():
    """读取 knowledge_pipeline/.env,返回 dict(effective last-wins)。"""
    env = {}
    env_path = os.path.join(PIPELINE_ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, val = line.partition("=")
                    env[k.strip()] = val.strip()
    return env


# --------------------------------------------------------------------------- #
# embedding
# --------------------------------------------------------------------------- #
def _client():
    """构建 OpenAI 客户端(dashscope 兼容端点)。"""
    from openai import OpenAI
    env = _load_env()
    key = env.get("DASHSCOPE_API_KEY")
    if not key:
        raise RuntimeError("DASHSCOPE_API_KEY 未在 knowledge_pipeline/.env 中设置,无法做语义嵌入")
    return OpenAI(
        api_key=key,
        base_url=env.get("DASHSCOPE_EMBED_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    )


def embed_texts(texts, model="qwen3.7-text-embedding", batch=10):
    """把一批文本转成 embedding 向量矩阵(已 L2 归一化)。batch≤10(dashscope 限制)。"""
    client = _client()
    vecs = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        resp = client.embeddings.create(model=model, input=chunk)
        vecs.extend(d.embedding for d in resp.data)
        time.sleep(0.2)
    arr = np.array(vecs, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


# --------------------------------------------------------------------------- #
# 同域校验(疾病类型词表)
# --------------------------------------------------------------------------- #
def _disease_type_words():
    if DISEASE_TYPE_WORDS:
        return set(DISEASE_TYPE_WORDS)
    # 兜底默认词表(策划笔记定稿,外科学现状):只描述病变性质,不含器官名
    return {
        "癌", "瘤", "结石", "梗阻", "脓肿", "扭转", "穿孔", "出血", "破裂", "狭窄",
        "栓塞", "梗死", "坏死", "溃疡", "炎症", "炎", "疝", "蛔虫", "囊肿", "瘘", "痔",
        "脱", "痉挛", "畸形", "肥厚",
    }


def check_same_domain(title, question):
    """同域不同病校验。

    真题 question 含疾病类型词而知识 title 完全不含 Q 的任何类型词 → 判为
    "同域不同病"(可能误配,如 肝内胆管结石←肝内胆管癌),返回 False(拦截)。
    否则返回 True(放行)。词表只描述病变性质、不含器官名,避免把"胆结石↔胆管癌"
    那种共享器官名的合法对拦掉。
    """
    if not question or not title:
        return True
    words = _disease_type_words()
    q_types = {w for w in words if w in question}
    if not q_types:
        return True
    t_types = {w for w in words if w in title}
    return bool(q_types & t_types)


def match_exercises(point_titles, exercise_questions, threshold=0.85):
    """embedding 匹配:每个 question 取最相似的 title(≥ threshold)。

    返回 [(score, point_idx, ex_idx), ...],按 score 降序。idx 是传入列表的下标,
    调用方自行映射回库内 id(exercises/点 的 id 与 question 顺序不对应,须外部映射)。
    """
    if not point_titles or not exercise_questions:
        return []
    pv = embed_texts(list(point_titles))
    ev = embed_texts(list(exercise_questions))
    sim = ev @ pv.T  # (n_exercises, n_points) 已归一化
    hits = []
    for ei in range(ev.shape[0]):
        pj = int(np.argmax(sim[ei]))
        s = float(sim[ei, pj])
        if s >= threshold and check_same_domain(point_titles[pj], exercise_questions[ei]):
            hits.append((s, pj, ei))
    hits.sort(key=lambda x: x[0], reverse=True)
    return hits


# --------------------------------------------------------------------------- #
# 写库
# --------------------------------------------------------------------------- #
def apply_matches(db, hits, points, exercises):
    """把命中对写库:set is_exam_hit + 写 reverse_index(事务)。

    hits: [(score, point_idx, ex_idx), ...]
    points: [(id, title, content...)] 与 match 时同序的复习知识点行
    exercises: [(id, question, answer...)] 与 match 时同序的习题册行
    返回 {hit: n, reverse: n} 统计 dict。
    """
    kb_store.init_db(db)                    # 切换目标库(单例连接)
    point_by_idx = {i: p for i, p in enumerate(points)}
    ex_by_idx = {i: e for i, e in enumerate(exercises)}
    for _, pj, ei in hits:
        pid = point_by_idx[pj][0]                 # knowledge_points.id
        ex = ex_by_idx[ei]                        # (id, question, answer)
        kb_store.set_flags(pid, is_exam_hit=1)
        kb_store.add_reverse_index(pid, ex[1], ex[2] if len(ex) > 2 else None)
    return {"hit": len(hits), "reverse": len(hits)}


def clear_matches(db):
    """幂等重跑:清空 reverse_index + 复位所有 is_exam_hit。"""
    kb_store.init_db(db)
    kb_store.clear_reverse_index()
    conn = kb_store.get_conn()             # 单例连接,无参数
    conn.execute("UPDATE knowledge_points SET is_exam_hit = 0 WHERE is_exam_hit = 1")
    conn.commit()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _threshold():
    return EXERCISE_MATCH_THRESHOLD


def main(argv=None):
    parser = argparse.ArgumentParser(description="习题册真题↔复习资料知识点 匹配+同域校验+反向索引写库")
    parser.add_argument("--db", default=os.path.join(ROOT_DIR, "data", "mk.db"), help="数据库路径")
    parser.add_argument("--threshold", type=float, default=None, help="匹配阈值(默认 config 0.85)")
    parser.add_argument("--apply-exam-hits", action="store_true", help="写库:set is_exam_hit + reverse_index")
    parser.add_argument("--clear", action="store_true", help="幂等重跑:先清空 reverse_index + 复位 is_exam_hit")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库不调 embedding")
    args = parser.parse_args(argv)

    db = os.path.abspath(args.db)
    if not os.path.exists(db):
        print(f"数据库不存在:{db}")
        return 1

    kb_store.init_db(db)
    conn = kb_store.get_conn()

    # 读复习资料知识点(带 title) + 习题册真题
    points = conn.execute(
        "SELECT id, title, content FROM knowledge_points WHERE title IS NOT NULL ORDER BY id"
    ).fetchall()
    exercises = conn.execute(
        "SELECT id, question, answer FROM exercises ORDER BY id"
    ).fetchall()
    print(f"复习知识点(带title):{len(points)} 条 | 习题册真题:{len(exercises)} 条")

    th = args.threshold if args.threshold is not None else _threshold()
    print(f"匹配阈值:{th} | dry-run:{args.dry_run} | apply-exam-hits:{args.apply_exam_hits}")

    if args.clear:
        clear_matches(db)
        print("已清空 reverse_index + 复位 is_exam_hit(幂等)")

    if not args.dry_run:
        hits = match_exercises([p[1] for p in points], [q[1] for q in exercises], threshold=th)
        print(f"命中(校验后):{len(hits)} 条")
        for s, pj, ei in hits[:20]:
            print(f"  {s:.3f} | {points[pj][1][:24]}  <-  Q:{exercises[ei][1][:28]}")
        if not hits:
            print("(dry-run 用缓存的 embedding 或需先正式跑;无命中时请检查阈值/数据)")
        if args.apply_exam_hits and hits:
            stat = apply_matches(db, hits, points, exercises)
            print(f"写库完成:is_exam_hit {stat['hit']} 条,reverse_index {stat['reverse']} 条")
    else:
        print("dry-run 模式:不做 embedding 不写库(仅展示数据规模)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
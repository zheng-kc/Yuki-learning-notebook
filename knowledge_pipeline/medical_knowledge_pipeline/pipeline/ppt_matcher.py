# -*- coding: utf-8 -*-
"""PPT 知识点 ↔ 复习资料知识点 匹配 + 同域校验 + 反向索引写库。

把「PPT 作为权威信号(is_teacher)判重点」的关联固化,补充 PPT 流程缺失的
「反向索引正式落库」一环(见 PPT_pipeline 笔记待办第 2 条)。

与 exercises_matcher.py(习题册)同构,差异在**匹配对象**:
    exercises_matcher.py      : 习题册真题 question ↔ 复习资料 title   (权威源=习题册)
    ppt_matcher.py  : PPT 考点 title        ↔ 复习资料 title   (权威源=PPT)

核心设计决策(策划笔记 _meta/ppt_matcher-模块方案.md,已批准方向):
    1. 匹配源 = knowledge_points 表里 is_teacher=1(PPT 来源)的 title
    2. 匹配目标 = 同表非 PPT(复习资料)的 title
    3. 反向索引方案 X:point_id = 复习知识点, exam_question = PPT 考点 title
        含义「这个复习知识点被老师(PPT)学习贯彻过」,权重 is_teacher 的证据。
    4. 复用 exercises_matcher.py 的通用骨架:embed_texts / check_same_domain /
       apply_matches 的写库逻辑(import,不复制,避免双份维护,不动 exercises_matcher.py)。
    5. PPT 源判定:source_file 含 PPT 目录/文件名标记 或 is_teacher=1。

PPTM: PPT 与复习学科不一致时(如牙周 PPT vs 外科复习)命中≈0 是正确防御
     而非 bug——模块学科无关,换同学科 PPT 即得真实命中。

嵌入 key 从 knowledge_pipeline/.env 读 DASHSCOPE_API_KEY(dashscope),与 matcher 一致。
阈值 PPT_MATCH_THRESHOLD 在 config.py 集中管理。
"""

import argparse
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

# --------------------------------------------------------------------------- #
# 路径与配置
# --------------------------------------------------------------------------- #
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
PIPELINE_ROOT = os.path.dirname(ROOT_DIR)


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 加载 exercises_matcher.py(复用其 embed_texts/check_same_domain/apply_matches/加载逻辑)
matcher = _load_module("matcher", os.path.join(SCRIPT_DIR, "exercises_matcher.py"))
# matcher 内部已加载 kb_store / config / DISEASE_TYPE_WORDS / EXERCISE_MATCH_THRESHOLD
kb_store = matcher.kb_store
_config_mod = matcher._config_mod


def _config(name, default):
    return getattr(_config_mod, name, default)


PPT_MATCH_THRESHOLD = _config("PPT_MATCH_THRESHOLD", 0.85)
# PPT 来源判定标记:source_file 含这些子串 → 视为 PPT 来源(is_teacher 候选)
PPT_SOURCE_MARKERS = _config("PPT_SOURCE_MARKERS", ["PPT", "ppt"])


def _is_ppt_point(row):
    """判定行是否为 PPT 来源的知识点。row 为 (id, title, content, source_file, is_teacher)。"""
    if row[4]:                                    # is_teacher 已置位 → 判定为 PPT
        return True
    sf = row[3] or ""
    return any(m in sf for m in PPT_SOURCE_MARKERS)


def load_sources(db, threshold_required=True):
    """从数据库读 PPT 源知识点 + 复习目标知识点。

    返回 (ppt_points, rev_points):
        ppt_points: [(id, title)]  PPT 来源(is_teacher=1 或 source_file 含 PPT 标记)
        rev_points: [(id, title)]  复习来源(非 PPT、带 title)
    """
    kb_store.init_db(db)
    conn = kb_store.get_conn()
    rows = conn.execute(
        "SELECT id, title, content, source_file, is_teacher FROM knowledge_points ORDER BY id"
    ).fetchall()
    ppt = [(r[0], r[1]) for r in rows if r[1] and _is_ppt_point(r)]
    rev = [(r[0], r[1]) for r in rows if r[1] and not _is_ppt_point(r)]
    return ppt, rev


def _matcher_hits(ppt_points, rev_points, threshold):
    """复用 matcher.match_exercises:把「PPT title ↔ 复习 title」当「题↔知识点」匹配。"""
    if not ppt_points or not rev_points:
        return []
    ppt_titles = [t for _, t in ppt_points]
    rev_titles = [t for _, t in rev_points]
    # match_exercises(point_titles=复习, exercise_questions=PPT):每个PPT取最像的复习点
    # 返回 [(score, 复习idx, PPTidx)]——与 apply 期望的 (p_idx, r_idx) 相反,需翻转
    raw = matcher.match_exercises(rev_titles, ppt_titles, threshold=threshold)
    return [(s, ppi, rpi) for s, rpi, ppi in raw]


def apply_ppt_matches(db, hits, ppt_points, rev_points):
    """写库:对每个命中,在复习知识点上打 reverse_index(point_id=复习点, exam_question=PPT考点)。

    不做 is_exam_hit(PPT 已是 is_teacher=1);反向索引方案 X。
    hits: [(score, p_idx, r_idx), ...](p=PPT源, r=复习目标)。
    返回 {"reverse": n}。
    """
    kb_store.init_db(db)
    for _, pi, ri in hits:
        pid = rev_points[ri][0]                 # 复习知识点 id(反向索引挂这)
        ppt_title = ppt_points[pi][1]
        kb_store.add_reverse_index(pid, f"[PPT] {ppt_title}")
    return {"reverse": len(hits)}


def clear_ppt_matches(db):
    """幂等重跑:清空标记为 [PPT] 的反向索引。"""
    kb_store.init_db(db)
    conn = kb_store.get_conn()
    conn.execute("DELETE FROM reverse_index WHERE exam_question LIKE '[PPT] %'")
    conn.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description="PPT 知识点 ↔ 复习资料知识点 匹配+同域校验+反向索引写库")
    parser.add_argument("--db", default=os.path.join(ROOT_DIR, "data", "mk.db"), help="数据库路径")
    parser.add_argument("--threshold", type=float, default=None, help="匹配阈值(默认 config PPT_MATCH_THRESHOLD)")
    parser.add_argument("--apply", action="store_true", help="写库:写 [PPT] reverse_index")
    parser.add_argument("--clear", action="store_true", help="幂等重跑:先清 [PPT] reverse_index")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库不调 embedding(仅列数据规模)")
    args = parser.parse_args(argv)

    db = os.path.abspath(args.db)
    if not os.path.exists(db):
        print(f"数据库不存在:{db}")
        return 1

    th = args.threshold if args.threshold is not None else PPT_MATCH_THRESHOLD
    print(f"数据库:{db}")
    print(f"匹配阈值:{th} | dry-run:{args.dry_run} | apply:{args.apply} | clear:{args.clear}")

    ppt_points, rev_points = load_sources(db)
    print(f"PPT 源知识点:{len(ppt_points)} 条 | 复习目标知识点:{len(rev_points)} 条")

    if args.clear:
        clear_ppt_matches(db)
        print("已清空 [PPT] 反向索引(幂等)")

    if args.dry_run:
        print("dry-run 模式:不做 embedding 不写库(仅展示数据规模)。")
        return 0

    if not ppt_points or not rev_points:
        print("⚠️ PPT 源或复习目标为空:自动补 PPT 源时请确保库中含 is_teacher=1 的 PPT 点。")
        return 0

    hits = _matcher_hits(ppt_points, rev_points, th)
    print(f"命中(校验后):{len(hits)} 条")
    for s, pi, ri in hits[:20]:
        print(f"  {s:.3f} | PPT:{ppt_points[pi][1][:22]}  ↔  复习:{rev_points[ri][1][:22]}")
    if args.apply and hits:
        stat = apply_ppt_matches(db, hits, ppt_points, rev_points)
        print(f"写库完成:reverse_index(方案X) {stat['reverse']} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
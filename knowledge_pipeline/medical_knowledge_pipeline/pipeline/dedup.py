# -*- coding: utf-8 -*-
"""相似度去重 + 重复度计数模块(③)。

职责:
    对库内全部知识点做相似度去重归并,为后续 weight.py 提供"重复度"信号:
    - 读取库内所有知识点(id/content/source_file/dup_count...)
    - 文本轻度归一化后两两比较相似度(difflib.SequenceMatcher)
    - 相似度 >= 阈值 → 判定为同一知识点 → 归并:
        · 保留 content 更长/信息更全的一条为主条目
        · 主条目 dup_count += 1(每归并一条),被归并条目的来源挂到主条目
        · 被归并条目从库中删除(来源已迁移)
    - 输出统计:原始条数 / 归并后条数 / 去重率 / 重复度最高 Top N

两层去重:
    第一层(默认)SequenceMatcher 字符相似度,快、零误判,合并字面几乎相同的;
    第二层(use_embedding=True)Qwen embedding 余弦聚类,补算法漏掉的
    "说法不同、实为同考点"的条目。语义层的 dup_count 采用方案X:
    dup_count = 该知识点来源去重后的 source_file 数(跨了几份材料),
    由 kb_store.set_dup 覆盖写入(而非 incr_dup 按归并条数累加)。

与数据层的关系:
    仅通过 kb_store 公开接口操作数据库(kb/kb_store.py),不直接写 DML SQL。
    去重写入全程处在一个 SQLite 事务中:失败整体回滚。
    通过把 kb_store 的模块级连接临时替换为"提交拦截包装",使 kb_store
    内部每次操作后的 commit() 不生效,最后由 dedup 统一 commit/rollback。

用法:
    python dedup.py --db data/mk.db --threshold 0.85 --dry-run   # 预演,不写库
    python dedup.py --db data/mk.db --threshold 0.85             # 正式去重
    python dedup.py --db data/mk.db --embedding --dry-run        # 语义层预演
    python dedup.py --db data/mk.db --embedding                  # 语义层正式去重
    python dedup.py --db data/mk.db --report --top 15            # 只读重复度报表
"""

import argparse
import difflib
import importlib.util
import json
import os
import sys

try:
    from openai import OpenAI
except ImportError:  # 延迟到调用时报错,保证纯算法去重/离线测试可用
    OpenAI = None

# 默认相似度阈值:>= 该值判定为同一知识点
DEFAULT_THRESHOLD = 0.85
# dedup() 返回 top_dup 的默认条数
TOP_DUP_DEFAULT = 10

# --------------------------------------------------------------------------- #
# 语义 embedding 层配置(Qwen,OpenAI 兼容端点)
# --------------------------------------------------------------------------- #
EMBED_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBED_MODEL = "qwen3.7-text-embedding"
EMBED_BATCH_LIMIT = 20               # 阿里限制:单次 input 最多 20 条,超了报 400
DEFAULT_EMBED_THRESHOLD = 0.85       # 语义层余弦相似度阈值(判为同一知识点)

# 路径推断:SCRIPT_DIR = .../medical_knowledge_pipeline/pipeline
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)              # .../medical_knowledge_pipeline
PIPELINE_ROOT = os.path.dirname(ROOT_DIR)           # .../knowledge_pipeline
DEFAULT_ENV = os.path.join(PIPELINE_ROOT, ".env")   # knowledge_pipeline/.env
# 已算好的 embedding 向量缓存(qwen3.7-text-embedding,1024 维)
DEFAULT_EMB_CACHE = os.path.join(ROOT_DIR, "data", "emb_vecs.json")


# --------------------------------------------------------------------------- #
# 文本归一化与相似度
# --------------------------------------------------------------------------- #

def normalize(text):
    """文本轻度归一化:去空白、全角转半角、去标点(大小写折叠)。

    中文知识点常因换行、多余空格、中英文标点差异导致"同文不同串",
    先归一化再比较可抹平这类格式噪声。仅保留字母数字与汉字。

    例: normalize("甲状腺。切除(术)") == normalize("甲状腺切除 术")
    """
    if not text:
        return ""
    chars = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:          # 全角空格 → 半角空格
            ch = " "
        elif 0xFF01 <= code <= 0xFF5E:   # 全角 ASCII → 半角
            ch = chr(code - 0xFEE0)
        chars.append(ch)
    return "".join(c for c in "".join(chars).lower() if c.isalnum())


def similarity(a, b):
    """两段文本的相似度(0~1)。

    对归一化后的文本用 difflib.SequenceMatcher 计算 ratio:
    1.0 表示完全相同;>= 阈值(DEFAULT_THRESHOLD)视为同一知识点。
    内部自动归一化,调用方无需先 normalize。
    """
    return _ratio(normalize(a), normalize(b))


def _ratio(na, nb):
    """对已归一化字符串计算 SequenceMatcher ratio(autojunk=False 更稳)。"""
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()


def _max_possible_ratio(la, lb):
    """两段长度已知文本的相似度理论上限:短串被长串完全包含时的 ratio。"""
    if la == 0 and lb == 0:
        return 1.0
    lo, hi = (la, lb) if la < lb else (lb, la)
    return 2.0 * lo / (lo + hi)


# --------------------------------------------------------------------------- #
# kb_store 加载(kb/kb_store.py,支持退化为同目录 kb_store.py)
# --------------------------------------------------------------------------- #

def _load_kb_store():
    """按文件路径加载 kb_store 模块(importlib),避免依赖包内 import 路径。"""
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = (
        os.path.join(base, "kb", "kb_store.py"),
        os.path.join(base, "kb_store.py"),
    )
    for path in candidates:
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("kb_store", path)
            kb = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(kb)
            return kb
    raise FileNotFoundError("未找到 kb_store.py: " + " | ".join(candidates))


def _src_key(s):
    """来源映射的去重键:(source_file, source_kb)。"""
    return (s.get("source_file"), s.get("source_kb"))


# --------------------------------------------------------------------------- #
# 事务化写入:提交拦截包装
# --------------------------------------------------------------------------- #

class _CommitInterceptor:
    """sqlite3 连接的薄包装:一切属性委托给原连接,唯独把 commit() 变空操作。

    kb_store 的每个公开写接口末尾都自带一次 conn.commit();要让整个去重过程
    落在一个事务里,必须让这些中间 commit 不生效,最后统一提交或回滚。
    只替换 kb_store 模块级 _conn 期间生效,用完立即还原。
    """

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "deferred_commits", 0)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def commit(self):
        """拦截提交:只计数,不真正 commit,交由外层统一控制。"""
        object.__setattr__(self, "deferred_commits",
                           self.deferred_commits + 1)


def _apply_plan(kb, plan):
    """把归并方案写入数据库,全程单事务(失败回滚)。

    对每个簇:保留主条目,其余条目 → 来源迁移到主条目 + 重复度累加 + 删除。
    迁移/删除都通过 kb_store 接口完成;期间 kb_store 自带的 commit 被包装拦截,
    全部完成后一次性 commit,任何异常则 rollback。
    """
    real = kb.get_conn()
    kb._conn = _CommitInterceptor(real)     # 临时替换模块级连接
    try:
        for cluster in plan:
            main_id = cluster["main"]["id"]
            # 主条目已挂的来源(去重),避免重复登记同一条来源
            have = {_src_key(s) for s in cluster["main"].get("_sources", [])}
            for m in cluster["merged"]:
                # 1) 把被归并条目的来源挂到主条目
                for s in m.get("_sources", []):
                    key = _src_key(s)
                    if key not in have:
                        kb.add_source(main_id, key[0], key[1])
                        have.add(key)
                # 2) 主条目重复度 +1(被归并条目自身 dup_count 代表其重复出现次数)
                for _ in range(m.get("dup_count") or 1):
                    kb.incr_dup(main_id)
                # 3) 删除被归并条目(来源已迁移,级联删除无损失)
                kb.delete_point(m["id"])
        real.commit()
    except Exception:
        real.rollback()
        raise
    finally:
        kb._conn = real                     # 还原模块级连接


# --------------------------------------------------------------------------- #
# 归并方案(内存计算,不写库)
# --------------------------------------------------------------------------- #

def _build_merge_plan(rows, threshold):
    """在内存中计算去重归并方案,返回 (plan, empty_rows)。

    plan: list[dict],每项一个"知识点簇":
        {"main": 保留的主条目 dict, "merged": [被归并条目 dict, ...]}
    empty_rows: 归一化后为空文本的条目(无法比较,保持原样不参与归并)。

    算法(两步):
        1) 精确去重:归一化文本完全相同的条目先按 dict 分组(文本=键),
           每组只需挑出一个代表参与后续模糊比较,跳过重复的 O(n²) 比较。
        2) 模糊聚类:代表按 content 长度降序依次处理,与已建成各簇的
           "最长代表" 两两比相似度,选最相似且 >= 阈值的簇并入;
           否则自成一簇。簇代表即簇内最长条目,满足"保留更长条目为主"。
    """
    # 1) 按归一化文本分组(精确相同串)
    norm_groups = {}
    empty_rows = []
    for r in rows:
        norm = normalize(r.get("content") or "")
        r["_norm"] = norm
        if norm:
            norm_groups.setdefault(norm, []).append(r)
        else:
            empty_rows.append(r)

    # 每组选一个幸存代表:content 最长,同长取 id 最小(先入库者)
    survivors = []
    for grp in norm_groups.values():
        rep = max(grp, key=lambda x: (len(x["content"]), -x["id"]))
        survivors.append(rep)

    # 2) 按长度降序做 leader 聚类:长文本优先成为簇代表
    survivors.sort(key=lambda x: len(x["content"]), reverse=True)
    clusters = []                          # 每个: {"rep": 簇代表, "norms": [norm]}
    for surv in survivors:
        sn = surv["_norm"]
        best_cluster, best_sim = None, -1.0
        for c in clusters:
            rn = c["rep"]["_norm"]
            # 长度理论上限不足阈值时直接跳过,省掉 SequenceMatcher 开销
            if _max_possible_ratio(len(sn), len(rn)) < threshold:
                continue
            sim = _ratio(sn, rn)
            if sim > best_sim:
                best_sim, best_cluster = sim, c
        if best_cluster is not None and best_sim >= threshold:
            best_cluster["norms"].append(sn)
        else:
            clusters.append({"rep": surv, "norms": [sn]})

    # 汇总成 plan:簇内全部条目(含同文本重复)挑主条目,其余待归并
    plan = []
    for c in clusters:
        all_rows = []
        for nk in c["norms"]:
            all_rows.extend(norm_groups[nk])
        main = max(all_rows, key=lambda x: (len(x["content"]), -x["id"]))
        merged = [x for x in all_rows if x["id"] != main["id"]]
        if merged:
            plan.append({"main": main, "merged": merged})
    return plan, empty_rows


def _snippet(content, limit=30):
    """取 content 前 limit 字用于展示(空白折叠)。"""
    if not content:
        return ""
    text = " ".join(content.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _pair_record(cluster):
    """把归并簇转成可读的记录(供 merged_pairs / 输出)。"""
    main = cluster["main"]
    merged = [{
        "id": m["id"],
        "content": _snippet(m["content"]),
        "source_file": m.get("source_file"),
        "dup_count": m.get("dup_count") or 1,
    } for m in cluster["merged"]]
    return {
        "main_id": main["id"],
        "main_content": _snippet(main["content"]),
        "main_source_file": main.get("source_file"),
        "cluster_size": len(merged) + 1,
        "dup_count": cluster.get("dup_count"),
        "merged": merged,
    }


def _compute_top_dup(rows, plan, top_n=TOP_DUP_DEFAULT):
    """依据归并方案推算归并后的 Top-N 重复条目(不依赖写库结果)。

    rows 须含 "_sources"(来源映射)。返回按 dup_count 降序的列表,
    每项含 id / content 前 30 字 / dup_count / source_count(去重来源数)。
    """
    rows_by_id = {r["id"]: r for r in rows}
    merged_ids = set()
    new_dup = {}        # 主条目 id -> 归并后 dup_count
    new_srcs = {}       # 主条目 id -> 归并后来源键集合
    for c in plan:
        main = c["main"]
        dup = main.get("dup_count") or 1
        srcs = {_src_key(s) for s in main.get("_sources", [])}
        for m in c["merged"]:
            merged_ids.add(m["id"])
            dup += m.get("dup_count") or 1
            for s in m.get("_sources", []):
                srcs.add(_src_key(s))
        new_dup[main["id"]] = dup
        new_srcs[main["id"]] = srcs

    retained = []
    for r in rows:
        rid = r["id"]
        if rid in merged_ids:
            continue
        if rid in new_dup:
            dup, nsrc = new_dup[rid], len(new_srcs[rid])
        else:
            dup = r.get("dup_count") or 1
            nsrc = len({_src_key(s) for s in r.get("_sources", [])})
        retained.append({
            "id": rid,
            "content": _snippet(r["content"]),
            "dup_count": dup,
            "source_count": nsrc,
        })
    retained.sort(key=lambda x: (-x["dup_count"], x["id"]))
    return retained[:top_n]


# --------------------------------------------------------------------------- #
# 语义 embedding 层(Qwen 向量 + 余弦聚类 + 方案X dup_count)
# --------------------------------------------------------------------------- #

def _load_env(env_path=DEFAULT_ENV):
    """加载 .env 到环境变量(不覆盖已存在的同名变量;KEY=VALUE,# 注释跳过)。"""
    if not env_path or not os.path.isfile(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


def _api_key():
    """获取 DashScope API Key(knowledge_pipeline/.env 的 DASHSCOPE_API_KEY)。"""
    _load_env()
    return os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def embed_texts(texts, api_key=None, model=EMBED_MODEL, base_url=EMBED_BASE_URL,
                batch_size=EMBED_BATCH_LIMIT):
    """调用 Qwen embedding 端点,把文本列表转成向量列表(与入参顺序一致)。

    阿里限制单次 input 最多 20 条,内部按 batch_size 分批请求;
    base_url 为 OpenAI 兼容端点,key 取自 .env 的 DASHSCOPE_API_KEY。返回 1024 维向量。
    """
    if OpenAI is None:
        raise RuntimeError("未安装 openai 包,无法调用 embedding 接口")
    key = api_key or _api_key()
    if not key:
        raise RuntimeError(
            "未设置 DASHSCOPE_API_KEY(knowledge_pipeline/.env),无法调用 embedding 接口")
    if not 1 <= batch_size <= EMBED_BATCH_LIMIT:
        raise ValueError("batch_size 需在 1~%d 之间(阿里单次上限)" % EMBED_BATCH_LIMIT)
    client = OpenAI(base_url=base_url, api_key=key)
    vecs = []
    for start in range(0, len(texts), batch_size):
        chunk = texts[start:start + batch_size]
        resp = client.embeddings.create(model=model, input=chunk)
        # 按 index 回排,防止服务端乱序
        data = sorted(resp.data, key=lambda d: d.index)
        vecs.extend(list(d.embedding) for d in data)
    return vecs


def load_embedding_cache(cache_path=None):
    """读取已算好的向量缓存(支持 {"vecs":[...]} 或裸 list)。

    文件不存在/损坏/结构不符时返回 []。返回:list[list[float]]。
    """
    path = cache_path or DEFAULT_EMB_CACHE
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get("vecs")
    if isinstance(data, list) and data and isinstance(data[0], list):
        return data
    return []


def _resolve_vectors(rows, cache_path=None):
    """取行对应的向量:优先用与行数一致的缓存,否则调接口现算。

    缓存顺序须与 rows 顺序一致(get_points 按 id 升序);条数不符则忽略缓存。
    """
    cached = load_embedding_cache(cache_path)
    if cached and len(cached) == len(rows):
        return cached
    if cached:
        print("提示:向量缓存条数(%d)与库内条数(%d)不一致,改为现算 embedding。"
              % (len(cached), len(rows)))
    return embed_texts([r.get("content") or "" for r in rows])


def _cluster_by_cosine(vecs, threshold):
    """按余弦相似度 >= threshold 用并查集聚类,返回簇(每组为行下标 list)。"""
    n = len(vecs)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]     # 路径减半
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    try:
        import numpy as np
        m = np.asarray(vecs, dtype=np.float32)
        norm = np.linalg.norm(m, axis=1, keepdims=True)
        norm[norm == 0] = 1.0                 # 零向量兜底,避免除零
        m = m / norm
        sim = m @ m.T
        for i in range(n):
            row = sim[i]
            for j in range(i + 1, n):
                if row[j] >= threshold:
                    union(i, j)
    except ImportError:                       # 无 numpy 时纯 Python 回退
        units = [_unit_vector(v) for v in vecs]
        for i in range(n):
            for j in range(i + 1, n):
                if _dot(units[i], units[j]) >= threshold:
                    union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _unit_vector(v):
    """返回 v 的单位向量(零向量原样返回)。"""
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v] if norm else list(v)


def _dot(a, b):
    """两向量点积。"""
    return sum(x * y for x, y in zip(a, b))


def _build_semantic_plan(rows, vecs, threshold):
    """语义聚类 → 归并方案(方案X:dup_count = 去重后的 source_file 数)。

    每个簇:content 最长者为保留主条目,其余归并;簇内所有条目(含主条目)的
    sources 合并去重后,不同的 source_file 个数即该簇的 dup_count ——语义是
    "该知识点跨了几份材料",而非"被归并了几条"。返回 list[dict]:
        {"main": 主条目, "merged": [被归并条目...], "dup_count": int}
    """
    plan = []
    for group in _cluster_by_cosine(vecs, threshold):
        members = [rows[i] for i in group]
        main = max(members, key=lambda x: (len(x.get("content") or ""), -x["id"]))
        merged = [x for x in members if x["id"] != main["id"]]
        files = set()
        for x in members:
            for s in x.get("_sources", []):
                files.add(s.get("source_file"))
        files.discard(None)
        plan.append({
            "main": main,
            "merged": merged,
            "dup_count": len(files) or 1,
        })
    return plan


def _compute_semantic_top_dup(rows, plan, top_n=TOP_DUP_DEFAULT):
    """按语义方案推算归并后 Top-N 重复条目(dup_count 取方案X 的跨材料数)。"""
    merged_ids = set()
    meta = {}                              # 主条目 id -> (dup_count, 来源文件数)
    for c in plan:
        srcs = {s.get("source_file") for s in c["main"].get("_sources", [])}
        for m in c["merged"]:
            merged_ids.add(m["id"])
            for s in m.get("_sources", []):
                srcs.add(s.get("source_file"))
        srcs.discard(None)
        meta[c["main"]["id"]] = (c["dup_count"], len(srcs))

    retained = []
    for r in rows:
        rid = r["id"]
        if rid in merged_ids:
            continue
        if rid in meta:
            dup, nsrc = meta[rid]
        else:
            dup = r.get("dup_count") or 1
            nsrc = len({s.get("source_file") for s in r.get("_sources", [])} - {None})
        retained.append({
            "id": rid,
            "content": _snippet(r["content"]),
            "dup_count": dup,
            "source_count": nsrc,
        })
    retained.sort(key=lambda x: (-x["dup_count"], x["id"]))
    return retained[:top_n]


def _apply_semantic_plan(kb, plan):
    """把语义归并方案写入库,全程单事务(失败回滚)。

    与 _apply_plan 的差异:dup_count 用 set_dup **覆盖**为"去重来源文件数"
    (方案X),而非按被归并条数 incr_dup 累加。主条目 id 不动,被归并条目
    来源迁移后删除。
    """
    real = kb.get_conn()
    kb._conn = _CommitInterceptor(real)     # 临时替换模块级连接
    try:
        for cluster in plan:
            main_id = cluster["main"]["id"]
            have = {_src_key(s) for s in cluster["main"].get("_sources", [])}
            for m in cluster["merged"]:
                for s in m.get("_sources", []):
                    key = _src_key(s)
                    if key not in have:
                        kb.add_source(main_id, key[0], key[1])
                        have.add(key)
                kb.delete_point(m["id"])
            # 方案X:主条目重复度 = 去重来源文件数(直接覆盖,非累加)
            kb.set_dup(main_id, cluster["dup_count"])
        real.commit()
    except Exception:
        real.rollback()
        raise
    finally:
        kb._conn = real                     # 还原模块级连接


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #

def dedup(db_path=None, threshold=DEFAULT_THRESHOLD, dry_run=False,
          use_embedding=False, semantic_threshold=DEFAULT_EMBED_THRESHOLD,
          embedding_cache=None):
    """主入口:对库内全部知识点做相似度去重归并。

    参数:
        db_path: SQLite 库文件路径;None 使用 kb_store 默认路径(knowledge.db)
        threshold: 算法层相似度阈值(0~1),>= 该值判定为同一知识点
        dry_run: True 只计算并返回统计,不写库;False 真正执行归并
        use_embedding: True 走语义 embedding 层(Qwen 余弦聚类 + 方案X dup_count);
                       False 保持原有 SequenceMatcher 算法层
        semantic_threshold: 语义层余弦阈值(use_embedding=True 时生效)
        embedding_cache: 向量缓存路径;None 用 data/emb_vecs.json;条数不符则现算

    返回 dict:
        before        原始条数
        after         归并后预计/实际条数
        merged_pairs  归并簇列表(主条目 + 被归并条目)
        top_dup       归并后重复度最高的 Top 条目(供快速浏览)
        method        'algorithm' | 'embedding'
    """
    kb = _load_kb_store()
    kb.init_db(db_path)
    rows = kb.get_points()
    before = len(rows)

    # 预读每个点的来源映射(get_sources),供迁移与统计使用
    for r in rows:
        r["_sources"] = kb.get_sources(r["id"])

    if use_embedding:
        vecs = _resolve_vectors(rows, cache_path=embedding_cache)
        plan = _build_semantic_plan(rows, vecs, semantic_threshold)
        merged_total = sum(len(c["merged"]) for c in plan)
        after = before - merged_total
        merged_pairs = [_pair_record(c) for c in plan if c["merged"]]
        top_dup = _compute_semantic_top_dup(rows, plan, top_n=TOP_DUP_DEFAULT)
        if not dry_run and plan:
            _apply_semantic_plan(kb, plan)     # 单事务写入,失败回滚
        return {
            "before": before,
            "after": after,
            "merged_pairs": merged_pairs,
            "top_dup": top_dup,
            "method": "embedding",
            "plan": plan,
        }

    plan, empty_rows = _build_merge_plan(rows, threshold)
    merged_total = sum(len(c["merged"]) for c in plan)
    after = before - merged_total

    merged_pairs = [_pair_record(c) for c in plan]
    top_dup = _compute_top_dup(rows, plan, top_n=TOP_DUP_DEFAULT)

    if not dry_run and merged_total:
        _apply_plan(kb, plan)      # 单事务写入,失败回滚

    return {
        "before": before,
        "after": after,
        "merged_pairs": merged_pairs,
        "top_dup": top_dup,
        "method": "algorithm",
    }


def report(db_path=None, top_n=10):
    """只读统计:输出库内知识点总数、dup_count 分布与重复度 Top-N 报表。

    report 不修改数据库,可在去重前预览,也可在去重后核对结果。
    """
    kb = _load_kb_store()
    kb.init_db(db_path)
    rows = kb.get_points()
    total = len(rows)
    dup_dist = {}
    for r in rows:
        dup_dist[r.get("dup_count") or 1] = dup_dist.get(r.get("dup_count") or 1, 0) + 1

    print("=" * 60)
    print(f"知识点总数: {total}")
    print("-" * 60)
    print("dup_count 分布:")
    for dc in sorted(dup_dist):
        print(f"  重复度 {dc:<4d}: {dup_dist[dc]:<6d} 条")
    print("-" * 60)
    top = sorted(rows, key=lambda r: (-(r.get("dup_count") or 1), r["id"]))[:top_n]
    print(f"重复度最高 Top {len(top)}:")
    for r in top:
        nsrc = len(kb.get_sources(r["id"]))
        print(f"  [dup={r.get('dup_count') or 1}] [来源x{nsrc}] "
              f"(id={r['id']}) {_snippet(r['content'])}")
    print("=" * 60)
    return {
        "total": total,
        "dup_count_dist": dup_dist,
        "top": top,
    }


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #

def _print_result(res):
    before = res["before"]
    after = res["after"]
    merged = before - after
    rate = (merged / before * 100) if before else 0.0
    print("=" * 60)
    print(f"去重前条数: {before}")
    print(f"去重后条数: {after}")
    print(f"归并条目数: {merged}  去重率: {rate:.2f}%")
    print("=" * 60)

    pairs = res["merged_pairs"]
    print(f"归并簇 {len(pairs)} 组(每组保留主条目,其余并入):")
    for i, p in enumerate(pairs, 1):
        merges = ",".join(str(m["id"]) for m in p["merged"])
        dup = p.get("dup_count")
        dup_tag = f" dup={dup}" if dup is not None else ""
        print(f"  [{i:>3}] 主#{p['main_id']}({p['main_source_file']}){dup_tag} "
              f"「{p['main_content']}」"
              f"  <- {len(p['merged'])} 条: #{merges}")
    print("-" * 60)

    print("归并后重复度 Top 10:")
    for t in res["top_dup"]:
        print(f"  [dup={t['dup_count']:<2d}] [来源x{t['source_count']:<2d}] "
              f"(id={t['id']}) {t['content']}")
    print("=" * 60)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="知识点相似度去重 + 重复度计数(基于 kb_store,支持 dry-run)")
    parser.add_argument("--db", default=None,
                        help="SQLite 库文件路径(默认 kb_store 默认库 data/knowledge.db)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"相似度阈值 0~1,默认 {DEFAULT_THRESHOLD}")
    parser.add_argument("--dry-run", action="store_true",
                        help="只统计预演,不写库(默认建议先跑一次看效果)")
    parser.add_argument("--embedding", action="store_true",
                        help="启用 Qwen 语义 embedding 层(余弦聚类 + 方案X dup_count)")
    parser.add_argument("--emb-threshold", type=float, default=DEFAULT_EMBED_THRESHOLD,
                        help=f"语义层余弦阈值 0~1,默认 {DEFAULT_EMBED_THRESHOLD}")
    parser.add_argument("--emb-cache", default=None,
                        help="向量缓存 JSON 路径(默认 data/emb_vecs.json)")
    parser.add_argument("--report", action="store_true",
                        help="只读重复度报表,不做任何归并")
    parser.add_argument("--top", type=int, default=10,
                        help="report/top_dup 展示条数,默认 10")
    args = parser.parse_args(argv)

    # 防呆:db 显式给出但不存在时直接报错,避免 init_db 静默新建空库
    if args.db and not os.path.isfile(args.db):
        print(f"错误:数据库文件不存在: {args.db}")
        return 1
    if not 0.0 < args.threshold <= 1.0:
        print("错误:--threshold 需在 (0, 1] 区间")
        return 1
    if not 0.0 < args.emb_threshold <= 1.0:
        print("错误:--emb-threshold 需在 (0, 1] 区间")
        return 1

    if args.report:
        report(db_path=args.db, top_n=args.top)
        return 0

    res = dedup(db_path=args.db, threshold=args.threshold, dry_run=args.dry_run,
                use_embedding=args.embedding, semantic_threshold=args.emb_threshold,
                embedding_cache=args.emb_cache)
    _print_result(res)
    if args.dry_run:
        print("提示:以上为 dry-run 预演结果,未写入数据库;确认无误后去掉 "
              "--dry-run 正式执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

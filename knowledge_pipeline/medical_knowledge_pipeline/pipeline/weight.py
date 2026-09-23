# -*- coding: utf-8 -*-
"""权重合成 + 阈值分流模块(④)。

职责:
    综合四个信号计算每个知识点的权重,并按阈值分流到重点/零碎知识库。
    位置在 dedup 之后:dup_count 已由 dedup 语义层按方案X 填为"跨材料数"。

信号定义:
    dup_count    重复度(跨材料出现次数)→ 归一化后乘 W_DUP
    is_exam_hit  习题命中(习题册考点反向标记)→ W_EXAM
    is_teacher   课堂重点(来源为 PPT)→ W_TEACHER
    is_big_q     大题命中(来源为复习资料)→ W_BIG_Q

定稿公式(系数见 config.py,勿在此处硬编码):
    weight = W_DUP × norm(dup_count) + W_EXAM × is_exam_hit
           + W_TEACHER × is_teacher + W_BIG_Q × is_big_q
    默认 W_DUP=0.4 / W_EXAM=0.2 / W_TEACHER=0.15 / W_BIG_Q=0.25(和=1.0)
    分流:weight >= WEIGHT_THRESHOLD(默认 0.6)→ 重点;否则留零碎

接口:
    normalize_dup(count, mode=None, norm_max=None) -> float
        重复度归一化,保证返回 0~1;'cap' 封顶法 / 'max' 全局最大值法
    compute_weight(point, weights=None, mode=None, norm_max=None) -> float
        单知识点权重合成(纯函数,不碰库)
    distribute(kb, threshold=None, dry_run=False, top_n=None) -> dict
        遍历知识点,写回 weight,weight >= 阈值 → 移入重点库
    apply_exam_hits(kb, exercises=None, dry_run=False) -> int
        习题库反向标记:习题 points 命中的知识点置 is_exam_hit=1,返回命中条数
    main(argv=None)  # CLI

与数据层的关系:
    仅通过 kb_store 公开接口操作数据库(kb/kb_store.py),不直接写 DML SQL。
    写库全程包在一个 SQLite 事务里(提交拦截包装,同 dedup._CommitInterceptor):
    中途任一异常整体回滚。dry_run=True 时完全不调用写接口。

用法:
    python pipeline/weight.py --db data/mk.db --dry-run         # 预演,不写库
    python pipeline/weight.py --db data/mk.db                   # 正式分流
    python pipeline/weight.py --db data/mk.db --apply-exam-hits # 先习题标记再分流
    python pipeline/weight.py --db data/mk.db --threshold 0.5 --top 15
"""

import argparse
import importlib.util
import json
import os
import sys

# --------------------------------------------------------------------------- #
# 模块加载:config.py(同目录)与 kb_store.py(kb/ 子目录,退化为同目录)
# --------------------------------------------------------------------------- #

_CFG = None
_KB = None


def _load_config():
    """按文件路径加载同目录 config.py(importlib),缓存复用。"""
    global _CFG
    if _CFG is not None:
        return _CFG
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = (
        os.path.join(base, "config.py"),
        os.path.join(os.path.dirname(base), "config.py"),
    )
    for path in candidates:
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("weight_config", path)
            cfg = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cfg)
            _CFG = cfg
            return cfg
    raise FileNotFoundError("未找到 config.py: " + " | ".join(candidates))


def _load_kb_store():
    """按文件路径加载 kb_store 模块(importlib),避免依赖包内 import 路径。

    与 dedup._load_kb_store 同一方式,含退化为同目录 kb_store.py 的分支。
    """
    global _KB
    if _KB is not None:
        return _KB
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
            _KB = kb
            return kb
    raise FileNotFoundError("未找到 kb_store.py: " + " | ".join(candidates))


def _reset_module_cache():
    """清空已加载的 config / kb_store 缓存(供测试在多库间切换时使用)。"""
    global _CFG, _KB
    _CFG = None
    _KB = None


# --------------------------------------------------------------------------- #
# 文本归一化与数值工具
# --------------------------------------------------------------------------- #

def normalize_text(text):
    """文本轻度归一化:去空白、全角转半角、只留字母数字与汉字(小写折叠)。

    供 apply_exam_hits 的子串包含匹配使用;思路同 dedup.normalize,此处独立实现
    以免 weight 依赖 dedup 模块。
    例: normalize_text("甲状腺。切除(术)") == normalize_text("甲状腺切除 术")
    """
    if not text:
        return ""
    chars = []
    for ch in str(text):
        code = ord(ch)
        if code == 0x3000:                 # 全角空格 → 半角空格
            ch = " "
        elif 0xFF01 <= code <= 0xFF5E:      # 全角 ASCII → 半角
            ch = chr(code - 0xFEE0)
        chars.append(ch)
    return "".join(c for c in "".join(chars).lower() if c.isalnum())


def _clamp01(value):
    """把数值夹到 [0, 1] 区间(NaN 等非法值按 0 处理)。"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if value != value:                      # NaN
        return 0.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _flag(point, name):
    """读取 0/1 标记位:字段缺失/None 记 0,非零真值记 1。"""
    return 1 if point.get(name) else 0


def _snippet(content, limit=None):
    """取 content 前 limit 字用于展示(空白折叠);limit 缺省取 config。"""
    if limit is None:
        limit = _load_config().CONTENT_SNIP_LEN
    if not content:
        return ""
    text = " ".join(str(content).split())
    return text if len(text) <= limit else text[:limit] + "…"


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #

def normalize_dup(dup_count, mode=None, norm_max=None):
    """重复度归一化,返回 0~1 的 float。

    mode:
        'cap'(默认)封顶法: min(count, norm_max) / norm_max
                    norm_max 缺省取 config.DUP_NORM_MAX(默认 5)
        'max'        全局最大值法: count / norm_max,此时 norm_max 必须是
                    库内所有知识点 dup_count 的全局最大值
                    (由调用方用 global_max_dup() 算好传入)

    guard: count <= 0 直接返回 0;结果 clamp 到 [0, 1]。
    """
    cfg = _load_config()
    mode = mode or cfg.DUP_NORM_MODE
    try:
        count = float(dup_count) if dup_count is not None else 0.0
    except (TypeError, ValueError):
        count = 0.0
    if count <= 0:
        return 0.0

    if mode == "max":
        top = norm_max
        if not top or top <= 0:
            raise ValueError(
                "normalize_dup: mode='max' 需传入全局最大 dup_count(norm_max)")
        return _clamp01(count / float(top))

    # 'cap' 封顶法(未知 mode 亦按封顶处理,避免静默除零)
    cap = norm_max if norm_max else cfg.DUP_NORM_MAX
    if not cap or cap <= 0:
        return 0.0
    return _clamp01(min(count, float(cap)) / float(cap))


def global_max_dup(points):
    """库内所有知识点 dup_count 的最大值(至少 1,避免除零)。"""
    top = 0
    for p in points or []:
        try:
            value = int(p.get("dup_count") or 0)
        except (TypeError, ValueError):
            value = 0
        if value > top:
            top = value
    return top or 1


# --------------------------------------------------------------------------- #
# 权重合成
# --------------------------------------------------------------------------- #

def compute_weight(point, weights=None, mode=None, norm_max=None):
    """单知识点权重合成,返回 0~1 的 float(纯函数,不读写数据库)。

    参数:
        point: kb_store.get_points() 返回的一条 dict
               (需含 dup_count / is_exam_hit / is_teacher / is_big_q)
        weights: 系数 dict,键 W_DUP/W_EXAM/W_TEACHER/W_BIG_Q;
                 None 用 config.WEIGHT_COEFFS
        mode / norm_max: 重复度归一化方式(见 normalize_dup);
                         mode='max' 时 norm_max 须为全局最大 dup_count

    公式:
        W_DUP×norm(dup_count) + W_EXAM×is_exam_hit
        + W_TEACHER×is_teacher + W_BIG_Q×is_big_q
    返回值四舍五入到 6 位小数,消除浮点加法的尾数噪声(如 0.24000000000000002)。
    """
    cfg = _load_config()
    coeffs = weights or cfg.WEIGHT_COEFFS
    norm = normalize_dup(point.get("dup_count"), mode=mode, norm_max=norm_max)
    score = (
        float(coeffs.get("W_DUP", 0.0)) * norm
        + float(coeffs.get("W_EXAM", 0.0)) * _flag(point, "is_exam_hit")
        + float(coeffs.get("W_TEACHER", 0.0)) * _flag(point, "is_teacher")
        + float(coeffs.get("W_BIG_Q", 0.0)) * _flag(point, "is_big_q")
    )
    return round(_clamp01(score), 6)


def _step_decimals(step):
    """由步长推断区间标签需要的小数位(0.1 → 1 位,0.05 → 2 位)。

    避免 config.WEIGHT_HIST_STEP 改成 0.05 之类后标签重复("0.1-0.1"),
    导致不同区间的计数被 dict 合并、直方图悄悄丢数据。
    """
    for dec in range(0, 5):
        if abs(round(step, dec) - step) < 1e-9:
            return max(dec, 1)
    return 4


def _weight_hist(weights, step=None):
    """分数区间分布:按 step(默认 config.WEIGHT_HIST_STEP)分桶计数。

    返回有序 dict,如 {"0.0-0.1": 3, "0.1-0.2": 0, ...}(含计数为 0 的桶)。
    """
    cfg = _load_config()
    step = float(step or cfg.WEIGHT_HIST_STEP)
    if step <= 0:
        step = cfg.WEIGHT_HIST_STEP
    n = max(1, int(round(1.0 / step)))
    dec = _step_decimals(step)
    fmt = "%%.%df-%%.%df" % (dec, dec)
    keys = [fmt % (i * step, (i + 1) * step) for i in range(n)]
    hist = dict((k, 0) for k in keys)
    for w in weights:
        # round 到 6 位再取整:0.6/0.1=5.9999... 否则会被错分进 "0.5-0.6",
        # 与"0.6 已达阈值进重点"自相矛盾。区间按左闭右开 [lo, hi)。
        idx = int(round(w / step, 6))
        if idx >= n:
            idx = n - 1                      # 1.0 归入最后一桶
        if idx < 0:
            idx = 0
        hist[keys[idx]] += 1
    return hist


# --------------------------------------------------------------------------- #
# 事务化写入:提交拦截包装(同 dedup 的做法)
# --------------------------------------------------------------------------- #

class _CommitInterceptor:
    """sqlite3 连接的薄包装:属性全委托原连接,唯独 commit() 变空操作。

    kb_store 每个公开写接口末尾都自带一次 conn.commit();要让整批权重更新
    落在同一事务里,必须让这些中间 commit 不生效,最后由外层统一提交/回滚。
    只在写库期间替换 kb_store 模块级 _conn,用完立即还原。
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


class _single_transaction(object):
    """上下文管理器:把 with 块内的 kb_store 写操作合并成单个事务。

    正常退出统一 commit,异常 rollback 并原样抛出;退出时还原原连接。
    """

    def __init__(self, kb):
        self.kb = kb
        self.real = None

    def __enter__(self):
        self.real = self.kb.get_conn()
        self.kb._conn = _CommitInterceptor(self.real)
        return self.real

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                self.real.rollback()
            else:
                self.real.commit()
        finally:
            self.kb._conn = self.real            # 还原模块级连接
        return False                             # 异常继续向外抛


# --------------------------------------------------------------------------- #
# 分流
# --------------------------------------------------------------------------- #

def distribute(kb, threshold=None, dry_run=False, top_n=None):
    """遍历库内全部知识点,算权重 → 写回 weight → 按阈值分流到重点/零碎。

    参数:
        kb: 已 init_db 的 kb_store 模块(由调用方加载/初始化)
        threshold: 分流阈值,None 用 config.WEIGHT_THRESHOLD(默认 0.6)
        dry_run: True 只统计不写库(不 update_weight、不 set_kb_type)
        top_n: 返回 top 里最高分的条数,None 用 config.DEFAULT_TOP

    只调用 kb_store 公开接口(update_weight / set_kb_type),不写裸 SQL;
    写库整体包在单事务里。三个 flag(is_exam_hit/is_teacher/is_big_q)本函数
    一律不动,需要习题命中请先跑 apply_exam_hits。

    返回 dict:
        total        知识点总数
        moved        进重点的条目 [{id, weight, chapter, content_snip}, ...](权重降序)
        moved_count  进重点条数
        kept_count   留零碎条数
        moved_ratio  重点占比(0~1)
        weight_hist  分数区间分布 {"0.0-0.1": n, ...}
        threshold    本次使用的阈值
        top          权重最高的 Top N(便于预览,含未过线的高分条目)
    """
    cfg = _load_config()
    threshold = cfg.WEIGHT_THRESHOLD if threshold is None else float(threshold)
    if top_n is None:
        top_n = cfg.DEFAULT_TOP

    points = kb.get_points()
    total = len(points)
    mode = cfg.DUP_NORM_MODE
    # 'max' 模式需要全局最大值:整库先算一次,所有点共用同一标尺
    norm_max = global_max_dup(points) if mode == "max" else None
    coeffs = cfg.WEIGHT_COEFFS

    scored = []
    for p in points:
        w = compute_weight(p, weights=coeffs, mode=mode, norm_max=norm_max)
        scored.append((p, w))

    moved = []
    kept = []
    for p, w in scored:
        record = {
            "id": p["id"],
            "weight": w,
            "chapter": p.get("chapter"),
            "content_snip": _snippet(p.get("content")),
        }
        (moved if w >= threshold else kept).append(record)
    moved.sort(key=lambda r: (-r["weight"], r["id"]))

    top = sorted(scored, key=lambda pw: (-pw[1], pw[0]["id"]))[:top_n]
    top_list = [{
        "id": p["id"],
        "weight": w,
        "chapter": p.get("chapter"),
        "content_snip": _snippet(p.get("content")),
    } for p, w in top]

    if not dry_run:
        with _single_transaction(kb):
            for p, w in scored:
                kb.update_weight(p["id"], w)          # 幂等覆盖,不累加
                kb.set_kb_type(
                    p["id"], cfg.KB_TYPE_FOCUS if w >= threshold else cfg.KB_TYPE_MISC)

    moved_count = len(moved)
    return {
        "total": total,
        "moved": moved,
        "moved_count": moved_count,
        "kept_count": total - moved_count,
        "moved_ratio": (moved_count / total) if total else 0.0,
        "weight_hist": _weight_hist([w for _, w in scored]),
        "threshold": threshold,
        "top": top_list,
    }


# --------------------------------------------------------------------------- #
# 习题反向标记
# --------------------------------------------------------------------------- #

def _parse_points_field(value):
    """把习题的 points 字段解析成知识点字符串列表(容忍 JSON 串/裸 list/None)。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except ValueError:
            return [text]                     # 非 JSON 串:整串当作一个知识点
    if isinstance(value, list):
        return [str(x) for x in value if isinstance(x, (str, int, float)) and str(x).strip()]
    return [str(value)]


def _resolve_exam_points(kb, exercises):
    """收集待匹配的习题知识点字符串(去重保序)。

    exercises 为 None 时读库(get_all_exercise_points,即全部习题 points 扁平去重);
    也可直接传:习题 dict 列表 / points 字符串 / JSON 数组串 / 字符串列表。
    """
    if exercises is None:
        return kb.get_all_exercise_points()

    raw = []
    if isinstance(exercises, dict):
        raw.extend(_parse_points_field(exercises.get("points")))
    elif isinstance(exercises, str):
        raw.extend(_parse_points_field(exercises))
    else:
        for item in exercises:
            if isinstance(item, dict):
                raw.extend(_parse_points_field(item.get("points")))
            else:
                raw.extend(_parse_points_field(item))

    seen = set()
    result = []
    for key in raw:
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def apply_exam_hits(kb, exercises=None, dry_run=False):
    """习题库反向标记:习题 points 命中的知识点置 is_exam_hit=1。

    匹配方式(起步用精确子串,不追语义):
        把习题里的知识点串与 knowledge_point.content 都做轻量归一化
        (去空白/全角转半角/去标点),若前者是后者子串即判命中。
        归一化后短于 config.EXAM_MATCH_MIN_LEN 的串视作噪声跳过。

    参数:
        kb: 已 init_db 的 kb_store 模块
        exercises: None 读全部习题;亦可传习题 dict 列表/points 串/字符串列表
        dry_run: True 只统计命中不写库

    返回:命中的知识点条数(同一知识点被多条习题命中只计一次)。
    """
    cfg = _load_config()
    points = kb.get_points()
    norm_points = [(p["id"], normalize_text(p.get("content") or "")) for p in points]

    hit_ids = []
    for key in _resolve_exam_points(kb, exercises):
        nkey = normalize_text(key)
        if len(nkey) < cfg.EXAM_MATCH_MIN_LEN:
            continue
        for pid, ncontent in norm_points:
            if nkey and nkey in ncontent:
                hit_ids.append(pid)

    hit_ids = sorted(set(hit_ids))
    if not dry_run and hit_ids:
        with _single_transaction(kb):
            for pid in hit_ids:
                kb.set_flags(pid, is_exam_hit=1)
    return len(hit_ids)


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #

def _print_result(res, apply_exam_hits_result=None):
    """打印分流统计:总数 / 进重点 / 留零碎 / 占比 / 分数分布 / 最高分 Top N。"""
    total = res["total"]
    print("=" * 60)
    print(f"知识点总数: {total}")
    print(f"分流阈值  : {res['threshold']}")
    print("-" * 60)
    if apply_exam_hits_result is not None:
        print(f"习题命中标记: {apply_exam_hits_result} 条 (is_exam_hit=1)")
    print(f"进重点库  : {res['moved_count']} 条")
    print(f"留零碎库  : {res['kept_count']} 条")
    print(f"重点占比  : {res['moved_ratio'] * 100:.2f}%")
    print("-" * 60)
    print("分数分布:")
    for label, n in res["weight_hist"].items():
        bar = "#" * min(n, 40)
        print(f"  {label}: {n:<6d} {bar}")
    print("-" * 60)
    print(f"最高分 Top {len(res['top'])}:")
    for t in res["top"]:
        tag = "*" if t["weight"] >= res["threshold"] else " "
        print(f" {tag}[w={t['weight']:.4f}] (id={t['id']}) [{t['chapter']}] "
              f"{t['content_snip']}")
    print("=" * 60)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg = _load_config()
    parser = argparse.ArgumentParser(
        description="知识点权重合成 + 阈值分流(kb_store 公开接口,dry-run 安全)")
    parser.add_argument("--db", default=None,
                        help="SQLite 库文件路径(默认 kb_store 默认库 data/knowledge.db)")
    parser.add_argument("--threshold", type=float, default=None,
                        help=f"分流阈值 0~1,默认 config.WEIGHT_THRESHOLD({cfg.WEIGHT_THRESHOLD})")
    parser.add_argument("--dry-run", action="store_true",
                        help="只预演统计,不写库(强烈建议先跑一次)")
    parser.add_argument("--apply-exam-hits", action="store_true",
                        help="分流前先跑习题反向标记(读 exercises 表 → is_exam_hit)")
    parser.add_argument("--top", type=int, default=cfg.DEFAULT_TOP,
                        help=f"最高分 Top N 展示条数,默认 {cfg.DEFAULT_TOP}")
    args = parser.parse_args(argv)

    # 防呆:db 显式给出但不存在时直接报错,避免 init_db 静默新建空库
    if args.db and not os.path.isfile(args.db):
        print(f"错误:数据库文件不存在: {args.db}")
        return 1
    if args.threshold is not None and not 0.0 < args.threshold <= 1.0:
        print("错误:--threshold 需在 (0, 1] 区间")
        return 1
    if args.top <= 0:
        print("错误:--top 需为正整数")
        return 1

    kb = _load_kb_store()
    kb.init_db(args.db)

    hits = None
    if args.apply_exam_hits:
        hits = apply_exam_hits(kb, dry_run=args.dry_run)

    res = distribute(kb, threshold=args.threshold, dry_run=args.dry_run,
                     top_n=args.top)
    _print_result(res, hits)

    if args.dry_run:
        print("提示:以上为 dry-run 预演,未写入数据库;确认无误后去掉 --dry-run。")
    elif not args.apply_exam_hits:
        print("说明:本次未改 is_exam_hit/is_teacher/is_big_q(需要习题标记请加 "
              "--apply-exam-hits)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

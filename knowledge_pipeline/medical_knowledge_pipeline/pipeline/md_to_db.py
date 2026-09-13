# -*- coding: utf-8 -*-
"""md 文件 → SQLite 入库工具(测试用:规则切分,非 LLM 提取)。

将 <input_dir> 下所有 .md 文件按行切分为知识点条目,写入数据库。
真正的知识点提取应由 extractor.py(LLM + prompts)完成,本脚本仅用于
验证 kb_store 数据层与后续 dedup/weight 模块的效果。

用法:
    python md_to_db.py [--input <dir>] [--db <path>] [--source-kb 复习资料]

默认:
    input = test_files/test_mk_to_db
    db    = data/mk.db
"""

import argparse
import os
import re
import sys
import time

# 支持从 pipeline 下任意位置运行
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_DIR = SCRIPT_DIR
DATA_DIR = os.path.join(os.path.dirname(PIPELINE_DIR), "data")
DEFAULT_INPUT = os.path.join(SCRIPT_DIR, "..", "test_files", "test_mk_to_db")
DEFAULT_DB = os.path.join(DATA_DIR, "mk.db")

# 知识点行首编号前缀(1. 2. (1) ① 等)
_NUM_PREFIX = re.compile(r"^[\d①②③④⑤⑥⑦⑧⑨⑩()（）.\s]+")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def extract_points(md_path: str) -> list[str]:
    """按行切分 md 为知识点条目(规则版,非 LLM)。

    规则:非空行、非标题/分隔线,去行首编号后长度 >= 4 的文本作为知识点。
    """
    with open(md_path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    points = []
    for ln in lines:
        t = ln.strip()
        if not t or t.startswith("---") or t.startswith("#"):
            continue
        clean = _NUM_PREFIX.sub("", t)
        if len(clean) >= 4:
            points.append(clean)
    return points


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="md 文件 → SQLite 入库(规则切分)")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="输入 md 目录(默认 test_files/test_mk_to_db)")
    parser.add_argument("--db", default=DEFAULT_DB, help="数据库路径(默认 data/mk.db)")
    parser.add_argument("--source-kb", default="复习资料", help="材料类型标记(课本/PPT/习题册/复习资料)")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args(argv)

    input_dir = os.path.abspath(args.input)
    db_path = os.path.abspath(args.db)

    if not os.path.isdir(input_dir):
        _log(f"错误:输入目录不存在:{input_dir}")
        return 1

    md_files = sorted(f for f in os.listdir(input_dir) if f.lower().endswith(".md"))
    if not md_files:
        _log("错误:输入目录中没有 .md 文件")
        return 1

    _log(f"发现 {len(md_files)} 个 md 文件(目录:{input_dir})")
    if args.dry_run:
        total = 0
        for fname in md_files:
            n = len(extract_points(os.path.join(input_dir, fname)))
            _log(f"[dry-run] {fname}: {n} 条")
            total += n
        _log(f"合计: {total} 条(未写库)")
        return 0

    # 加载 kb_store(支持 kb/ 子目录与 pipeline 目录两种位置)
    kb_store_path = os.path.join(PIPELINE_DIR, "kb", "kb_store.py")
    if not os.path.isfile(kb_store_path):
        kb_store_path = os.path.join(PIPELINE_DIR, "kb_store.py")
    import importlib.util

    spec = importlib.util.spec_from_file_location("kb_store", kb_store_path)
    kb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kb)

    # 初始化数据库(路径由参数指定)
    kb.init_db(db_path)
    _log(f"数据库:{db_path}")

    total = 0
    for fname in md_files:
        pts = extract_points(os.path.join(input_dir, fname))
        items = [{"content": p, "chapter": None, "source_file": fname, "source_kb": args.source_kb} for p in pts]
        kb.add_points(items)
        total += len(items)
        _log(f"[{fname}] 入库 {len(items)} 条")

    stats = kb.stats()
    _log(f"入库完成,共 {total} 条。库内总数: {stats['knowledge_points']} 条")
    _log(f"分布: {kb.count_by_kb_type()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

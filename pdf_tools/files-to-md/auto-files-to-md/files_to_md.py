# -*- coding: utf-8 -*-
"""批量文件转 Markdown 工具(基于 microsoft/markitdown)。

扫描 material-input/ 下所有文件,转换为 Markdown,
输出到 material_output/ 保持目录结构镜像一致。

用法:
    python files_to_md.py [--input <dir>] [--output <dir>] [--dry-run]

依赖:
    pip install markitdown pdfplumber pdfminer.six pymupdf
"""

import argparse
import os
import sys
import time

from markitdown import MarkItDown

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(os.path.dirname(SCRIPT_DIR), "material-input")
DEFAULT_OUTPUT = os.path.join(os.path.dirname(SCRIPT_DIR), "material_output")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _collect_files(root: str):
    """递归收集 root 下所有文件路径(排除 .md 本身,已转换的不重复处理)。"""
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            if fn.lower().endswith(".md"):
                continue  # 已是 md,跳过
            found.append(full)
    return found


def _convert_one(md: MarkItDown, src: str, dst: str) -> str:
    """转换单个文件,返回 'success' / 'skip' / 'fail'。"""
    if os.path.isfile(dst) and os.path.getsize(dst) > 0:
        return "skip"
    try:
        result = md.convert(src)
        text = result.markdown or ""
        if not text.strip():
            return "fail"  # 空结果视为失败
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(text)
        return "success"
    except Exception as exc:
        _log(f"转换失败: {src} ({exc})")
        return "fail"


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="批量文件转 Markdown(markitdown)")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="输入目录(默认 material-input)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="输出目录(默认 material_output)")
    parser.add_argument("--dry-run", action="store_true", help="只统计不转换")
    parser.add_argument("--no-check", action="store_true", help="转换完成后不自动执行质量检测")
    args = parser.parse_args(argv)

    input_root = os.path.abspath(args.input)
    output_root = os.path.abspath(args.output)

    if not os.path.isdir(input_root):
        _log(f"错误:输入目录不存在:{input_root}")
        return 1

    files = _collect_files(input_root)
    _log(f"发现 {len(files)} 个待转换文件(目录:{input_root})")
    if args.dry_run:
        _log("dry-run 模式:只统计不转换")
    else:
        _log(f"输出目录:{output_root}")

    md = MarkItDown()
    stats = {"success": 0, "skip": 0, "fail": 0}
    failed_list = []

    for src in files:
        rel = os.path.relpath(src, input_root)
        base, _ext = os.path.splitext(rel)
        dst = os.path.join(output_root, base + ".md")
        if args.dry_run:
            exists = os.path.isfile(dst) and os.path.getsize(dst) > 0
            status = "skip" if exists else "convert"
            stats["success" if status == "convert" else "skip"] += 1
            _log(f"[dry-run] {'跳过(已存在)' if exists else '待转换'}: {rel}")
            continue
        status = _convert_one(md, src, dst)
        stats[status] += 1
        if status == "success":
            _log(f"已转换: {rel} -> {os.path.relpath(dst, output_root)}")
        elif status == "skip":
            _log(f"跳过(已存在): {rel}")
        else:
            failed_list.append(rel)

    print("\n" + "=" * 50)
    print("统计结果")
    print("=" * 50)
    print(f"总文件数:{len(files)}")
    print(f"成功数:{stats['success']}")
    print(f"跳过数:{stats['skip']}")
    print(f"失败数:{stats['fail']}")
    if failed_list:
        print("\n失败清单:")
        for f in failed_list:
            print(f"  - {f}")
    print("=" * 50)

    # 转换完成后自动执行质量检测(除非 --no-check)
    if not args.dry_run and not args.no_check:
        try:
            from check_quality import scan_dir

            _log("自动执行质量检测...")
            dirty, _ = scan_dir(output_root)
            if dirty:
                print("\n" + "!" * 60)
                print("⚠️  以下文件不适合 AI 阅读,建议用 PaddleOCR 重新识别:")
                for rel, reasons in dirty:
                    print(f"  - {rel}")
                    for r in reasons:
                        print(f"      {r}")
                print("!" * 60)
            else:
                print("\n✅ 质量检测:全部文件正常,无脏文件。")
        except ImportError:
            _log("警告:check_quality.py 未找到,跳过质量检测")
        except Exception as exc:
            _log(f"警告:质量检测失败({exc})")

    return 0 if stats["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

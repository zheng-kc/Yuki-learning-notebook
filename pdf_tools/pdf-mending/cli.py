# -*- coding: utf-8 -*-
"""pdf_tools 命令行入口。

用法：
    python -m pdf_tools.cli split <src> -r 201-384,496-568 [-n 名称1,名称2] [-o <outdir>]
    python -m pdf_tools.cli merge <f1> <f2> ... -o <out.pdf>
"""

import argparse
import sys

from .pdf_mending import merge_pdfs, parse_names, parse_ranges, split_pdf


def _cmd_split(args):
    try:
        ranges = parse_ranges(args.ranges)
        names = parse_names(args.names, len(ranges)) if args.names else None
    except ValueError as exc:
        print(f"错误：{exc}")
        return 1

    if names:
        ranges = [(s, e, n) for (s, e), n in zip(ranges, names)]

    out_dir = args.outdir or "pdf-output"
    result = split_pdf(args.src, ranges, out_dir, offset=args.offset)
    if not result["ok"]:
        print(f"拆分失败：{result['error']}")
        return 1

    print("拆分成功，输出文件：")
    for f in result["files"]:
        print(f"  {f['path']}（{f['range']}，{f['pages']} 页）")
    return 0


def _cmd_merge(args):
    result = merge_pdfs(args.files, args.output)
    if not result["ok"]:
        print(f"拼接失败：{result['error']}")
        return 1
    print(f"拼接成功：{result['out_path']}（共 {result['total_pages']} 页）")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="pdf_tools", description="PDF 拆分/拼接工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_split = sub.add_parser("split", help="按页码范围拆分 PDF")
    p_split.add_argument("src", help="源 PDF 文件路径")
    p_split.add_argument("-r", "--ranges", required=True, help="页码范围，多段用逗号分隔，如 201-384,496-568")
    p_split.add_argument("-n", "--names", help="每段名称，逗号分隔，数量需与范围一致")
    p_split.add_argument("-o", "--outdir", help="输出目录（默认 pdf-output）")
    p_split.add_argument("--offset", type=int, default=0, help="目录页码偏移，实际裁剪页码=输入页码+偏移（默认 0）")
    p_split.set_defaults(func=_cmd_split)

    p_merge = sub.add_parser("merge", help="按顺序拼接多个 PDF")
    p_merge.add_argument("files", nargs="+", help="待拼接的 PDF 文件（按顺序）")
    p_merge.add_argument("-o", "--output", required=True, help="输出 PDF 文件路径")
    p_merge.set_defaults(func=_cmd_merge)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

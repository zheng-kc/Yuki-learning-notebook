# -*- coding: utf-8 -*-
"""Markdown 输出质量检测:识别不适合 AI 阅读的脏文件,提醒用户改用 PaddleOCR。

判定为"脏文件"的条件(任一命中):
    1. 存在 PDF 字体子集错乱标记 (cid:NNNNN)
    2. 可读中文字符数 < 300(内容过少,疑似扫描版/乱码)
    3. 中文字符占比 < 0.25(乱码特征:字符几乎不可读)

用法:
    python check_quality.py [--dir <material_output>] [--min-cjk 300] [--cjk-ratio 0.25]

集成:files_to_md.py 转换完成后自动调用本脚本。
"""

import argparse
import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT = os.path.join(os.path.dirname(SCRIPT_DIR), "material_output")

# 乱码/异常特征
CID_RE = re.compile(r"\(cid:\d+\)", re.IGNORECASE)          # PDF 字体子集错乱
REPLACE_CHAR = "\ufffd"                                       # 替换字符 �
CJK_RE = re.compile(r"[\u4e00-\u9fff]")                      # 中文字符
# 乱码常见孤立符号(扫描版 OCR 失败时的高频噪声)
NOISE_CHARS = set("¥¤£±÷×°§©®™☐☒①②③④⑤⑥⑦⑧⑨⑩◆◇●○")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def analyze_md(path: str):
    """分析单个 md,返回 (is_dirty, reasons, stats)。"""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    cid_matches = CID_RE.findall(text)
    replace_count = text.count(REPLACE_CHAR)
    cjk_count = len(CJK_RE.findall(text))
    total_chars = len(text)
    cjk_ratio = cjk_count / total_chars if total_chars else 0

    reasons = []
    if cid_matches:
        reasons.append(f"存在 {len(cid_matches)} 处 (cid:) 字体错乱标记")
    if cjk_count < 300:
        reasons.append(f"可读中文字符仅 {cjk_count} 个(<300)")
    if cjk_ratio < 0.25:
        reasons.append(f"中文字符占比 {cjk_ratio:.2%}(<25%,疑似乱码)")
    if replace_count > 50:
        reasons.append(f"含 {replace_count} 个替换字符(�)")

    stats = {
        "total_chars": total_chars,
        "cjk_count": cjk_count,
        "cjk_ratio": round(cjk_ratio, 4),
        "cid_count": len(cid_matches),
        "replace_count": replace_count,
    }
    return (len(reasons) > 0, reasons, stats)


def scan_dir(root: str, min_cjk: int = 300, cjk_ratio: float = 0.25):
    """扫描目录下所有 md,返回脏文件清单和统计。"""
    dirty = []
    all_stats = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            if not fn.lower().endswith(".md"):
                continue
            full = os.path.join(dirpath, fn)
            try:
                is_dirty, reasons, stats = analyze_md(full)
            except Exception as exc:
                dirty.append((full, [f"读取失败: {exc}"]))
                continue
            rel = os.path.relpath(full, root)
            all_stats.append((rel, stats, is_dirty, reasons))
            if is_dirty:
                dirty.append((rel, reasons))
    return dirty, all_stats


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="检测不适合 AI 阅读的 md 文件")
    parser.add_argument("--dir", default=DEFAULT_OUTPUT, help="扫描目录(默认 material_output)")
    parser.add_argument("--min-cjk", type=int, default=300, help="可读中文字符下限(默认 300)")
    parser.add_argument("--cjk-ratio", type=float, default=0.25, help="中文字符占比下限(默认 0.25)")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.dir)
    if not os.path.isdir(root):
        _log(f"错误:目录不存在:{root}")
        return 1

    dirty, all_stats = scan_dir(root, args.min_cjk, args.cjk_ratio)
    total = len(all_stats)
    good = [s for s in all_stats if not s[2]]

    # 脏文件清单写入 dirty_md.txt(放在扫描根目录下)
    dirty_txt = os.path.join(root, "dirty_md.txt")
    try:
        with open(dirty_txt, "w", encoding="utf-8") as fh:
            fh.write("# 不适合 AI 阅读的 md 文件清单(建议用 PaddleOCR 重新识别)\n")
            fh.write(f"# 生成时间:{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            fh.write(f"# 总数:{len(dirty)}\n\n")
            for rel, reasons in dirty:
                fh.write(f"{rel}\n")
                for r in reasons:
                    fh.write(f"    - {r}\n")
                fh.write("\n")
        _log(f"脏文件清单已写入:{dirty_txt}")
    except OSError as exc:
        _log(f"警告:写入 dirty_md.txt 失败({exc})")

    print("\n" + "=" * 60)
    print(f"质量检测结果(目录:{root})")
    print("=" * 60)
    print(f"总 md 数:{total} | 正常:{len(good)} | 需 PaddleOCR 重转:{len(dirty)}")
    print("-" * 60)

    if dirty:
        print("\n⚠️  以下文件不适合 AI 阅读,建议用 PaddleOCR 重新识别:")
        for rel, reasons in dirty:
            print(f"\n  [{rel}]")
            for r in reasons:
                print(f"    - {r}")
    else:
        print("\n✅ 全部文件质量正常,无脏文件。")

    print("\n" + "-" * 60)
    print("各文件统计(仅供参考):")
    for rel, stats, is_dirty, _reasons in all_stats:
        flag = "⚠️" if is_dirty else "✅"
        print(f"  {flag} {rel} | 字符:{stats['total_chars']} | 中文:{stats['cjk_count']} "
              f"| 中文占比:{stats['cjk_ratio']:.1%} | cid:{stats['cid_count']} | 替换符:{stats['replace_count']}")
    print("=" * 60)
    return 1 if dirty else 0


if __name__ == "__main__":
    sys.exit(main())

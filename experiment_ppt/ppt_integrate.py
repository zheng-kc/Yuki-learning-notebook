# -*- coding: utf-8 -*-
"""PPT 处理整合环节:视觉结果 → 替换 <img> → 重新提取 → 关联考点。

输入:
    image_contents.json   视觉模型读出的图内内容(ppt_vision.py 产出)
    PPT md(含 <img> 链接)
输出:
    ppt_integrated.md     图内简要内容替换原 <img> 链接后的 md(供 extractor)
    关联结果打印:每张图的图内内容归属到哪个考点(最近 ## 标题)
"""

import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
IMG_CONTENTS = os.path.join(BASE, "image_contents.json")
MD_PATH = os.path.join(BASE, "..", "test", "ppt-matcher",
                       "第二章 牙周组织(2024).pdf_by_PaddleOCR-VL-1.6.md")
OUT_MD = os.path.join(BASE, "ppt_integrated.md")

IMG_RE = re.compile(r'<img\s+src="([^"]+)"[^>]*>')


def replace_images(md_text: str, img_contents: dict) -> str:
    """把 md 中每个 <img> 替换为 '【图片N·图内内容】...' 占位文本。

    图内内容来自 image_contents.json(按 imgNN 键):
        summary 非空 且 不是 "[读取失败" 且 不是 "无考点内容" → 用 summary 替换
        否则 → 仅留 '【图片NN】'(无内容可补时保留占位,不引入错误信息)
    """
    lines = md_text.splitlines()
    out = []
    for line in lines:
        m = IMG_RE.search(line)
        if not m:
            out.append(line)
            continue
        src = m.group(1)
        # 找到这张图是第几张(img{N})
        # 通过计数:按出现顺序对应 images 清单的 idx
        # 这里由调用方把 src 映射好;先按出现顺序匹配
        out.append(line)  # 占位,下面统一替换
    # 上面的遍历保留原样,真正替换用计数法:
    txt = md_text
    idx = 0
    def _repl(m):
        nonlocal idx
        key = f"img{idx:02d}"
        idx += 1
        info = img_contents.get(key)
        if info and info.get("summary") and not info["summary"].startswith("[读取失败") \
                and "无考点内容" not in info["summary"]:
            return f"【图片{idx-1:02d}·图内内容】{info['summary']}"
        return f"【图片{idx-1:02d}·无文字考点】(原图为示意图/照片,内容见原图)"
    return IMG_RE.sub(_repl, txt)


def map_images_to_headings(md_text: str, img_contents: dict) -> list[dict]:
    """关联:每张图 → 最近的前一个 markdown 标题链(考点归属)。"""
    sys.path.insert(0, BASE)
    from ppt_parse import parse_ppt_md, heading_path_str

    res = parse_ppt_md(md_text)
    rows = []
    for img in res["images"]:
        idx = img["idx"]
        key = f"img{idx:02d}"
        info = img_contents.get(key, {})
        rows.append({
            "idx": idx,
            "heading": heading_path_str(img.get("heading_path", [])),
            "summary": (info.get("summary") or "")[:80],
        })
    return rows


def main():
    if not os.path.isfile(IMG_CONTENTS):
        print(f"错误:未找到 {IMG_CONTENTS},请先运行 ppt_vision.py")
        return 1

    with open(IMG_CONTENTS, encoding="utf-8") as f:
        img_contents = json.load(f)

    with open(MD_PATH, encoding="utf-8") as f:
        md_text = f.read()

    # 1. 关联打印
    rows = map_images_to_headings(md_text, img_contents)
    print("=== 图片→考点归属 ===")
    for r in rows:
        tag = "✓" if r["summary"] and not r["summary"].startswith("[读取失败") else "·"
        print(f"  img{r['idx']:02d} [{tag}] {r['heading']}  ->  {r['summary'][:40]!r}")

    # 2. 替换 <img> 为图内内容
    new_md = replace_images(md_text, img_contents)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(new_md)
    n_img = len(IMG_RE.findall(md_text))
    n_left = len(IMG_RE.findall(new_md))
    print(f"\n替换完成:原 {n_img} 个 <img>,剩余 {n_left} 个未替换")
    print(f"整合后 md: {OUT_MD} ({len(new_md)} 字符)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
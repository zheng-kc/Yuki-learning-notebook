# -*- coding: utf-8 -*-
"""PPT md 解析模块:把 PPT(PaddleOCR-VL 转出的 md)拆成文本块 + 图片块。

每张 <img> 图记录:
    idx      序号(0 起)
    src      图片 URL
    heading  图前面最近的 markdown 标题(## 或更低层级,含正文行),用于关联考点
    prev_text 图前面一小段正文(帮助人判断图属于哪个考点)
    after_text 图后面一小段正文(语境)

目标:支撑『图片预览勾选 → 视觉模型 → 图内容补入原考点』流程。
"""

import os
import re

IMG_RE = re.compile(r'<img\s+src="([^"]+)"[^>]*>')
HEADING_RE = re.compile(r'^(#{1,6})\s+(.*)$')


def parse_ppt_md(md_text: str) -> dict:
    """解析 PPT md,返回:
    {
        "images": [ {idx, src, heading_path, prev_text, after_text}, ... ],
        "text_only": str,   # 去掉所有 <img> 标签后的纯文本(供 extractor)
    }
    heading_path: 图前面最近的『标题链』,如 ['第二章 牙周组织', '牙龈', '表面解剖']
                  用于把图挂到最近的考点标题。
    """
    lines = md_text.splitlines()
    images = []
    headings_stack = []   # 当前标题层级栈(只保留当前路径)
    pending_text = ""     # 图前面累积的正文(遇到新标题清空)
    img_idx = 0

    text_parts = []
    for raw in lines:
        line = raw.rstrip()
        # 图片行
        m = IMG_RE.search(line)
        if m:
            src = m.group(1)
            images.append({
                "idx": img_idx,
                "src": src,
                "heading_path": list(headings_stack),
                "prev_text": pending_text.strip()[-120:],
                "after_text": "",
            })
            img_idx += 1
            text_parts.append(f"[图片{img_idx - 1}]")
            pending_text = ""
            continue
        # 标题行:更新栈
        hm = HEADING_RE.match(line.strip())
        if hm:
            level = len(hm.group(1))
            title = hm.group(2).strip()
            # 栈维护:>= level 的旧标题弹出,压入本标题
            while headings_stack and headings_stack[-1][0] >= level:
                headings_stack.pop()
            headings_stack.append((level, title))
            pending_text = ""
            text_parts.append(line)
            continue
        # 普通正文行
        if line.strip():
            pending_text = (pending_text + " " + line.strip()).strip()
            text_parts.append(line)
        else:
            text_parts.append(line)

    # after_text:为每张图补上它后面一小段(下一个图片/标题前的正文)
    for i, img in enumerate(images):
        start = md_text.find(img["src"])
        if start == -1:
            continue
        tail = md_text[start + len(img["src"]):]
        # 截到下一个 <img> 或标题
        nxt_img = tail.find("<img")
        nxt_h = tail.find("\n#")
        cut = min(x for x in (nxt_img, nxt_h) if x != -1) if (nxt_img != -1 or nxt_h != -1) else len(tail)
        after = tail[:cut]
        after = IMG_RE.sub("", after)
        # 清掉 HTML 残留标签(</div>、alt 属性尾巴等)
        after = re.sub(r"<[^>]+>", "", after)
        after = re.sub(r'\\?"?\s*alt="[^"]*"\s*/?>?', "", after)
        after = re.sub(r'width="[^"]*"', "", after)
        after = after.replace("&nbsp;", " ").strip()
        img["after_text"] = after[:200]

    # 纯文本(去掉 <img> 标签,保留占位)
    text_only = "\n".join(
        "[图片%d]" % (img_idx - 1) if False else ln
        for ln in text_parts
    )
    # 上面的 text_only 生成有误(不会去掉 img),重新做一遍:
    text_only = IMG_RE.sub("[图片]", md_text)
    return {
        "images": images,
        "text_only": text_only,
        "n_images": len(images),
    }


def heading_path_str(heading_path) -> str:
    """把 heading_path 列表转成可读字符串,如 '牙龈 > 表面解剖'。"""
    if not heading_path:
        return ""
    return " > ".join(t for _, t in heading_path)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    md_path = sys.argv[1] if len(sys.argv) > 1 else \
        r"test/ppt-matcher/第二章 牙周组织(2024).pdf_by_PaddleOCR-VL-1.6.md"
    with open(md_path, encoding="utf-8") as f:
        txt = f.read()
    res = parse_ppt_md(txt)
    print(f"图片总数: {res['n_images']}")
    print(f"纯文本长度: {len(res['text_only'])} 字符")
    for img in res["images"][:8]:
        print(f"--- img{img['idx']:02d}  heading: {heading_path_str(img['heading_path'])}")
        print(f"    prev: {img['prev_text'][:80]!r}")
        print(f"    after: {img['after_text'][:80]!r}")
# -*- coding: utf-8 -*-
"""PPT 图片视觉模型提取:读取 selected_images.json(勾选的图)→ 逐张调视觉模型 → 图内简要内容。

输出:image_contents.json
    { "img14": {"summary": "图内文字/结构简要内容", "heading": "牙龈纤维束的分组"},
      ... }

关联规则:图片 idx → images 清单里的 heading_path(最近考点标题链)+ prev/after 上下文。
"""

import base64
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
SEL_PATH = os.path.join(BASE, "selected_images.json")
OUT_PATH = os.path.join(BASE, "image_contents.json")
IMG_DIR = os.path.join(BASE, "images")
MD_PATH = os.path.join(BASE, "..", "test", "ppt-matcher",
                       "第二章 牙周组织(2024).pdf_by_PaddleOCR-VL-1.6.md")

# 视觉模型配置(与 Hermes auxiliary.vision 一致:custom + DeepSeek vision)
VISION_MODEL = "deepseek-v4-flash-vision-exp"
VISION_BASE = "https://api.deepseek.com"
# DeepSeek vision 用 OpenAI 兼容接口;key 从 .env 读
ENV_PATH = os.path.join(BASE, "..", "knowledge_pipeline", ".env")


def _load_env():
    if not os.path.isfile(ENV_PATH):
        return {}
    env = {}
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _img_b64(fname: str) -> str:
    with open(os.path.join(IMG_DIR, fname), "rb") as f:
        return base64.b64encode(f.read()).decode()


def vision_read(fname: str, heading: str, prev: str, after: str) -> str:
    """调用视觉模型,返回图内简要内容。失败抛异常。"""
    import requests

    env = _load_env()
    key = env.get("DEEPSEEK_API_KEY") or env.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("未找到 API Key(knowledge_pipeline/.env)")

    b64 = _img_b64(fname)
    prompt = (
        "你是医学知识提取助手。请仔细观察这张图片,提取图内承载的医学考点内容。\n"
        "要求:\n"
        "1. 只输出图中客观呈现的信息(文字标注、结构名称、形态特征、走行/分组等),不要脑补图中没有的内容。\n"
        "2. 若图是显微照片/照片且无文字标注,简要描述图中可见的形态学特征(如'H&E染色,可见牙周膜间隙、牙槽骨')\n"
        "3. 若图是结构示意图且有文字标注,逐条列出标注文字与对应的结构/走行说明。\n"
        "4. 若图是封面/装饰图无医学内容,输出'无考点内容'。\n"
        f"图片所属考点上下文: {heading} | 前文: {prev} | 后文: {after}\n"
        "输出:一段简洁的图内内容说明(300字内)。"
    )
    resp = requests.post(
        f"{VISION_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": VISION_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
            "temperature": 0.1,
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def main():
    if not os.path.isfile(SEL_PATH):
        print(f"错误:未找到 {SEL_PATH},请先在 preview.html 里勾选并导出")
        return 1

    with open(SEL_PATH, encoding="utf-8") as f:
        sel = json.load(f).get("selected", [])
    if not sel:
        print("错误:勾选列表为空")
        return 1

    # 读取解析结果(复用 ppt_parse)
    sys.path.insert(0, BASE)
    from ppt_parse import parse_ppt_md, heading_path_str

    with open(MD_PATH, encoding="utf-8") as f:
        md_text = f.read()
    res = parse_ppt_md(md_text)
    img_map = {img["idx"]: img for img in res["images"]}

    print(f"勾选了 {len(sel)} 张图,开始视觉模型提取...")
    results = {}
    for idx in sorted(sel):
        img = img_map.get(idx)
        if not img:
            print(f"  跳过 img{idx:02d}:不在解析结果中")
            continue
        fname = f"img{idx:02d}.jpg"
        if not os.path.isfile(os.path.join(IMG_DIR, fname)):
            fname = f"img{idx:02d}.png"
        heading = heading_path_str(img.get("heading_path", []))
        print(f"  [img{idx:02d}] 调用视觉模型...", flush=True)
        try:
            summary = vision_read(fname, heading, img.get("prev_text", ""), img.get("after_text", ""))
            results[f"img{idx:02d}"] = {
                "idx": idx,
                "summary": summary,
                "heading": heading,
                "prev_text": img.get("prev_text", ""),
                "after_text": img.get("after_text", ""),
            }
            print(f"    -> {summary[:50]}...")
        except Exception as e:
            print(f"    !! 失败: {e}")
            results[f"img{idx:02d}"] = {"idx": idx, "summary": f"[读取失败: {e}]",
                                        "heading": heading}

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n完成: {len(results)} 张图内容已写入 {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
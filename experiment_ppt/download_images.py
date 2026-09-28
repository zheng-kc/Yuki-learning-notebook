# -*- coding: utf-8 -*-
"""下载 PPT md 里的 46 张 PaddleOCR 图到 experiment_ppt/images/"""
import os, re, requests

BASE = os.path.dirname(os.path.abspath(__file__))
MD = os.path.join(BASE, "..", "test", "ppt-matcher", "第二章 牙周组织(2024).pdf_by_PaddleOCR-VL-1.6.md")
OUT = os.path.join(BASE, "images")
os.makedirs(OUT, exist_ok=True)

with open(MD, encoding="utf-8") as f:
    txt = f.read()

urls = re.findall(r'<img\s+src="([^"]+)"', txt)
print(f"找到 {len(urls)} 张图")

ok, fail = 0, 0
for i, u in enumerate(urls):
    ext = ".jpg"
    name = os.path.join(OUT, f"img{i:02d}{ext}")
    try:
        r = requests.get(u, timeout=60)
        if r.status_code == 200 and len(r.content) > 1000:
            with open(name, "wb") as f:
                f.write(r.content)
            ok += 1
        else:
            print(f"  {i}: status={r.status_code} len={len(r.content)} FAIL")
            fail += 1
    except Exception as e:
        print(f"  {i}: {e}")
        fail += 1

print(f"\n完成: ok={ok} fail={fail}")
print(f"图片目录: {OUT}")
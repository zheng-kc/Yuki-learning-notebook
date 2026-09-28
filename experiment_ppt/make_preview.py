# -*- coding: utf-8 -*-
"""生成 PPT 图片预览勾选页(HTML 网格)。

输入:PPT md 解析结果(images 清单)+ 本地图片目录
输出:preview.html(网格缩略图 + 勾选框 + 考点上下文)
     用户勾选后点『导出勾选』→ 生成 selected_images.json
"""

import json
import os
import sys
import urllib.parse

sys.stdout.reconfigure(encoding="utf-8")

from ppt_parse import parse_ppt_md, heading_path_str

BASE = os.path.dirname(os.path.abspath(__file__))
MD_PATH = os.path.join(BASE, "..", "test", "ppt-matcher",
                       "第二章 牙周组织(2024).pdf_by_PaddleOCR-VL-1.6.md")
IMG_DIR = os.path.join(BASE, "images")
OUT_HTML = os.path.join(BASE, "preview.html")
OUT_SEL = os.path.join(BASE, "selected_images.json")

# 模板非 f-string:@@COUNT@@ 占位数量,@@CARDS@@ 占位卡片 HTML。
# CSS/JS 里的花括号保持原样,不做转义。
HTML_TMPL = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PPT 图片预览勾选</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: "Microsoft YaHei", sans-serif; margin: 0; background: #f5f5f5; }
  .topbar { position: sticky; top: 0; z-index: 10; background: #fff;
            padding: 12px 16px; border-bottom: 2px solid #ccc;
            display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .topbar h1 { font-size: 18px; margin: 0 8px 0 0; }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr);
          grid-auto-rows: 1fr; gap: 12px; padding: 16px; }
  .card { min-width: 0; background: #fff; border: 1px solid #ddd; border-radius: 8px;
          padding: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1);
          display: flex; flex-direction: column; gap: 6px;
          transition: box-shadow .15s, transform .15s; }
  .card:hover { box-shadow: 0 4px 12px rgba(0,0,0,.16); transform: translateY(-2px); }
  .card.selected { outline: 3px solid #4a90d9; outline-offset: -1px; }
  .chk { display: flex; align-items: center; gap: 6px; }
  .thumb { width: 100%; aspect-ratio: 4/3; display: flex; align-items: center;
           justify-content: center; background: #eee; border-radius: 4px; overflow: hidden; }
  .thumb img { width: 100%; height: 100%; object-fit: contain; display: block; }
  .heading { min-width: 0; color: #666; font-size: 12px; height: 18px; line-height: 18px;
             overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .ctx { min-width: 0; color: #888; font-size: 11px; line-height: 16px; height: 48px;
         overflow: hidden; display: -webkit-box; -webkit-line-clamp: 3;
         -webkit-box-orient: vertical; word-break: break-all; }
  button { padding: 8px 16px; font-size: 14px; cursor: pointer; }
  #selCount { font-weight: bold; }
  @media (max-width: 900px) { .grid { grid-template-columns: repeat(2, 1fr); } }
</style></head>
<body>
  <div class="topbar">
    <h1>PPT 图片预览勾选 — @@COUNT@@ 张</h1>
    <span>已选 <span id="selCount">0</span> / @@COUNT@@</span>
    <button id="all">全选</button>
    <button id="none">清空</button>
    <button id="export">导出勾选 → selected_images.json</button>
    <span id="msg"></span>
  </div>
  <div class="grid">@@CARDS@@</div>
<script>
  const cards = document.querySelectorAll('.card');
  const selCount = document.getElementById('selCount');
  function refresh() {
    const n = document.querySelectorAll('.sel:checked').length;
    selCount.textContent = n;
    cards.forEach(c => c.classList.toggle('selected', c.querySelector('.sel').checked));
  }
  cards.forEach(c => c.querySelector('.sel').addEventListener('change', refresh));
  document.getElementById('all').onclick = () => {
    document.querySelectorAll('.sel').forEach(b => b.checked = true); refresh(); };
  document.getElementById('none').onclick = () => {
    document.querySelectorAll('.sel').forEach(b => b.checked = false); refresh(); };
  document.getElementById('export').onclick = () => {
    const sel = [...document.querySelectorAll('.sel:checked')].map(b => +b.dataset.idx);
    const blob = new Blob([JSON.stringify({selected: sel}, null, 2)], {type: 'application/json'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'selected_images.json';
    a.click();
    document.getElementById('msg').textContent = '已导出 ' + sel.length + ' 张';
  };
</script>
</body></html>"""


def build_preview_html(images, img_dir, out_html):
    """把图片清单渲染成 HTML 网格,URL 用 file:// + 本地路径。"""
    cards = []
    for img in images:
        idx = img["idx"]
        fname = f"img{idx:02d}.jpg"
        fpath = os.path.join(img_dir, fname)
        if not os.path.isfile(fpath):
            fpath = os.path.join(img_dir, fname.replace(".jpg", ".png"))
        file_uri = urllib.parse.unquote(
            "file:///" + fpath.replace("\\", "/").replace(" ", "%20")
        )
        heading = heading_path_str(img.get("heading_path", []))
        heading_attr = heading.replace('"', "&quot;")
        # 关键:PPT 原文里可能残留 HTML 标签(<div></div> 等),
        # 必须转义,否则会破坏卡片 DOM 结构,导致后续卡片被吞、布局崩坏。
        import html as _html
        heading = _html.escape(heading)
        prev = _html.escape((img.get("prev_text") or "")[:60])
        after = _html.escape((img.get("after_text") or "")[:60])
        cards.append(f"""
        <div class="card" data-idx="{idx}">
          <label class="chk"><input type="checkbox" class="sel" data-idx="{idx}">
            <strong>img{idx:02d}</strong></label>
          <div class="thumb"><img loading="lazy" src="{file_uri}" alt="img{idx:02d}"></div>
          <div class="heading" title="{heading_attr}">{heading}</div>
          <div class="ctx"><b>前:</b>{prev}<br><b>后:</b>{after}</div>
        </div>""")
    html = (
        HTML_TMPL
        .replace("@@COUNT@@", str(len(images)))
        .replace("@@CARDS@@", "".join(cards))
    )
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    return out_html


if __name__ == "__main__":
    with open(MD_PATH, encoding="utf-8") as f:
        txt = f.read()
    res = parse_ppt_md(txt)
    out = build_preview_html(res["images"], IMG_DIR, OUT_HTML)
    print(f"预览页已生成: {out}")
    print(f"图片总数: {len(res['images'])}")
    print(f"勾选结果将写入: {OUT_SEL}")
    print("打开 preview.html 勾选图片,点『导出勾选』生成 selected_images.json")
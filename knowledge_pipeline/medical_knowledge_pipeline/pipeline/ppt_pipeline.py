# -*- coding: utf-8 -*-
"""PPT 材料处理管线:自定义图片选择 + 文字提取 + 视觉模型补图内内容。

输入:subject/<科目>/PPT/ 目录(或多个 PaddleOCR-VL 转出的 .md 文件)
流程:
    1. parse   解析 PPT md → 纯文本块 + 图片块清单(图位置/URL/最近考点标题链/前后文)
    2. preview 生成 HTML 网格预览页(4 列缩略图 + 勾选框),人工勾选要读的图
    3. vision  对勾选的图调视觉模型(DeepSeek vision)提取图内简要内容
    4. integrate 图内内容替换原 <img> 链接 → 生成整合 md
    5. extract 复用 extractor.extract_file 提取知识点(整合 md)
    6. store   入库 → is_teacher=1;图内内容写入 reverse_index 供反查

设计依据(详见 Obsidian: Yuki 1.5开发/_meta/PPT_pipeline.md):
    - PPT 权威信号 is_teacher 与习题册 is_exam_hit 同等地位,考点本身即重点
    - 图内细节(结构标注/纤维分组/走行)纯文字提取会丢,需视觉模型补
    - 全量视觉处理浪费 token → 人工预览挑选(实测 46 张勾 7 张 ≈ 省 85%)

接口:
    run_subject(subject, images_dir=None, out_dir=None, ...) -> dict
        处理 subject/<科目>/PPT/ 下全部 md(可指定勾选 JSON 路径)
    parse_ppt_md(md_text)                        (移植自 experiment_ppt/ppt_parse.py)
    build_preview_html(images, img_dir, out_html)(移植自 experiment_ppt/make_preview.py)
    extract_images(...)                          下载 md 内 <img> 到本地

CLI 例:
    python pipeline/ppt_pipeline.py --subject 外科学 --select selected.json
"""

import argparse
import base64
import hashlib
import html as _html
import importlib.util
import json
import os
import re
import sys
import time
import urllib.parse

try:
    import requests
except ImportError:
    requests = None

# --------------------------------------------------------------------------- #
# 路径推断(与 extractor.py / config.py 同款)
# --------------------------------------------------------------------------- #
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)          # .../medical_knowledge_pipeline
PIPELINE_ROOT = os.path.dirname(ROOT_DIR)       # .../knowledge_pipeline
DEFAULT_PROMPT = os.path.join(ROOT_DIR, "prompts", "extractor.md")
DEFAULT_ENV = os.path.join(PIPELINE_ROOT, ".env")

# PPT 材料目录约定:subject/<科目>/PPT/
PPT_DIR_NAME = "PPT"
# 图片本地化目录:data/ppt_images/<subject>/
PPT_IMG_ROOT = os.path.join(ROOT_DIR, "data", "ppt_images")

# 视觉模型配置(与 Hermes auxiliary.vision 一致:custom + DeepSeek vision)
VISION_MODEL = "deepseek-v4-flash-vision-exp"
VISION_BASE = "https://api.deepseek.com"
VISION_TIMEOUT = 120

# 预览页一次最多显示多少张图(超多图分页/提示分批勾选)
PREVIEW_MAX_IMAGES = 200

_IMG_RE = re.compile(r'<img\s+src="([^"]+)"[^>]*>')
_HEADING_RE = re.compile(r'^(#{1,6})\s+(.*)$')


# --------------------------------------------------------------------------- #
# 0. 基础工具
# --------------------------------------------------------------------------- #

def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_env(env_path: str = DEFAULT_ENV) -> None:
    if not env_path or not os.path.isfile(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


def _env_key() -> str:
    _load_env()
    return os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


# --------------------------------------------------------------------------- #
# 1. 解析:PPT md → 文本块 + 图片块
# --------------------------------------------------------------------------- #

def parse_ppt_md(md_text: str) -> dict:
    """解析 PPT md,返回:
    {
        "images": [ {idx, src, heading_path, prev_text, after_text}, ... ],
        "n_images": int,
    }
    heading_path: 图前面最近的标题链(用于关联考点)。
    """
    lines = md_text.splitlines()
    images = []
    headings_stack = []
    pending_text = ""
    img_idx = 0

    for raw in lines:
        line = raw.rstrip()
        m = _IMG_RE.search(line)
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
            pending_text = ""
            continue
        hm = _HEADING_RE.match(line.strip())
        if hm:
            level = len(hm.group(1))
            title = hm.group(2).strip()
            while headings_stack and headings_stack[-1][0] >= level:
                headings_stack.pop()
            headings_stack.append((level, title))
            pending_text = ""
            continue
        if line.strip():
            pending_text = (pending_text + " " + line.strip()).strip()

    # after_text:图后面一小段(下一个图片/标题前)
    for img in images:
        start = md_text.find(img["src"])
        if start == -1:
            continue
        tail = md_text[start + len(img["src"]):]
        nxt_img = tail.find("<img")
        nxt_h = tail.find("\n#")
        cuts = [c for c in (nxt_img, nxt_h) if c != -1]
        cut = min(cuts) if cuts else len(tail)
        after = _IMG_RE.sub("", tail[:cut])
        after = re.sub(r"<[^>]+>", "", after)
        after = re.sub(r'\s*alt="[^"]*"\s*/?>?', "", after)
        after = re.sub(r'width="[^"]*"', "", after)
        img["after_text"] = after.replace("&nbsp;", " ").strip()[:200]

    return {"images": images, "n_images": len(images)}


def heading_path_str(heading_path) -> str:
    if not heading_path:
        return ""
    return " > ".join(t for _, t in heading_path)


# --------------------------------------------------------------------------- #
# 2. 图片本地化 + 预览页
# --------------------------------------------------------------------------- #

def extract_images(md_text: str, img_dir: str, timeout: int = 60) -> int:
    """下载 md 内全部 <img> 到 img_dir(命名为 img00.jpg...),返回成功数。"""
    if requests is None:
        raise RuntimeError("未安装 requests,请先:pip install requests")
    os.makedirs(img_dir, exist_ok=True)
    urls = _IMG_RE.findall(md_text)
    ok = 0
    for i, u in enumerate(urls):
        ext = ".jpg"  # PaddleOCR 统一 jpg;无法判断时按扩展名兜底
        if ".png" in u.lower() or "png" in u.lower().split("?")[0]:
            ext = ".png"
        fname = os.path.join(img_dir, f"img{i:02d}{ext}")
        if os.path.isfile(fname) and os.path.getsize(fname) > 1000:
            ok += 1
            continue
        try:
            r = requests.get(u, timeout=timeout)
            if r.status_code == 200 and len(r.content) > 1000:
                with open(fname, "wb") as f:
                    f.write(r.content)
                ok += 1
            else:
                _log(f"  img{i:02d}: status={r.status_code} len={len(r.content)} 跳过")
        except Exception as e:
            _log(f"  img{i:02d}: {e} 跳过")
    return ok


def build_preview_html(images, img_dir, out_html, count: int = -1) -> str:
    """生成 4 列网格预览勾选页(缩略图 + 考点上下文 + 勾选框)。

    关键:所有插入文本(上下文中可能含 PaddleOCR 残留 HTML 标签)必须转义,
    否则未闭合标签会吞掉后续卡片、破坏 DOM(实测踩坑,见 Obsidian 排障记录)。
    """
    cards = []
    for img in images:
        idx = img["idx"]
        # 找本地文件(优先 jpg,兜底 png)
        fpath = os.path.join(img_dir, f"img{idx:02d}.jpg")
        if not os.path.isfile(fpath):
            fpath = os.path.join(img_dir, f"img{idx:02d}.png")
        if not os.path.isfile(fpath):
            continue  # 本地无图则跳过该卡
        file_uri = "file:///" + fpath.replace("\\", "/").replace(" ", "%20")
        heading = _html.escape(heading_path_str(img.get("heading_path", [])))
        prev = _html.escape((img.get("prev_text") or "")[:60])
        after = _html.escape((img.get("after_text") or "")[:60])
        cards.append(f"""
        <div class="card" data-idx="{idx}">
          <label class="chk"><input type="checkbox" class="sel" data-idx="{idx}">
            <strong>img{idx:02d}</strong></label>
          <div class="heading">{heading}</div>
          <div class="thumb"><img loading="lazy" src="{file_uri}" alt="img{idx:02d}"></div>
          <div class="ctx"><b>前:</b>{prev}<br><b>后:</b>{after}</div>
        </div>""")

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PPT 图片预览勾选</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: "Microsoft YaHei", sans-serif; margin: 0; background: #f5f5f5; }}
  .topbar {{ position: sticky; top: 0; z-index: 10; background: #fff;
            padding: 12px 16px; border-bottom: 2px solid #ccc;
            display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
  .grid {{ display: grid; grid-template-columns: repeat(4, 1fr);
          grid-auto-rows: 1fr; gap: 12px; padding: 16px; }}
  .card {{ min-width: 0; background: #fff; border: 1px solid #ddd; border-radius: 8px;
          padding: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1);
          display: flex; flex-direction: column; gap: 6px; }}
  .card.selected {{ outline: 3px solid #4a90d9; }}
  .chk {{ display: flex; align-items: center; gap: 6px; }}
  .thumb {{ width: 100%; aspect-ratio: 4/3; display: flex; align-items: center;
           justify-content: center; background: #eee; border-radius: 4px; overflow: hidden; }}
  .thumb img {{ max-width: 100%; max-height: 100%; object-fit: contain; }}
  .heading {{ min-width: 0; color: #666; font-size: 12px; height: 18px; line-height: 18px;
             overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }}
  .ctx {{ min-width: 0; color: #888; font-size: 11px; line-height: 16px; height: 48px;
         overflow: hidden; display: -webkit-box; -webkit-line-clamp: 3; word-break: break-all; }}
  button {{ padding: 8px 16px; font-size: 14px; cursor: pointer; }}
  @media (max-width: 900px) {{ .grid {{ grid-template-columns: repeat(2, 1fr); }} }}
</style></head>
<body>
  <div class="topbar">
    <h1>PPT 图片预览勾选 — {count} 张</h1>
    <span>已选 <span id="selCount">0</span> / {count}</span>
    <button id="all">全选</button>
    <button id="none">清空</button>
    <button id="export">导出勾选 → selected_images.json</button>
    <span id="msg"></span>
  </div>
  <div class="grid">{''.join(cards)}</div>
<script>
  const cards = document.querySelectorAll('.card');
  const selCount = document.getElementById('selCount');
  function refresh() {{
    const n = document.querySelectorAll('.sel:checked').length;
    selCount.textContent = n;
    cards.forEach(c => c.classList.toggle('selected', c.querySelector('.sel').checked));
  }}
  cards.forEach(c => c.querySelector('.sel').addEventListener('change', refresh));
  document.getElementById('all').onclick = () => {{
    document.querySelectorAll('.sel').forEach(b => b.checked = true); refresh(); }};
  document.getElementById('none').onclick = () => {{
    document.querySelectorAll('.sel').forEach(b => b.checked = false); refresh(); }};
  document.getElementById('export').onclick = () => {{
    const sel = [...document.querySelectorAll('.sel:checked')].map(b => +b.dataset.idx);
    const blob = new Blob([JSON.stringify({{selected: sel}}, null, 2)], {{type: 'application/json'}});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'selected_images.json';
    a.click();
    document.getElementById('msg').textContent = '已导出 ' + sel.length + ' 张';
  }};
</script>
</body></html>"""
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    return out_html


# --------------------------------------------------------------------------- #
# 3. 视觉模型提取图内内容
# --------------------------------------------------------------------------- #

def _img_b64(fpath: str) -> str:
    with open(fpath, "rb") as f:
        return base64.b64encode(f.read()).decode()


def vision_read(fpath: str, heading: str = "", prev: str = "", after: str = "",
                api_key: str = "") -> str:
    """调用视觉模型,返回图内简要内容。失败抛异常。"""
    if requests is None:
        raise RuntimeError("未安装 requests,请先:pip install requests")
    key = api_key or _env_key()
    if not key:
        raise RuntimeError("未设置 API Key(knowledge_pipeline/.env 的 DEEPSEEK_API_KEY 或 OPENAI_API_KEY)")

    b64 = _img_b64(fpath)
    prompt = (
        "你是医学知识提取助手。请仔细观察这张图片,提取图内承载的医学考点内容。\n"
        "要求:\n"
        "1. 只输出图中客观呈现的信息(文字标注、结构名称、形态特征、走行/分组等),不要脑补图中没有的内容。\n"
        "2. 若图是显微照片/照片且无文字标注,简要描述图中可见的形态学特征。\n"
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
        timeout=VISION_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def read_selected(select_path: str) -> list[int]:
    """读取勾选 JSON,返回选中图 idx 列表。"""
    with open(select_path, encoding="utf-8") as f:
        data = json.load(f)
    sel = data.get("selected", []) if isinstance(data, dict) else data
    return [int(i) for i in sel]


# --------------------------------------------------------------------------- #
# 4. 整合:图内内容替换 <img> 链接
# --------------------------------------------------------------------------- #

def integrate_images(md_text: str, img_contents: dict) -> str:
    """把 md 中每个 <img> 替换为『【图片N·图内内容】…』或占位文本。

    img_contents: { "img05": {"summary": "...", ...}, ... }
    """
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
    return _IMG_RE.sub(_repl, md_text)


# --------------------------------------------------------------------------- #
# 5. 正式流程:run_subject
# --------------------------------------------------------------------------- #

def _load_extractor():
    """按文件路径动态加载 extractor.py(与 extractor._load_kb_store 同思路)。"""
    ext_path = os.path.join(SCRIPT_DIR, "extractor.py")
    spec = importlib.util.spec_from_file_location("extractor_mod", ext_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_subject(subject: str, select_path: str = None, db_path: str = None,
                prompt_path: str = None, chapter_path: str = None,
                cache_path: str = None, out_dir: str = None,
                api_key: str = "", skip_vision: bool = False,
                force_extract: bool = False) -> dict:
    """处理 subject/<科目>/PPT/ 目录下全部 md,返回流程统计 dict。

    subject:      科目名(如 "外科学");PPT 材料位于 subject/<科目>/PPT/
    select_path:  人工勾选 JSON(selected_images.json)路径;None 则只到预览就停
    db_path:      落库路径(默认 data/ppt_<科目>.db)
    out_dir:      中间产物目录(默认 data/ppt_work/<科目>/)
    skip_vision:  已有 image_contents.json 时跳过视觉调用(断点续跑)
    """
    extractor = _load_extractor()

    if prompt_path is None:
        prompt_path = DEFAULT_PROMPT
    subject_dir = os.path.join(ROOT_DIR, "subject", subject)
    ppt_dir = os.path.join(subject_dir, PPT_DIR_NAME)
    if not os.path.isdir(ppt_dir):
        # 兼容:显式传入 PPT md 所在目录(非 subject/PPT 布局时)
        if os.path.isdir(subject):
            ppt_dir = subject
        else:
            raise FileNotFoundError(f"PPT 目录不存在:{ppt_dir}")

    if out_dir is None:
        out_dir = os.path.join(ROOT_DIR, "data", "ppt_work", subject)
    if db_path is None:
        db_path = os.path.join(ROOT_DIR, "data", f"ppt_{subject}.db")
    if cache_path is None:
        cache_path = os.path.join(ROOT_DIR, "data", f"ppt_cache_{subject}.json")
    if chapter_path is None:
        chapter_path = os.path.join(subject_dir, "learning_chapter.txt")
    os.makedirs(out_dir, exist_ok=True)

    img_dir = os.path.join(PPT_IMG_ROOT, subject)
    os.makedirs(img_dir, exist_ok=True)

    md_files = sorted(
        f for f in os.listdir(ppt_dir)
        if f.lower().endswith(".md") and "integrated" not in f
    )
    if not md_files:
        raise FileNotFoundError(f"PPT 目录无 .md 文件:{ppt_dir}")

    stats = {"subject": subject, "files": [], "images_total": 0, "images_selected": 0,
             "extracted": 0, "stored": 0, "teacher_flags": 0, "reverse_index": 0}

    # ---------- ① 解析 + 图片本地化 + 预览 ----------
    for fname in md_files:
        md_path = os.path.join(ppt_dir, fname)
        md_text = _read_text(md_path)
        res = parse_ppt_md(md_text)
        stats["images_total"] += res["n_images"]
        _log(f"[{fname}] 解析: {res['n_images']} 张图")

        if res["images"]:
            _log(f"[{fname}] 下载图片到 {img_dir} ...")
            extract_images(md_text, img_dir)

        # 每份文件生成一个预览页(多文件各自勾选)
        preview_html = os.path.join(out_dir, f"preview_{fname}.html".replace(".md", ""))
        build_preview_html(res["images"], img_dir, preview_html, res["n_images"])
        _log(f"  预览页: {preview_html}")
        stats["files"].append({"file": fname, "images": res["n_images"],
                               "preview": preview_html})

    # ---------- ② 勾选(run_subject 停在预览;勾选后带 --select 重跑) ----------
    if not select_path:
        _log("已生成预览页。请在浏览器打开勾选图片 → 导出 selected_images.json →")
        _log("带 --select <路径> 重新运行以继续视觉提取与入库。")
        return stats

    sel = read_selected(select_path)
    stats["images_selected"] = len(sel)
    _log(f"勾选 {len(sel)} 张图: {sel}")

    # ---------- ③ 视觉模型提取图内内容 ----------
    img_map = {}
    for fname in md_files:
        md_text = _read_text(os.path.join(ppt_dir, fname))
        res = parse_ppt_md(md_text)
        for img in res["images"]:
            img_map[img["idx"]] = img

    img_contents_path = os.path.join(out_dir, "image_contents.json")
    img_contents = {}
    if skip_vision and os.path.isfile(img_contents_path):
        with open(img_contents_path, encoding="utf-8") as f:
            img_contents = json.load(f)
        _log(f"skip_vision: 读取已有 {img_contents_path} ({len(img_contents)} 张)")
    else:
        for idx in sorted(sel):
            img = img_map.get(idx)
            if not img:
                continue
            fpath = os.path.join(img_dir, f"img{idx:02d}.jpg")
            if not os.path.isfile(fpath):
                fpath = os.path.join(img_dir, f"img{idx:02d}.png")
            if not os.path.isfile(fpath):
                _log(f"  img{idx:02d}: 本地文件缺失,跳过")
                continue
            key = f"img{idx:02d}"
            heading = heading_path_str(img.get("heading_path", []))
            _log(f"  [{key}] 视觉模型提取...", )
            try:
                summary = vision_read(fpath, heading, img.get("prev_text", ""),
                                      img.get("after_text", ""), api_key)
                img_contents[key] = {"idx": idx, "summary": summary, "heading": heading}
                _log(f"    -> {summary[:60]}...")
            except Exception as e:
                _log(f"    !! 失败: {e}")
                img_contents[key] = {"idx": idx, "summary": f"[读取失败: {e}]",
                                     "heading": heading}
        with open(img_contents_path, "w", encoding="utf-8") as f:
            json.dump(img_contents, f, ensure_ascii=False, indent=2)
        _log(f"视觉内容写入 {img_contents_path}")

    # ---------- ④ 整合 + 提取 + 入库 ----------
    kb = extractor._load_kb_store()
    kb.init_db(db_path)

    for fname in md_files:
        md_path = os.path.join(ppt_dir, fname)
        md_text = _read_text(md_path)
        integrated = integrate_images(md_text, img_contents)
        integrated_path = os.path.join(out_dir, f"integrated_{fname}")
        with open(integrated_path, "w", encoding="utf-8") as f:
            f.write(integrated)
        _log(f"[{fname}] 整合 md: {integrated_path} ({len(integrated)} 字符)")

        points = extractor.extract_file(integrated_path, prompt_path, chapter_path,
                                        cache_path, force_extract)
        items = [
            {"content": p["content"], "chapter": p.get("chapter"),
             "title": p.get("title"),
             "source_file": f"PPT/{fname}", "source_kb": "PPT"}
            for p in points if p.get("content")
        ]
        ids = kb.add_points(items)
        for pid in ids:
            kb.set_flags(pid, is_teacher=1)
        stats["extracted"] += len(items)
        stats["stored"] += len(ids)
        stats["teacher_flags"] += len(ids)
        _log(f"  提取 {len(items)} 条,is_teacher=1 落库")

    # ---------- ⑤ 图内内容写反向索引(PPT 图片本身可被反查) ----------
    rev_pairs = []
    for key, info in img_contents.items():
        summary = info.get("summary", "")
        if not summary or summary.startswith("[读取失败") or "无考点内容" in summary:
            continue
        m = re.match(r"img(\d+)", key)
        if not m:
            continue
        rev_pairs.append((None, f"[PPT图内内容] {summary}", None))
    stats["reverse_index"] = len(rev_pairs)
    _log(f"反向索引素材: {len(rev_pairs)} 条图内内容(待关联知识点后写入)")

    stats["db"] = db_path
    stats["out_dir"] = out_dir
    _log(f"完成: 提取 {stats['extracted']} 条,is_teacher=1 × {stats['teacher_flags']},"
         f"库: {db_path}")
    return stats


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="PPT 材料处理管线(图片选择 + 视觉提取 + 入库)")
    parser.add_argument("--subject", default="外科学", help="科目名(材料目录 subject/<科目>/PPT/)")
    parser.add_argument("--select", default=None, help="勾选 JSON 路径(selected_images.json)")
    parser.add_argument("--db", default=None, help="数据库路径(默认 data/ppt_<科目>.db)")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="提取提示词路径")
    parser.add_argument("--chapter", default=None, help="章节清单路径(默认 subject/<科目>/learning_chapter.txt)")
    parser.add_argument("--cache", default=None, help="提取缓存路径")
    parser.add_argument("--out", default=None, help="中间产物目录")
    parser.add_argument("--skip-vision", action="store_true", help="跳过视觉模型(已有 image_contents.json 时续跑)")
    parser.add_argument("--force", action="store_true", help="强制重提提取(忽略缓存)")
    args = parser.parse_args(argv)

    try:
        stats = run_subject(
            subject=args.subject, select_path=args.select, db_path=args.db,
            prompt_path=args.prompt, chapter_path=args.chapter,
            cache_path=args.cache, out_dir=args.out,
            skip_vision=args.skip_vision, force_extract=args.force,
        )
    except Exception as exc:
        _log(f"错误:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
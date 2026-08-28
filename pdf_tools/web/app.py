# -*- coding: utf-8 -*-
"""PDF 工具 FastAPI Web 服务。"""

import os
import uuid
from urllib.parse import quote

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..pdf_tools import merge_pdfs, parse_names, parse_ranges, split_pdf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, "tmp")
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
os.makedirs(TMP_DIR, exist_ok=True)

app = FastAPI(title="PDF 工具")


def _token() -> str:
    return uuid.uuid4().hex[:8]


def _safe_remove(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _resolve_in_project(raw: str):
    """把绝对路径或项目内相对路径解析为绝对路径；不在项目根目录内则返回 None。"""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    if os.path.isabs(raw):
        candidate = os.path.abspath(raw)
    else:
        candidate = os.path.abspath(os.path.join(PROJECT_ROOT, raw))
    candidate = os.path.realpath(candidate)
    root = os.path.realpath(PROJECT_ROOT)
    try:
        common = os.path.commonpath([candidate, root])
    except ValueError:
        return None
    if common != root:
        return None
    return candidate


def _resolve_out_dir(out_dir: str):
    """解析输出目录：空则用默认 tmp 目录，否则校验必须在项目根目录内。"""
    if not out_dir or not out_dir.strip():
        return TMP_DIR
    return _resolve_in_project(out_dir)


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(BASE_DIR, "index.html"), encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@app.post("/api/split")
async def api_split(
    file: UploadFile = File(...),
    ranges: str = Form(""),
    book_ranges: str = Form(""),
    names: str = Form(""),
    offset: int = Form(0),
    out_dir: str = Form(""),
):
    use_default = not out_dir or not out_dir.strip()
    target_dir = _resolve_out_dir(out_dir)
    if target_dir is None:
        return JSONResponse({"ok": False, "error": "输出目录不合法"}, status_code=400)

    token = _token()
    src_path = os.path.join(TMP_DIR, f"upload_{token}.pdf")
    content = await file.read()
    with open(src_path, "wb") as fh:
        fh.write(content)

    try:
        ranges_text = (ranges or "").strip()
        book_ranges_text = (book_ranges or "").strip()

        if ranges_text and book_ranges_text:
            return JSONResponse({"ok": False, "error": "PDF页码与书本页码请二选一"}, status_code=400)
        if not ranges_text and not book_ranges_text:
            return JSONResponse({"ok": False, "error": "页码必填"}, status_code=400)

        try:
            if book_ranges_text:
                range_list = parse_ranges(book_ranges_text)
                effective_offset = offset
            else:
                range_list = parse_ranges(ranges_text)
                effective_offset = 0
            name_list = parse_names(names, len(range_list)) if names and names.strip() else None
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        if name_list:
            range_list = [(s, e, n) for (s, e), n in zip(range_list, name_list)]

        result = split_pdf(src_path, range_list, target_dir, offset=effective_offset)
        if not result["ok"]:
            return JSONResponse({"ok": False, "error": result["error"]}, status_code=400)

        files = []
        for f in result["files"]:
            if use_default:
                new_name = f"{token}_{f['filename']}"
                os.replace(f["path"], os.path.join(TMP_DIR, new_name))
                full = os.path.join(TMP_DIR, new_name)
                files.append({
                    "name": new_name,
                    "path": os.path.relpath(full, PROJECT_ROOT),
                    "url": f"/api/download/{quote(new_name)}",
                    "pdf_range": f["range"],
                })
            else:
                full = f["path"]
                files.append({
                    "name": f["filename"],
                    "path": os.path.relpath(full, PROJECT_ROOT),
                    "url": f"/api/download/{quote(f['filename'])}?path={quote(os.path.relpath(full, PROJECT_ROOT))}",
                    "pdf_range": f["range"],
                })

        return {"ok": True, "files": files}
    finally:
        _safe_remove(src_path)


@app.post("/api/merge")
async def api_merge(files: list[UploadFile] = File(...), order: str = Form(""), out_dir: str = Form("")):
    if not files:
        return JSONResponse({"ok": False, "error": "未上传任何文件"}, status_code=400)

    use_default = not out_dir or not out_dir.strip()
    target_dir = _resolve_out_dir(out_dir)
    if target_dir is None:
        return JSONResponse({"ok": False, "error": "输出目录不合法"}, status_code=400)

    token = _token()
    saved = []
    for f in files:
        safe_name = os.path.basename(f.filename or "upload.pdf")
        path = os.path.join(TMP_DIR, f"src_{token}_{safe_name}")
        content = await f.read()
        with open(path, "wb") as fh:
            fh.write(content)
        saved.append(path)

    try:
        if order and order.strip():
            try:
                indices = [int(x.strip()) for x in order.split(",") if x.strip()]
            except ValueError:
                return JSONResponse({"ok": False, "error": "order 参数格式错误，应为逗号分隔的序号"}, status_code=400)
            if any(i < 0 or i >= len(saved) for i in indices):
                return JSONResponse({"ok": False, "error": "order 序号越界"}, status_code=400)
            ordered = [saved[i] for i in indices]
        else:
            ordered = saved

        out_name = f"merged_{token}.pdf"
        out_path = os.path.join(target_dir, out_name)
        result = merge_pdfs(ordered, out_path)
        if not result["ok"]:
            return JSONResponse({"ok": False, "error": result["error"]}, status_code=400)

        rel = os.path.relpath(out_path, PROJECT_ROOT)
        url = f"/api/download/{quote(out_name)}" if use_default else f"/api/download/{quote(out_name)}?path={quote(rel)}"
        return {"ok": True, "url": url, "path": rel}
    finally:
        for p in saved:
            _safe_remove(p)


@app.get("/api/download/{filename}")
def download(filename: str, path: str = ""):
    if path and path.strip():
        full_path = _resolve_in_project(path)
        if full_path is None:
            return JSONResponse({"ok": False, "error": "输出目录不合法"}, status_code=400)
        if not os.path.isfile(full_path):
            return JSONResponse({"ok": False, "error": "文件不存在"}, status_code=404)
        return FileResponse(full_path, media_type="application/pdf", filename=os.path.basename(full_path))

    safe_name = os.path.basename(filename)
    full_path = os.path.realpath(os.path.join(TMP_DIR, safe_name))
    if not full_path.startswith(os.path.realpath(TMP_DIR) + os.sep):
        return JSONResponse({"ok": False, "error": "非法文件名"}, status_code=400)
    if not os.path.isfile(full_path):
        return JSONResponse({"ok": False, "error": "文件不存在"}, status_code=404)
    return FileResponse(full_path, media_type="application/pdf", filename=safe_name)

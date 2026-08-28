# -*- coding: utf-8 -*-
"""泛化 PDF 工具：按页码范围拆分 PDF、按顺序拼接多个 PDF。"""

import os
import time

from pypdf import PdfReader, PdfWriter

# Windows 文件名中不允许出现的字符
_ILLEGAL_CHARS = '\\/:*?"<>|'


def _log(msg: str) -> None:
    """输出带时间戳的中文日志。"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _sanitize_filename(name: str) -> str:
    """去除文件名中的非法字符，返回安全文件名（不含扩展名）。"""
    for ch in _ILLEGAL_CHARS:
        name = name.replace(ch, "_")
    name = name.strip().strip(".")
    return name or "segment"


def _result(ok: bool, **fields):
    result = {"ok": ok}
    result.update(fields)
    return result


def parse_ranges(text):
    """解析 '201-384,496-568' 或单个页码 '100' 形式的页码范围字符串。

    返回 [(start, end), ...]（1-based，含首尾）。非法输入抛出 ValueError。
    """
    if not text or not str(text).strip():
        raise ValueError("页码范围为空")
    ranges = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                left, right = part.split("-", 1)
                start, end = int(left.strip()), int(right.strip())
            else:
                start = end = int(part)
        except ValueError:
            raise ValueError(f"页码范围格式错误：{part!r}，应为 'start-end' 或单个页码")
        ranges.append((start, end))
    if not ranges:
        raise ValueError("页码范围为空")
    return ranges


def parse_names(text, count):
    """解析逗号分隔的段名称，数量须与 count 一致；为空时返回 None。"""
    names = [n.strip() for n in (str(text) if text else "").split(",") if n.strip()]
    if not names:
        return None
    if len(names) != count:
        raise ValueError(f"名称数量 {len(names)} 与页码范围数量 {count} 不一致")
    return names


def split_pdf(src_path, ranges, out_dir, offset=0):
    """按页码范围拆分源 PDF 为多个 PDF 文件。

    参数：
        src_path: 源 PDF 文件路径。
        ranges: [(start, end[, name]), ...]，1-based 页码（含首尾），可带段名。
        out_dir: 输出目录。
        offset: 目录页码偏移（非负整数），实际裁剪页码 = 输入页码 + offset。

    返回 dict：成功时 ok=True 且包含 files 列表；失败时 ok=False 且包含 error。
    """
    _log(f"开始拆分：源文件 {src_path}")

    if not isinstance(offset, int) or offset < 0:
        msg = f"offset 必须为非负整数：{offset!r}"
        _log(f"错误：{msg}")
        return _result(False, error=msg)
    if offset:
        _log(f"偏移 {offset} 页")

    if not os.path.isfile(src_path):
        msg = f"源 PDF 文件不存在：{src_path}"
        _log(f"错误：{msg}")
        return _result(False, error=msg)

    if not ranges:
        msg = "页码范围为空"
        _log(f"错误：{msg}")
        return _result(False, error=msg)

    # 归一化 ranges 为 [(start, end, name), ...]
    normalized = []
    for idx, item in enumerate(ranges):
        if not isinstance(item, (tuple, list)) or not (2 <= len(item) <= 3):
            msg = f"第 {idx + 1} 段格式非法：{item!r}，应为 (start, end) 或 (start, end, name)"
            _log(f"错误：{msg}")
            return _result(False, error=msg)
        start, end = item[0], item[1]
        if not isinstance(start, int) or not isinstance(end, int):
            msg = f"第 {idx + 1} 段页码必须为整数：{item!r}"
            _log(f"错误：{msg}")
            return _result(False, error=msg)
        if start < 1 or end < start:
            msg = f"第 {idx + 1} 段页码非法：{start}-{end}"
            _log(f"错误：{msg}")
            return _result(False, error=msg)
        name = item[2] if len(item) == 3 else None
        if name is not None:
            name = str(name)
        normalized.append((start, end, name))

    if offset:
        normalized = [(start + offset, end + offset, name) for start, end, name in normalized]

    # 读取源 PDF
    try:
        reader = PdfReader(src_path)
    except Exception as exc:
        msg = f"读取源 PDF 失败：{exc}"
        _log(f"错误：{msg}")
        return _result(False, error=msg)

    total_pages = len(reader.pages)
    _log(f"源 PDF 总页数：{total_pages}")

    # 页码越界校验
    for start, end, _name in normalized:
        if end > total_pages:
            msg = f"页码越界：{start}-{end} 超出源 PDF 总页数 {total_pages}"
            _log(f"错误：{msg}")
            return _result(False, error=msg)

    # 范围重叠校验
    ordered = sorted(normalized, key=lambda r: r[0])
    for i in range(1, len(ordered)):
        if ordered[i][0] <= ordered[i - 1][1]:
            msg = f"页码范围重叠：{ordered[i - 1][0]}-{ordered[i - 1][1]} 与 {ordered[i][0]}-{ordered[i][1]}"
            _log(f"错误：{msg}")
            return _result(False, error=msg)

    # 创建输出目录
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        msg = f"创建输出目录失败：{out_dir}（{exc}）"
        _log(f"错误：{msg}")
        return _result(False, error=msg)

    src_stem = _sanitize_filename(os.path.splitext(os.path.basename(src_path))[0])

    files = []
    for start, end, name in normalized:
        if name:
            filename = f"{_sanitize_filename(name)}_{start}-{end}.pdf"
        else:
            filename = f"{src_stem}_{start}-{end}.pdf"
        out_path = os.path.join(out_dir, filename)

        writer = PdfWriter()
        for index in range(start - 1, end):
            writer.add_page(reader.pages[index])
        try:
            with open(out_path, "wb") as fh:
                writer.write(fh)
        except OSError as exc:
            msg = f"写入输出文件失败：{out_path}（{exc}）"
            _log(f"错误：{msg}")
            return _result(False, error=msg)

        count = end - start + 1
        _log(f"已生成：{filename}（页码 {start}-{end}，{count} 页）")
        files.append({
            "range": f"{start}-{end}",
            "name": name,
            "filename": filename,
            "path": out_path,
            "pages": count,
        })

    _log(f"拆分完成，共 {len(files)} 个文件")
    return _result(
        True,
        src_path=src_path,
        total_pages=total_pages,
        out_dir=out_dir,
        files=files,
    )


def merge_pdfs(src_paths, out_path):
    """按顺序拼接多个 PDF 为一个 PDF。

    参数：
        src_paths: 待拼接 PDF 文件路径列表（按顺序）。
        out_path: 输出 PDF 文件路径。

    返回 dict：成功时 ok=True 且包含 out_path、total_pages；失败时 ok=False 且包含 error。
    """
    _log(f"开始拼接：{len(src_paths)} 个文件 -> {out_path}")

    if not src_paths:
        msg = "待拼接的 PDF 文件列表为空"
        _log(f"错误：{msg}")
        return _result(False, error=msg)

    missing = [p for p in src_paths if not os.path.isfile(p)]
    if missing:
        msg = f"以下文件不存在：{', '.join(missing)}"
        _log(f"错误：{msg}")
        return _result(False, error=msg)

    out_dir = os.path.dirname(os.path.abspath(out_path))
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        msg = f"创建输出目录失败：{out_dir}（{exc}）"
        _log(f"错误：{msg}")
        return _result(False, error=msg)

    writer = PdfWriter()
    total_pages = 0
    merged = []
    for path in src_paths:
        try:
            reader = PdfReader(path)
        except Exception as exc:
            msg = f"读取 PDF 失败：{path}（{exc}）"
            _log(f"错误：{msg}")
            return _result(False, error=msg)
        pages = len(reader.pages)
        for page in reader.pages:
            writer.add_page(page)
        total_pages += pages
        _log(f"已合并：{os.path.basename(path)}（{pages} 页）")
        merged.append({"path": path, "filename": os.path.basename(path), "pages": pages})

    try:
        with open(out_path, "wb") as fh:
            writer.write(fh)
    except OSError as exc:
        msg = f"写入输出文件失败：{out_path}（{exc}）"
        _log(f"错误：{msg}")
        return _result(False, error=msg)

    _log(f"拼接完成：共 {total_pages} 页")
    return _result(
        True,
        out_path=out_path,
        total_pages=total_pages,
        files_merged=merged,
    )

# -*- coding: utf-8 -*-
"""下载 md 中的 pplines 图片并重定向为本地相对路径。

遍历 md-input 下所有 .md（递归），提取 <img src="https://pplines-online..."> 图片，
下载到 md-images 的镜像路径，并在 md-output 生成改写后的 md（图片改为相对路径）。

用法：
    python pdf_tools/pdf-to-md/fetch_images.py [--input <dir>] [--output <dir>]
                                               [--images <dir>] [--workers N] [--dry-run]
"""

import argparse
import os
import posixpath
import re
import shutil
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

IMG_RE = re.compile(r'<img src="(https://pplines-online[^"]+)"')

RETRIES = 2
TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _image_filename(url: str) -> str:
    """取 URL 路径最后一段作为图片文件名（忽略查询串）。"""
    path = urlparse(url).path
    name = posixpath.basename(path)
    return name or "image"


def _download(url: str, dest: str, retries: int = RETRIES, timeout: int = TIMEOUT) -> str:
    """下载单张图片，返回 "skip" / "success" / "fail"。"""
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return "skip"

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as fh:
                shutil.copyfileobj(resp, fh)
            if os.path.getsize(tmp) == 0:
                raise OSError("空文件")
            os.replace(tmp, dest)
            return "success"
        except Exception as exc:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            if attempt < retries:
                _log(f"重试 {attempt + 1}/{retries}：{url}（{exc}）")
            else:
                _log(f"下载失败：{url}（{exc}）")
    return "fail"


def _collect(root: str):
    """递归收集 root 下所有 .md 的绝对路径。"""
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            if fn.lower().endswith(".md"):
                found.append(os.path.join(dirpath, fn))
    return found


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="下载 md 中的 pplines 图片并重定向为本地相对路径")
    parser.add_argument("--input", default=os.path.join(SCRIPT_DIR, "md-input"),
                        help="md 输入目录（默认脚本同级 md-input）")
    parser.add_argument("--output", default=os.path.join(SCRIPT_DIR, "md-output"),
                        help="处理后 md 输出目录（默认脚本同级 md-output）")
    parser.add_argument("--images", default=os.path.join(SCRIPT_DIR, "md-images"),
                        help="图片存放目录（默认脚本同级 md-images）")
    parser.add_argument("--workers", type=int, default=8, help="并发下载线程数（默认 8）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不下载")
    args = parser.parse_args(argv)

    input_root = os.path.abspath(args.input)
    output_root = os.path.abspath(args.output)
    images_root = os.path.abspath(args.images)

    if not os.path.isdir(input_root):
        _log(f"错误：输入目录不存在：{input_root}")
        return 1

    md_paths = _collect(input_root)
    _log(f"发现 {len(md_paths)} 个 md 文件（目录：{input_root}）")
    if args.dry_run:
        _log("dry-run 模式：只统计，不下载、不写文件")
    else:
        _log(f"输出目录：{output_root}，图片目录：{images_root}，线程数：{args.workers}")

    # 解析每个 md，收集所有下载任务，并记录镜像路径
    records = []  # dict: path / rel_dir / out_md / urls
    tasks = []    # [(url, dest), ...]
    for md_path in md_paths:
        rel = os.path.relpath(md_path, input_root)
        rel_dir = os.path.dirname(rel)
        out_md = os.path.join(output_root, rel)
        with open(md_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        urls = IMG_RE.findall(content)
        records.append({
            "path": md_path,
            "rel_dir": rel_dir,
            "out_md": out_md,
            "urls": urls,
        })
        for url in urls:
            dest = os.path.join(images_root, rel_dir, _image_filename(url))
            tasks.append((url, dest))

    # 并发下载（或 dry-run 只判断是否已存在）
    statuses = []
    if tasks:
        if args.dry_run:
            statuses = ["skip" if (os.path.isfile(d) and os.path.getsize(d) > 0) else "download"
                        for _url, d in tasks]
        else:
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
                statuses = list(pool.map(lambda t: _download(t[0], t[1]), tasks))

    decisions = {dest: status for (_url, dest), status in zip(tasks, statuses)}

    # 写输出 md（相对路径基于最终输出位置 md-output/<rel>/<md>.md）
    for rec in records:
        with open(rec["path"], "r", encoding="utf-8") as fh:
            content = fh.read()

        if not args.dry_run:
            os.makedirs(os.path.dirname(rec["out_md"]), exist_ok=True)
            if rec["urls"]:
                shutil.copy2(rec["path"], rec["path"] + ".bak")

        if rec["urls"]:
            out_dir = os.path.dirname(rec["out_md"])

            def _replace(m):
                url = m.group(1)
                dest = os.path.join(images_root, rec["rel_dir"], _image_filename(url))
                status = decisions.get(dest)
                if not args.dry_run and status in ("success", "skip"):
                    rel = os.path.relpath(dest, out_dir).replace(os.sep, "/")
                    return f'<img src="{rel}"'
                return m.group(0)

            content = IMG_RE.sub(_replace, content)

        if not args.dry_run:
            with open(rec["out_md"], "w", encoding="utf-8") as fh:
                fh.write(content)

    # 统计
    stats = {"md": len(md_paths), "images": len(tasks), "success": 0, "fail": 0, "skip": 0}
    failed_list = []
    for (url, _dest), status in zip(tasks, statuses):
        if args.dry_run:
            if status == "skip":
                stats["skip"] += 1
            else:
                stats["success"] += 1
        elif status == "success":
            stats["success"] += 1
        elif status == "skip":
            stats["skip"] += 1
        else:
            stats["fail"] += 1
            failed_list.append(url)

    print("\n" + "=" * 50)
    print("统计结果")
    print("=" * 50)
    print(f"总 md 数：{stats['md']}")
    print(f"总图片数：{stats['images']}")
    if args.dry_run:
        print(f"待下载（成功预期）：{stats['success']}")
        print(f"已存在（跳过预期）：{stats['skip']}")
    else:
        print(f"成功数：{stats['success']}")
        print(f"失败数：{stats['fail']}")
        print(f"跳过数：{stats['skip']}")
    if failed_list:
        print("\n失败图片清单：")
        for url in failed_list:
            print(f"  - {url}")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())

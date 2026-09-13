# -*- coding: utf-8 -*-
"""知识点提取模块(LLM 版,正式提取)。

与 md_to_db.py(规则切分,仅测试用)相对:本模块调用 LLM(DeepSeek),
按提示词 prompts/extractor.md 从单份 md 材料中提取结构化知识点,结果带
内容 hash 缓存,内容未变时零 LLM 调用。

接口:
    extract_file(md_path, prompt_path, chapter_path, cache_path=None, force=False)
    parse_markdown_output(text)
    main(argv=None)  # CLI

CLI 例:
    python pipeline/extractor.py --input test_files/test_mk_to_db --db data/mk.db
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time

try:
    from openai import OpenAI
except ImportError:  # 延迟到调用时报错,保证 parse 单测等纯函数可用
    OpenAI = None

# LLM 参数
MODEL = "deepseek-chat"
BASE_URL = "https://api.deepseek.com"
TEMPERATURE = 0.1
MAX_MD_CHARS = 30000          # 单块材料上限(超过则分片提取,不再硬截断丢弃)
CHUNK_SIZE = 28000            # 分片块大小(留余量给章节清单/提示词,避免单块超限)
OVERLAP = 2000                # 相邻块重叠长度,防止考点被从中间切开
MIN_SINGLE = 30000            # 材料低于该长度则单次提取(等价于无分片)
RETRY_TIMES = 2               # 失败重试次数(共 3 次尝试)
RETRY_INTERVAL = 3            # 重试间隔(秒)

# 路径推断:SCRIPT_DIR = .../medical_knowledge_pipeline/pipeline
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)          # .../medical_knowledge_pipeline
PIPELINE_ROOT = os.path.dirname(ROOT_DIR)       # .../knowledge_pipeline
DEFAULT_INPUT = os.path.join(ROOT_DIR, "test_files", "test_mk_to_db")
DEFAULT_DB = os.path.join(ROOT_DIR, "data", "mk.db")
DEFAULT_PROMPT = os.path.join(ROOT_DIR, "prompts", "extractor.md")
DEFAULT_CHAPTER = os.path.join(ROOT_DIR, "subject", "外科学", "learning_chapter.txt")
DEFAULT_CACHE = os.path.join(ROOT_DIR, "data", "extract_cache.json")
DEFAULT_ENV = os.path.join(PIPELINE_ROOT, ".env")   # knowledge_pipeline/.env


def _load_env(env_path: str = DEFAULT_ENV) -> None:
    """加载 .env 文件到环境变量(不覆盖已存在的同名变量)。

    支持 KEY=VALUE 与 # 注释行;自动跳过空行。
    """
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


def _api_key() -> str:
    """获取 LLM API Key:优先 DEEPSEEK_API_KEY,其次 OPENAI_API_KEY。"""
    _load_env()
    return os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""

# markdown 标题行:# / ## / ###...
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# 去除章节名末尾的"章节"二字(如"颈部疾病章节" -> "颈部疾病")
_CHAPTER_SUFFIX = re.compile(r"章节$")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

def _log(msg: str) -> None:
    """带时间前缀输出日志。"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _read_text(path: str) -> str:
    """按 utf-8 读取文本(非法字节用替换符兜底,避免中断)。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _file_hash(path: str) -> str:
    """计算文件内容的 SHA256(十六进制)。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_cache(cache_path) -> dict:
    """加载缓存 JSON;文件不存在或损坏时返回空 dict(不抛异常)。"""
    if not cache_path or not os.path.isfile(cache_path):
        return {}
    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache_path, cache: dict) -> None:
    """写回缓存 JSON(自动建目录,中文不转义,便于人工查看)。"""
    if not cache_path:
        return
    data_dir = os.path.dirname(os.path.abspath(cache_path))
    if data_dir and not os.path.isdir(data_dir):
        os.makedirs(data_dir, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _cache_hit(cache: dict, md_path: str, md_hash: str) -> bool:
    """判断缓存中该文件是否存在且 hash 未变且含知识点。"""
    entry = cache.get(md_path)
    if not isinstance(entry, dict):
        return False
    return entry.get("hash") == md_hash and bool(entry.get("points"))


# --------------------------------------------------------------------------- #
# LLM 输出解析(纯函数)
# --------------------------------------------------------------------------- #

def parse_markdown_output(text: str) -> list[dict]:
    """解析 LLM 输出的 markdown 层级,返回结构化知识点列表。

    结构约定:
        # 章节名            -> chapter
        ## 知识点标题        -> title(遇到即开启一条新知识点)
        ### 及后续内容行      -> 归入当前知识点的 content(逐行拼接)

    - 章节名去除行首 "#" 与末尾"章节"二字;
    - content 汇总该知识点下所有非空内容行(含 ### 行、列表、正文),按 "\\n" 拼接;
    - content 为空的知识点被跳过;
    - 忽略 markdown 代码围栏(```)与首个标题前的说明性文字。

    返回:[{chapter, title, content}, ...]
    """
    points = []
    chapter = None
    current = None

    def _flush():
        nonlocal current
        if current is not None and current["content"].strip():
            points.append(current)
        current = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue  # 空行与代码围栏不进正文

        m = _HEADING.match(line)
        if m:
            level = len(m.group(1))
            body = m.group(2).strip()
            if level == 1:
                _flush()
                chapter = _CHAPTER_SUFFIX.sub("", body).strip()
            elif level == 2:
                _flush()
                current = {"chapter": chapter, "title": body, "content": ""}
            else:  # level >= 3:作为正文行(保留 ### 前缀,维持子结构)
                if current is not None:
                    current["content"] = _join(current["content"], line)
            continue

        # 非标题行:仅当前有知识点时归入正文
        if current is not None:
            current["content"] = _join(current["content"], line)

    _flush()
    return points


def _join(existing: str, line: str) -> str:
    """按行拼接正文,首行不加换行。"""
    return line if not existing else existing + "\n" + line


# --------------------------------------------------------------------------- #
# LLM 调用
# --------------------------------------------------------------------------- #

def _clip(text: str) -> str:
    """材料过长时截断并附提示(单块用,分片主流程用 _chunk_text)。"""
    if len(text) > MAX_MD_CHARS:
        return text[:MAX_MD_CHARS] + "\n\n[内容过长已截断]"
    return text


def _chunk_text(text: str, chunk_size=CHUNK_SIZE, overlap=OVERLAP):
    """把长文本切成若干有重叠的块(固定大小 + 重叠窗口,方案A)。

    不依赖材料有无标题结构,仅按字符分块。每块的结尾尽量落在最近的换行处,
    避免从一个考点中间切开;相邻块重叠 overlap 字符,兜底切开时补回上下文。

    返回 list[str]。
    """
    n = len(text)
    if n <= MIN_SINGLE:
        return [text]
    chunks = []
    start = 0
    while start < n:
        end = min(start + chunk_size, n)
        # 尽量在换行处断开,防止切开句子/考点
        if end < n:
            nl = text.rfind("\n", start, end)
            if nl > start + int(chunk_size * 0.6):      # 找到较靠后的换行才采用
                end = nl + 1
        chunks.append(text[start:end])
        if end >= n:
            break
        # 下一块起点回退 overlap,保证边界内容在邻块也出现一遍
        start = max(end - overlap, start + 1)
    return chunks


def _call_llm(system_text: str, user_text: str, filename: str) -> str:
    """调用 DeepSeek 对话补全,失败重试 RETRY_TIMES 次(间隔 RETRY_INTERVAL 秒)。

    返回模型输出的 markdown 文本;最终失败抛 RuntimeError(带文件名)。
    """
    if OpenAI is None:
        raise RuntimeError("未安装 openai,请先执行:pip install openai")
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("未设置 API Key(knowledge_pipeline/.env 中的 DEEPSEEK_API_KEY 或 OPENAI_API_KEY),无法调用 LLM")

    client = OpenAI(base_url=BASE_URL, api_key=api_key)
    messages = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_text},
    ]

    last_err = None
    for attempt in range(RETRY_TIMES + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                temperature=TEMPERATURE,
                messages=messages,
            )
            content = resp.choices[0].message.content
            if not content or not content.strip():
                raise RuntimeError("LLM 返回空内容")
            return content
        except Exception as exc:  # 网络/限流/空返回统一重试
            last_err = exc
            if attempt < RETRY_TIMES:
                _log(f"[{filename}] LLM 调用失败({exc}),{RETRY_INTERVAL}s 后重试 "
                     f"({attempt + 1}/{RETRY_TIMES})")
                time.sleep(RETRY_INTERVAL)
    raise RuntimeError(f"LLM 调用失败(文件 {filename}):{last_err}")


# --------------------------------------------------------------------------- #
# 单文件提取
# --------------------------------------------------------------------------- #

def extract_file(md_path, prompt_path, chapter_path, cache_path=None,
                 force=False) -> list[dict]:
    """提取单份 md 材料的知识点,带内容 hash 缓存。

    - md 内容 hash 与缓存一致且未 force -> 直接返回缓存(零 LLM 调用);
    - 否则组装 prompt(prompt 模板 + 章节清单 + 材料)调用 LLM,解析后写缓存。

    cache_path 为 None 时不读写缓存。返回 [{chapter, title, content}, ...]。
    """
    md_path = os.path.abspath(md_path)
    md_text = _read_text(md_path)
    md_hash = _file_hash(md_path)
    fname = os.path.basename(md_path)

    cache = _load_cache(cache_path)
    if not force and _cache_hit(cache, md_path, md_hash):
        return cache[md_path]["points"]

    prompt_text = _read_text(prompt_path)
    chapter_text = _read_text(chapter_path)

    # 长材料分片提取:每片独立调用 LLM,合并全部知识点;不再硬截断丢弃尾部。
    chunks = _chunk_text(md_text)
    points = []
    for ci, chunk in enumerate(chunks, 1):
        user_text = (f"学习章节:\n{chapter_text}\n\n"
                     f"待处理文件内容:\n{chunk}")
        tot = len(chunks)
        tag = f"分片{ci}/{tot} " if tot > 1 else ""
        _log(f"[{fname}] {tag}调用 LLM 提取(md 长度 {len(chunk)} 字符)")
        output = _call_llm(prompt_text, user_text, fname)
        points += parse_markdown_output(output)
        if tot > 1:
            _log(f"[{fname}] 分片 {ci}/{tot} 解析出 {len(points)} 条(累计)")

    if cache_path:
        cache[md_path] = {"hash": md_hash, "points": points}
        _save_cache(cache_path, cache)
    return points


# --------------------------------------------------------------------------- #
# kb_store 加载
# --------------------------------------------------------------------------- #

def _load_kb_store():
    """按文件路径加载 pipeline/kb/kb_store.py(与 md_to_db.py 一致)。"""
    kb_path = os.path.join(SCRIPT_DIR, "kb", "kb_store.py")
    if not os.path.isfile(kb_path):
        kb_path = os.path.join(SCRIPT_DIR, "kb_store.py")
    spec = importlib.util.spec_from_file_location("kb_store", kb_path)
    kb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kb)
    return kb


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    """CLI 入口:遍历目录下 *.md -> LLM 提取 -> 入库。返回 0/1。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="LLM 知识点提取(DeepSeek)并入库")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="输入 md 目录(默认 test_files/test_mk_to_db)")
    parser.add_argument("--db", default=DEFAULT_DB, help="数据库路径(默认 data/mk.db)")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="提示词路径(默认 prompts/extractor.md)")
    parser.add_argument("--chapter", default=DEFAULT_CHAPTER, help="章节清单路径(默认 subject/外科学/learning_chapter.txt)")
    parser.add_argument("--cache", default=DEFAULT_CACHE, help="缓存路径(默认 data/extract_cache.json)")
    parser.add_argument("--force", action="store_true", help="强制重提,忽略缓存")
    parser.add_argument("--dry-run", action="store_true", help="只统计不调 LLM 不写库")
    args = parser.parse_args(argv)

    input_dir = os.path.abspath(args.input)
    db_path = os.path.abspath(args.db)
    prompt_path = os.path.abspath(args.prompt)
    chapter_path = os.path.abspath(args.chapter)
    cache_path = os.path.abspath(args.cache)

    if not os.path.isdir(input_dir):
        _log(f"错误:输入目录不存在:{input_dir}")
        return 1
    for label, path in (("提示词", prompt_path), ("章节清单", chapter_path)):
        if not os.path.isfile(path):
            _log(f"错误:{label}不存在:{path}")
            return 1

    md_files = sorted(f for f in os.listdir(input_dir) if f.lower().endswith(".md"))
    if not md_files:
        _log("错误:输入目录中没有 .md 文件")
        return 1

    cache = _load_cache(cache_path)
    _log(f"发现 {len(md_files)} 个 md 文件(目录:{input_dir})")

    # --dry-run:仅按 hash 判断缓存命中,不调 LLM 不写库
    if args.dry_run:
        hits = 0
        total = 0
        for fname in md_files:
            path = os.path.join(input_dir, fname)
            h = _file_hash(path)
            if not args.force and _cache_hit(cache, path, h):
                n = len(cache[path]["points"])
                total += n
                hits += 1
                _log(f"[dry-run] {fname}: {n} 条(缓存命中)")
            else:
                _log(f"[dry-run] {fname}: 待提取(缓存未命中)")
        _log(f"[dry-run] 缓存命中 {hits}/{len(md_files)} 个文件,缓存内合计 {total} 条(未调 LLM,未写库)")
        return 0

    kb = _load_kb_store()
    kb.init_db(db_path)
    _log(f"数据库:{db_path}")
    _log(f"缓存:{cache_path}")

    total = 0
    hits = 0
    try:
        for fname in md_files:
            path = os.path.join(input_dir, fname)
            h = _file_hash(path)
            hit = not args.force and _cache_hit(cache, path, h)
            points = extract_file(path, prompt_path, chapter_path, cache_path, args.force)
            items = [
                {"content": p["content"], "chapter": p.get("chapter"),
                 "source_file": fname, "source_kb": "复习资料"}
                for p in points if p.get("content")
            ]
            kb.add_points(items)
            total += len(items)
            if hit:
                hits += 1
            tag = "缓存命中" if hit else "LLM 提取"
            _log(f"[{fname}] {tag}:提取 {len(items)} 条")
    except Exception as exc:
        _log(f"错误:{exc}")
        return 1

    stats = kb.stats()
    _log(f"完成:共提取 {total} 条,缓存命中 {hits}/{len(md_files)} 个文件")
    _log(f"库内知识点总数:{stats['knowledge_points']} 条,分布:{stats['by_kb_type']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

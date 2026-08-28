# -*- coding: utf-8 -*-
"""PDF 裁剪拼接工具：从源 PDF 中按 1-based 页码提取两段并拼接输出。"""

import os
import sys
import time

from pypdf import PdfReader, PdfWriter


# 源 PDF 文件名（含括号和逗号）
SOURCE_FILENAME = "内科学第10版 (葛均波, 王辰, 王建安) (z-library.sk, 1lib.sk, z-lib.sk).pdf"
# 输出目录与文件名
OUTPUT_DIRNAME = "pdf-output"
OUTPUT_FILENAME = "内科学测试PDF-1.pdf"
# 要提取的页码范围（PDF 1-based，含首尾），按顺序拼接
PAGE_RANGES = [
    (201, 384),
    (496, 568),
]


def log(msg: str) -> None:
    """输出带时间戳的中文日志。"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    start_time = time.time()

    # 脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    source_path = os.path.join(script_dir, SOURCE_FILENAME)
    output_dir = os.path.join(script_dir, OUTPUT_DIRNAME)
    output_path = os.path.join(output_dir, OUTPUT_FILENAME)

    # 1. 检查源文件是否存在
    if not os.path.isfile(source_path):
        log(f"错误：源 PDF 文件不存在：{source_path}")
        return 1
    log(f"源文件：{source_path}")

    # 2. 创建输出目录
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as exc:
        log(f"错误：创建输出目录失败：{output_dir}（{exc}）")
        return 1
    log(f"输出目录：{output_dir}")

    # 3. 读取源 PDF
    try:
        reader = PdfReader(source_path)
    except Exception as exc:
        log(f"错误：读取源 PDF 失败：{exc}")
        return 1

    total_pages = len(reader.pages)
    log(f"源 PDF 总页数：{total_pages}")

    # 4. 校验页码范围
    for start_1based, end_1based in PAGE_RANGES:
        if start_1based < 1 or end_1based < start_1based:
            log(f"错误：页码范围非法：{start_1based}-{end_1based}")
            return 1
        if end_1based > total_pages:
            log(f"错误：页码越界：{start_1based}-{end_1based} 超出源 PDF 总页数 {total_pages}")
            return 1

    # 5. 提取并拼接
    writer = PdfWriter()
    expected_total = 0
    for start_1based, end_1based in PAGE_RANGES:
        start_index = start_1based - 1  # pypdf 索引需减 1
        end_index = end_1based - 1
        count = end_1based - start_1based + 1
        expected_total += count
        log(f"提取页码 {start_1based}-{end_1based}（0-based 索引 {start_index}-{end_index}），共 {count} 页")
        for index in range(start_index, end_index + 1):
            writer.add_page(reader.pages[index])

    log(f"预期输出页数：{expected_total}")

    # 6. 写出结果
    try:
        with open(output_path, "wb") as fh:
            writer.write(fh)
    except OSError as exc:
        log(f"错误：写入输出文件失败：{output_path}（{exc}）")
        return 1

    # 7. 验证实际页数
    try:
        check_reader = PdfReader(output_path)
        actual_total = len(check_reader.pages)
    except Exception as exc:
        log(f"错误：验证输出文件失败：{exc}")
        return 1

    elapsed = time.time() - start_time
    log(f"输出文件：{output_path}")
    log(f"实际页数：{actual_total}（预期 {expected_total}）")
    if actual_total != expected_total:
        log("错误：实际页数与预期不一致！")
        return 1
    log(f"任务完成，耗时：{elapsed:.2f} 秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())

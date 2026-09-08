"""工作流编排入口(⑤)。

职责:
    把 extractor → kb_store → dedup → weight 串成完整流水线,一键执行。

规划接口(待实现):
    run_subject(subject, material_types=None, dry_run=False) -> dict
        处理一个科目:
        1. extractor 提取各材料类型知识点
        2. kb_store 全部写入零碎知识库 + 登记来源
        3. weight.apply_exam_hits 习题反向标记
        4. dedup 去重归并,累计重复度
        5. weight.distribute 按阈值分流
        6. 输出统计报告(提取数/归并数/重点数/零碎数)

    run_all(dry_run=False) -> dict
        遍历所有科目执行 run_subject

    status(subject=None) -> dict
        查询处理状态(已处理文件/库内条目数)

设计要点:
    - dry_run 模式只统计不写库
    - 每步可单独调用,便于调试与增量处理
    - 幂等:重复执行不产生重复条目
"""

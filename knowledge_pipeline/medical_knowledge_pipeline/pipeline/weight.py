"""权重合成 + 阈值分流模块(④)。

职责:
    综合四个信号计算每个知识点的权重,并按阈值分流到重点/零碎知识库。

信号定义:
    dup_count    重复度(跨材料出现次数)→ 归一化后乘 W_DUP
    is_exam_hit  习题命中(习题册考点反向标记)→ W_EXAM
    is_teacher   课堂重点(来源为 PPT)→ W_TEACHER
    is_big_q     大题命中(来源为复习资料)→ W_BIG_Q

规划接口(待实现):
    compute_weight(point) -> float
        单知识点权重合成(公式见 config,参数可调)

    normalize_dup(count, max_count) -> float
        重复度归一化(如 count/max_count)

    distribute(kb, threshold=None) -> dict
        遍历知识点,weight ≥ 阈值 → 移入重点库,否则留零碎库
        返回 {moved: [...], kept: [...]}

    apply_exam_hits(kb, exercises) -> int
        习题库反向标记:习题 points 命中的知识点置 is_exam_hit=1,返回命中数

设计要点:
    - 阈值先保守(0.6),宁可多进重点,不可漏考点
    - 所有系数集中在 config,便于按实际效果调参
"""

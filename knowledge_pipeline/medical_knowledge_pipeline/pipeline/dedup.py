"""相似度去重 + 重复度计数模块(③)。

职责:
    新知识点入库前,与库内已有知识点做相似度比对:
    - 相似度 ≥ 阈值 → 判定为同一知识点,dup_count + 1,归并来源
    - 否则 → 作为新知识点入库

规划接口(待实现):
    similarity(a: str, b: str) -> float
        文本相似度:difflib.SequenceMatcher 起步,Jaccard 备选

    dedup_and_merge(kb, new_points) -> list[dict]
        对一批新知识点去重归并,返回更新后的知识点列表
        (已存在的累加 dup_count 与来源,新出现的入库)

    dup_count_summary(kb) -> list[dict]
        输出重复度排序(用于权重计算输入)

设计要点:
    - 中文文本先做轻度归一化(去空白/标点差异)再比相似度
    - 阈值先保守(0.75),宁合并不分裂,防止重复条目
"""

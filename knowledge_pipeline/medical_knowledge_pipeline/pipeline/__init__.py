"""medical knowledge pipeline 代码框架(第二步·材料处理)。

模块职责:
    config     配置:路径、阈值、信号权重、LLM 调用参数
    extractor  ① 知识点提取:逐文件调用 LLM(提示词驱动),产出结构化知识点
    kb_store   ② 三知识库存储:SQLite(重点/零碎/习题),增删查改 + 来源映射
    dedup      ③ 相似度去重 + 重复度计数(difflib/Jaccard)
    weight     ④ 权重合成(重复度×习题命中×课堂重点×大题命中)+ 阈值分流
    pipeline   ⑤ 工作流编排:一键跑完整流程
"""

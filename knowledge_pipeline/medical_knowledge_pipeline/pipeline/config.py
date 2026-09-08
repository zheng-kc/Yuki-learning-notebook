"""配置模块:所有可调参数集中管理。

规划内容(待实现):
    # 路径
    SUBJECT_DIR      # subject/<科目>/
    DATA_DIR         # data/(SQLite 落库)
    PROMPTS_DIR      # prompts/(提示词文件)

    # 分流阈值
    WEIGHT_THRESHOLD # 进入重点知识库的权重阈值(默认 0.6,先保守)

    # 权重信号系数(示例,按实际效果调整)
    W_DUP       # 重复度系数
    W_EXAM      # 习题命中系数
    W_TEACHER   # 课堂重点系数
    W_BIG_Q     # 大题命中系数

    # 相似度判定
    SIM_THRESHOLD   # 视为同一知识点的相似度阈值(默认 0.75)
    SIM_ALGO        # 'difflib' | 'jaccard'(起步用 difflib)

    # LLM 调用
    LLM_MODE        # 'hermes_cli' | 'api'(调用方式待定)
    PROMPT_TEMPLATE # 知识提取提示词文件路径
"""

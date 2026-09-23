# -*- coding: utf-8 -*-
"""配置模块:所有可调参数集中管理(路径 / 权重系数 / 分流阈值 / 归一化)。

本模块是 weight.py 的唯一调参入口:改系数、改阈值、切换归一化方式都在这里,
不必改公式代码。自身不导入 kb_store / dedup,只依赖标准库。

内容:
    路径常量      DATA_DIR / SUBJECT_DIR / PROMPTS_DIR(与 dedup、extractor 同款推断)
    权重系数      W_DUP / W_EXAM / W_TEACHER / W_BIG_Q(四者之和 1.0)
    分流阈值      WEIGHT_THRESHOLD
    重复度归一化  DUP_NORM_MODE('cap' | 'max')、DUP_NORM_MAX
    展示常量      WEIGHT_HIST_STEP / CONTENT_SNIP_LEN / DEFAULT_TOP
"""

import os

# --------------------------------------------------------------------------- #
# 路径常量(与 dedup.py / extractor.py 相同的推断方式,按文件位置反推项目根)
# --------------------------------------------------------------------------- #
# SCRIPT_DIR = .../medical_knowledge_pipeline/pipeline
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)            # .../medical_knowledge_pipeline
PIPELINE_ROOT = os.path.dirname(ROOT_DIR)         # .../knowledge_pipeline

DATA_DIR = os.path.join(ROOT_DIR, "data")         # SQLite 库 + 缓存
SUBJECT_DIR = os.path.join(ROOT_DIR, "subject")   # 科目材料根目录(subject/<科目>/)
PROMPTS_DIR = os.path.join(ROOT_DIR, "prompts")   # 提示词文件

# 当前在用科目(extractor 章节清单取 subject/<科目>/learning_chapter.txt)
DEFAULT_SUBJECT_NAME = "外科学"
SUBJECT_TARGET_DIR = os.path.join(SUBJECT_DIR, DEFAULT_SUBJECT_NAME)

# 默认主库(测试库 test_chunked.db 等请用 CLI --db 显式指定,勿走默认)
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "mk.db")
# API key 所在的 .env(knowledge_pipeline/.env)
DEFAULT_ENV = os.path.join(PIPELINE_ROOT, ".env")

# --------------------------------------------------------------------------- #
# 权重系数(定稿公式,README 关键设计决策)
# --------------------------------------------------------------------------- #
# weight = W_DUP × norm(dup_count) + W_EXAM × is_exam_hit
#        + W_TEACHER × is_teacher + W_BIG_Q × is_big_q
# 四者之和必须为 1.0,保证 weight 落在 [0, 1],0.6 阈值才有意义。
W_DUP = 0.4        # 重复度:跨材料出现(方案X dup_count),权重最高——高频即重点
W_EXAM = 0.2       # 习题命中:习题册反向标记(习题库覆盖到的考点)
W_TEACHER = 0.15   # 课堂重点:来源为 PPT(老师讲过)
W_BIG_Q = 0.25     # 大题命中:复习资料里的大题考点

# 供 compute_weight(weights=...) 一次取用的系数包(键名与上面常量一致)
WEIGHT_COEFFS = {
    "W_DUP": W_DUP,
    "W_EXAM": W_EXAM,
    "W_TEACHER": W_TEACHER,
    "W_BIG_Q": W_BIG_Q,
}

# --------------------------------------------------------------------------- #
# 分流阈值
# --------------------------------------------------------------------------- #
# weight >= 该值 → 重点知识库;否则留零碎。默认 0.6:先保守,宁可多进重点不漏考点。
# 注意:单靠重复度顶格也只有 W_DUP=0.4,必须至少再叠加一个 flag 才可能过线。
WEIGHT_THRESHOLD = 0.6

# 三个知识库归属取值(kb_type 字段)
KB_TYPE_FOCUS = "重点"
KB_TYPE_MISC = "零碎"

# --------------------------------------------------------------------------- #
# 重复度归一化
# --------------------------------------------------------------------------- #
# DUP_NORM_MODE:归一化方式开关
#   'cap' 封顶法(默认):norm = min(count, DUP_NORM_MAX) / DUP_NORM_MAX
#         跨 DUP_NORM_MAX 份及以上材料即顶格 1.0,抗极端值、可解释性强
#   'max' 全局最大值法:norm = count / max(库内所有点的 count)
#         相对关系准确,但单个离群高频点会把其他点压得很低
DUP_NORM_MODE = "cap"
# 封顶法上限:跨 5 份材料即视为"足够高频"(外科学材料约 8 份,5 份已属核心考点)
DUP_NORM_MAX = 5

# --------------------------------------------------------------------------- #
# 展示 / 匹配常量(不影响权重,只影响输出与习题命中判定)
# --------------------------------------------------------------------------- #
WEIGHT_HIST_STEP = 0.1    # 分数区间直方图步长(0.0-0.1, 0.1-0.2, ...)
CONTENT_SNIP_LEN = 30     # 统计输出里内容摘要的最大字数
DEFAULT_TOP = 10          # CLI 展示最高分 Top N 的默认条数
# 习题命中匹配的最短归一化长度:过短的串(如单字)会命中大量无关知识点,
# 视作噪声跳过。仅作防呆,不改变"子串精确匹配"的判定方式。
EXAM_MATCH_MIN_LEN = 2

# --------------------------------------------------------------------------- #
# 其他模块共用常量(与 dedup.py 的实现保持一致,便于后续统一收口)
# --------------------------------------------------------------------------- #
SIM_THRESHOLD = 0.85      # dedup 判为同一知识点的相似度阈值(见 dedup.DEFAULT_THRESHOLD)
EMB_THRESHOLD = 0.85      # dedup 语义层余弦阈值(见 dedup.DEFAULT_EMBED_THRESHOLD)

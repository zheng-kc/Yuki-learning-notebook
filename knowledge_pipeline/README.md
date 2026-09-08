# knowledge_pipeline · Yuki 1.5 知识管线

> 第二步·材料处理:多文件知识点提取 → 三知识库(重点/零碎/习题)→ 权重分流
> 对应 frontend.py「第二步·任务1」设计;详细方案见 Obsidian《第二步-材料处理方案-三知识库.md》

## 目录结构

```
knowledge_pipeline/
├── README.md                        # 本文件:总览
├── cs_knowledge_pipeline/           # 计算机学科管线(框架占位,待建设)
└── medical_knowledge_pipeline/      # 医学学科管线
    ├── README.md                    # 医学管线说明
    ├── pipeline/                    # 第二步核心代码框架
    │   ├── config.py                # 配置:路径/阈值/信号权重
    │   ├── extractor.py             # ① 知识点提取(逐文件调用 LLM)
    │   ├── kb_store.py              # ② 三知识库存储(SQLite)
    │   ├── dedup.py                 # ③ 相似度去重 + 重复度计数
    │   ├── weight.py                # ④ 权重合成 + 阈值分流
    │   └── pipeline.py              # ⑤ 工作流编排入口
    ├── data/                        # 运行时数据(SQLite 落库,不入 git)
    ├── prompts/                     # 提示词(用户动态维护)
    │   ├── System_Prompt.txt
    │   ├── SubTask1.txt             # 高频考点提取
    │   ├── SubTask2.txt             # 名词解释生成
    │   └── SubTask3.txt             # 简答题/论述题生成
    └── subject/                     # 科目材料目录(按四类材料分)
        └── 内科学/
            ├── PPT/                 # 课堂 PPT(md)
            ├── 习题册/              # 习题/试卷(md)
            ├── 复习资料/            # 复习资料/重点总结(md)
            └── 课本/                # 书本全文(md)
```

## 数据流

```
subject/内科学/{PPT,习题册,复习资料,课本}/  (材料 md)
   │  pipeline.extractor  逐文件提取知识点
   ▼
零碎知识库(SQLite)
   │  pipeline.dedup      跨文件相似度去重 + 重复度计数
   │  pipeline.weight     信号合成:重复度×习题命中×课堂重点×大题命中
   ▼
重点知识库 / 零碎知识库(分流)
   │  (后续第三步)prompts 读库生成名词解释/简答题/复习重点
   ▼
Obsidian 笔记
```

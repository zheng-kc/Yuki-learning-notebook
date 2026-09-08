# cs_knowledge_pipeline · 计算机学科知识管线

> 框架占位,待建设。
> 结构对齐 `medical_knowledge_pipeline/`(pipeline 代码 + subject 材料 + prompts)。

## 规划目录

```
cs_knowledge_pipeline/
├── pipeline/          # 复用医学管线的 extractor/kb_store/dedup/weight(抽公共层后共享)
├── prompts/           # 计算机学科提示词(按实际输出动态调整)
└── subject/
    └── <科目>/        # 四类材料:PPT / 习题册 / 复习资料 / 课本
```

## 说明

- 第二步的知识处理逻辑(提取/去重/加权/入库)与学科无关,后续抽取为公共模块后两管线共用
- 学科差异主要在 prompts 与科目目录,故 cs 管线主体为占位

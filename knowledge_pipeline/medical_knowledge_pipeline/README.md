# medical_knowledge_pipeline · 医学知识管线

> 医学学科知识处理:材料 → 三知识库 → 权重分流 → 笔记生成素材
> Prompts 由用户根据实际输出动态调整;pipeline 代码只搭框架,待逐模块实现。

## 结构

```
medical_knowledge_pipeline/
├── pipeline/          # 第二步核心代码框架(五个模块,职责见各文件 docstring)
├── data/              # 运行时数据:knowledge.db(SQLite 三知识库),不入 git
├── prompts/           # 提示词(用户维护):System_Prompt / SubTask1~3
└── subject/           # 科目材料:subject/<科目>/{PPT,习题册,复习资料,课本}
```

## 材料约定

- 各科目四类材料(md)放入 `subject/<科目>/` 对应子目录
- 材料来源:第一步 OCR 产物(pdf-tools/pdf-to-md/md-output)或手动整理
- 书本作为参考书最后处理(先索引,按考点反查)

## 处理节奏(70 分原则)

1. 习题册 → 考点清单(P0,最先)
2. PPT → 课堂重点标记(P1)
3. 复习资料 → 大题方向标记(P2)
4. 课本 → 章节索引 + 考点反查补全(P3,最后)

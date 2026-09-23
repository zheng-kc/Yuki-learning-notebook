# medical_knowledge_pipeline · 医学知识管线

> 医学学科知识处理:材料 → 三知识库 → 权重分流 → 笔记生成素材
> **本 README 用于开新对话时接续任务**,包含当前进度、已完成模块、测试结果、下一步。

---

## 当前进度(2026-09-13 快照)

### ✅ 已完成

| 模块 | 状态 | 说明 |
|---|---|---|
| `pipeline/kb/kb_store.py` | ✅ 完成 | 三知识库 SQLite 数据层(重点/零碎共用 knowledge_points 表 + kb_type 字段,习题独立 exercises 表,来源映射 knowledge_sources 表)。已实测通过;本会话新增 `set_dup(point_id, value)`(方案X 覆盖去重计数用) |
| `pipeline/md_to_db.py` | ✅ 完成 | md → SQLite 规则切分入库(仅测试用,非 LLM)。真实测试 2680 条入库成功 |
| `pipeline/dedup.py` | ✅ 完成 | **双层次去重 + 重dup计数**:(1) 算法层 difflib 阈值0.85;(2) **语义层 Qwen embedding 余弦聚类(阈值0.85)+ 方案X**:dup_count = 去重来源文件数(跨材料数)。实测 LLM 220 条 → 171 条,去重率 22.3%,高频考点纯净可用(kb_store.set_dup 落库) |
| `pipeline/extractor.py` | ✅ 完成 | LLM 知识点提取(openai sdk 直调 DeepSeek + 结果缓存)。实测 8 份材料共提取(含分片重跑);**新增长材料分片提取(方案A:固定块28K+重叠2K,不丢被截断尾部)** |
| `pipeline/weight.py` | ✅ 完成 | **权重合成 + 阈值分流**(Claude Code 委托实现)。4 信号加权(0.4×norm(dup)+0.2×exam+0.15×teacher+0.25×big_q)≥0.6 进重点;归一化 cap 封顶法(K=5)/max 全局法可切;实现 distribute/apply_exam_hits/normalize_dup/compute_weight + CLI(--dry-run 安全)。单库事务写库。实测通过(SPEC_weight.md) |
| `pipeline/config.py` | ✅ 完成 | 参数集中管理(路径/系数/阈值/归一化开关),weight 的唯一调参入口 |

### 🔲 待完成

| 模块 | 状态 | 说明 |
|---|---|---|
| `pipeline/pipeline.py` | 🔲 未开始 | 工作流编排(一键跑完整流程) |
| 端到端联调 | 🔲 未开始 | 真实材料 extract→dedup→weight 全流程验证 |

---

## 关键文件位置

```
knowledge_pipeline/
├── .env                      # API 配置:OPENAI_API_KEY(DeepSeek) + DASHSCOPE_API_KEY(Qwen embedding)
└── medical_knowledge_pipeline/
    ├── pipeline/
    │   ├── kb/kb_store.py    # SQLite 数据层(注意在 kb/ 子目录下!含 set_dup)
    │   ├── md_to_db.py       # 规则切分入库(测试用)
    │   ├── dedup.py          # 双层次去重(算法 + Qwen embedding 方案X)
    │   └── extractor.py      # LLM 提取 + 缓存 + 长材料分片
    ├── prompts/
    │   ├── extractor.md      # 单份材料提取知识点(extractor 用)
    │   ├── exam_knowledge_extractor.txt  # 高频考点提取(第三步用)
    │   ├── SubTask1~3.txt, System_Prompt.txt  # 第三步笔记生成用
    ├── subject/外科学/       # 材料(learning_chapter.txt = 章节清单)
    ├── test_files/test_mk_to_db/  # 测试材料(8 份外科学 md)
    ├── data/                 # SQLite 库 + 缓存 + embedding 向量
    ├── SPEC_dedup_embedding.md / TEST_LOG_*.md   # 开发规格 + 测试记录
    └── README.md             # 本文件
```

---

## 当前数据与测试结果

### 数据文件
- `data/mk.db`:原外科学测试库(2680 条规则切分 + 曾追加 220 条 LLM 提取)
- `data/test_chunked.db`:extractor 分片重跑独立库(220 条 LLM 提取 → 分片结果 204 条)
- `data/extract_cache.json`:extractor 提取缓存(8 份材料,含 hash 绑定)
- `data/emb_vecs.json`:220 条 LLM 知识点的 Qwen embedding 向量(1024 维,dedup 语义层复用)
- 真实库 `data/knowledge.db` 尚未创建(db_path 默认指向它,但测试都用了独立/测试库)

### 实测证据
1. **kb_store**:建表/CRUD/来源映射/权重/归属变更/统计/事务回滚/级联删除 — 通过;`set_dup` 新增
2. **md_to_db**:8 份材料规则切分 2680 条入库(测试用,碎片多)
3. **dedup semantic**:用 LLM 提取的 220 条测试 → Qwen embedding 余弦去重(阈值0.85)→ 171 条,去重率 22.3%;方案X dup_count=跨材料数,落库核对全对(肠梗阻6/疝5/乳腺途径5/麦氏点4)
4. **dedup 对比**:原先算法层高频 Top 9/10 是碎片("正确答案:C");现在 LLM+embedding 高频 Top 全为真实考点 → 高频判定地基可用
5. **extractor**:8 份材料 LLM 提取(重点整理缓存命中零重复调用);长材料分片(外科学(普外) 83KB 字符3.29万 拆2块,捞回被截断尾部考点)

---

## 关键设计决策(已定稿,勿改)

1. **三知识库 = 1 张主表(knowledge_points)+ kb_type 中文值(零碎/重点/习题)**,不建 3 张物理表
2. **去重原理**:不去重,"高频计数"无法计算(同一考点在不同材料表述不同,不归并则各自 count=1 全漏)。去重是高频判定的地基
3. **稳定性三层保障**:结果缓存(hash 绑定,重跑零 LLM)→ 结构化输出 + 低温(0.1)→ 库收敛(dedup 归并)。**重跑稳定靠缓存,不靠 LLM**(--force 重提会因 LLM 粒度浮动)
4. **处理顺序**:习题册(P0)→ PPT(P1)→ 复习资料(P2)→ 书本(P3,先索引后反查)
5. **LLM 调用**:openai sdk 直调。提取用 DeepSeek(base_url=api.deepseek.com, model=deepseek-chat,temperature=0.1);语义去重用 Qwen embedding(base_url=dashscope,compatible-mode/v1, model=qwen3.7-text-embedding, batch≤20)。key 从 `knowledge_pipeline/.env` 读
6. **extractor 缓存位置**:`data/extract_cache.json`,结构 `{md_path: {hash, points}}`
7. **长材料分片(方案A)**:extractor 材料字符>3万 → 固定块 28K + 重叠 2K + 换行对齐,逐块提取合并。分片按字符数(非字节),中文 1 字=3 字节
8. **方案X(去重计数)**:dedup 语义层 dup_count = 去重后 source_file 数(跨材料数),由 kb_store.set_dup 覆盖写库(非 incr_dup 累加)

---

## 下一步(接续从这里开始)

### 立即可做
1. **写 `pipeline.py`**(编排):extract → 全入零碎库 → 习题反向标记 → dedup 去重 → 加权分流 → 统计报告
   - weight.py + config.py 已完成(四信号加权、阈值 0.6、归一化 cap 可切),见 `SPEC_weight.md`

### 之后
3. 用真实材料端到端跑通完整流水线,验证 weight 高频分流效果
4. 确认 `data/` 与 `knowledge_pipeline/.env` 已被 .gitignore 排除(不入 Git)

### 注意
- kb_store.py 在 `pipeline/kb/` 子目录,extractor/md_to_db/dedup 都用 importlib 按路径加载它
- `data/` 和 `knowledge_pipeline/.env` 已被 .gitignore 排除(不入 Git)
- **embedding 需阿里 DASHSCOPE_API_KEY**(存在 .env),dedup 语义层依赖它;向量缓存 `data/emb_vecs.json` 条数一致时零 API 调用
- 脏文件(外科学（普外）.md / 普外科 .md)含 cid 乱码,提取效果可能受影响
- extractor 稳定性:重跑走缓存即稳定;`--force` 重提会有 LLM 粒度波动(非 bug,靠 dedup 收敛)

---

## 外部参考

- Obsidian:`D:\AgentManageKnowledgeBase\Yuki 1.5开发\_meta\第二步-材料处理方案-三知识库.md`(完整方案 v0.3)
- 项目:Yuki-learning-notebook(GitHub,已推送到 main)
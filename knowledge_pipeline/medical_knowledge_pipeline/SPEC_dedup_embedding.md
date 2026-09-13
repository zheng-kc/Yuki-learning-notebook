# 任务:给 dedup.py 加 Qwen embedding 语义去重层 + 方案X dup_count 统计

我要在这个项目里做医学知识库去重。请你(Claude Code)修改两个 Python 文件,
实现「算法+语义 embedding 两层去重」和「方案X 的重复度统计」。改完自测并回报。

## 绝对路径(Windows,注意用户名含单引号 z'k'c)
- 项目根: C:\Users\z'k'c\Desktop\Yuki_1.5\knowledge_pipeline\medical_knowledge_pipeline
- 待改 1: <项目根>\pipeline\dedup.py   (相似度去重主模块)
- 待改 2: <项目根>\pipeline\kb\kb_store.py  (SQLite 数据层)
- 缓存: <项目根>\data\emb_vecs.json   (已有 220 条知识的 embedding 向量,勿删勿动)
- 原文缓存: <项目根>\data\extract_cache.json  (220 条知识点原文,勿删勿动)
- key: <项目根上一级>\knowledge_pipeline\.env  (含 DASHSCOPE_API_KEY)
- 库: <项目根>\data\mk.db  (现成测试库,但主库不要改!测试用临时 kub)

## 背景:现状
- dedup.py 已实现 SequenceMatcher 字符相似度去重(阈值0.85),通过 kb_store 接口操作库。
- kb_store.py 是标准 sqlite 数据层,有 knowledge_points 表(含 dup_count 列)和 knowledge_sources 来源表。
- kb_store 现有 incr_dup(point_id):dup_count+1(累加)。缺一个"把 dup_count 设为指定值"的方法。

## 目标一:kb_store.py 增加 set_dup 方法(方案X必需)
加一个公开方法,模式照抄 incr_dup,但把 dup_count 设置成指定值而非累加:
    def set_dup(point_id, dup_count):  UPDATE knowledge_points SET dup_count=? WHERE id=?

## 目标二:dedup.py 增加「语义 embedding 去重层」
不破坏现有 SequenceMatcher 逻辑(保留为默认或第一层),新增/增强一个语义层:

### Qwen embedding 调用(已实测可用,给我用的参数)
- OpenAI 兼容端点: base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
- model = "qwen3.7-text-embedding",需从 <项目根上一级>\knowledge_pipeline\.env 读 DASHSCOPE_API_KEY
- 批量上限:每次 input 最多 20 条(Ali 限制),超了报 400
- 返回 1024 维向量

### 你需要实现的核心(方案X的 dup_count 统计)
方案X语义:「同一知识点的 dup_count = 该知识点来源的【去重 source_file 数】」,
即一个知识点出现在几份不同材料里。不是"被归并了几条",而是"跨了几份材料"。

具体做法(通过 kb_store 接口,不直接写裸 SQL 到库里,除非必要):
1. 聚类:用 embedding 余弦相似度 >= 阈值(0.85)判为同一知识点,贪心/并查集聚成簇。
2. 每个簇内:收集主条目 + 所有被归并条目的 sources(source_file 列表),取去重后的
   source_file 集合大小 = 该簇的 dup_count。例如乳腺淋巴途径在 5 份材料 -> dup_count=5。
3. 归并写库(复用/参考现有 _apply_plan 的"迁移来源到主条目 + 删除被归并"逻辑):
   - 主条目保留(库里 id 不动)
   - 被归并条目的来源映射都迁到主条目(add_source)
   - 主条目 dup_count 用 set_dup 设为"去重来源文件数"(而非 incr_dup 累加!)
   - 被归并条目 delete_point 删除
   - 建议也用提交拦截包装保证单事务(参考现有 _CommitInterceptor)

### 与现有 SequenceMatcher 的关系
建议:embedding 语义层作为新入口/增强;保留 SequenceMatcher 的逻辑供回退。
可以加一个参数如 use_embedding 或把语义层做成 dedup() 的可选分支。保持向后兼容。

## 测试约束(重要)
- 绝不修改 <项目根>\data\mk.db 主库、绝不删除 extract_cache.json / emb_vecs.json。
- 自测请用临时 sqlite 库(如 Tempfile),灌入 emb_vecs.json 对应的 220 条文案 + 来源,
  跑语义去重,验证:
    1. 归并正确(乳腺淋巴输出途径应合并成一条、duplicate 计数=跨材料数)
    2. dup_count = 去重来源文件数(方案X),不是被归并条数
    3. 完成后主库 mk.db 未被触碰

## 回报格式
- 改了哪两个文件的哪些位置
- 自测结果:跑了多少条、归并成多少簇、去重率多少、抽查 2-3 个簇的 dup_count 是否符合方案X
- 确认 mk.db 未被修改

## 注意
- Windows 环境,文件读写用 utf-8
- 项目路径含单引号,命令行里处理引号要小心
- 只改上面列的两个文件,不要动其他文件
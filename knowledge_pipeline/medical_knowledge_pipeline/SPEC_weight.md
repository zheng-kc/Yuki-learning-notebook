# 任务:实现 weight.py + config.py(权重合成 + 阈值分流)

请你(Claude Code)在已知的医学知识管线里,实现两个文件的**完整功能**,替换现有占位骨架。
公式、系数、阈值已由 README 定稿,归一化已有建议默认值,照本 SPEC 实现即可。改完自测(隔离临时库),并回报。

## 绝对路径(Windows,注意用户名含单引号 z'k'c)
- 项目根: `C:\Users\z'k'c\Desktop\Yuki_1.5\knowledge_pipeline\medical_knowledge_pipeline`
- 待实现 1: `<项目根>\pipeline\config.py`(配置集中;现仅 docstring 骨架)
- 待实现 2: `<项目根>\pipeline\weight.py`(权重合成 + 分流;现仅 docstring 骨架)
- 只依赖已实现的数据层: `<项目根>\pipeline\kb\kb_store.py`(已有 update_weight / set_kb_type / set_flags / get_all_exercise_points / get_points 等)
- 参考已完成的同类模块风格: `<项目根>\pipeline\dedup.py`(看 importlib 加载 kb_store、CLI、事务写法、文档字符串风格)

## 背景:现状
- 三知识库共一张主表 `knowledge_points`,字段含 `weight`、`dup_count`、`is_exam_hit`、`is_teacher`、`is_big_q`、`kb_type`、`source_file`、`chapter`。
- dedup(已实现)把 `dup_count` 填为**跨材料来源数**(方案X,同一考点出现在几份材料)。
- weight 要在 dedup 之后跑,给每个知识点算综合权重,并按阈值分流到 重点/零碎 两库。
- `weight.py` 的骨架 docstring 已定义好目标接口(compute_weight / normalize_dup / distribute / apply_exam_hits),请按它实现,可微调签名但保持核心语义。

## 已定稿公式(勿改,除非有充分理由并在回报里说明)
```
weight = W_DUP × norm(dup_count) + W_EXAM × is_exam_hit
       + W_TEACHER × is_teacher + W_BIG_Q × is_big_q
默认系数: W_DUP=0.4, W_EXAM=0.2, W_TEACHER=0.15, W_BIG_Q=0.25   (四者之和=1.0)
分流阈值: weight >= WEIGHT_THRESHOLD(默认 0.6) -> 重点 知识库;否则留 零碎
```
- is_exam_hit / is_teacher / is_big_q 是 0/1 标记(直接乘系数)。
- 系数集中到 `config.py`,便于用户调参。

## 归一化 norm(dup_count)(关键,建议默认用封顶法)
库内 dup_count 是绝对次数(1~N)。默认用**封顶法**(config 可换):
```
norm = min(dup_count, DUP_NORM_MAX) / DUP_NORM_MAX
DUP_NORM_MAX 默认 = 5(跨 5 份及以上材料即顶格 1.0)
```
同时在 config 提供可切换的**全局最大值法**(`dup_count / max(所有点 dup_count)`)。
设计成 config 里一个开关/函数选择,让用户日后切。`normalize_dup(count, ...)` 必须先保证返回 0~1。

## 需要实现的功能与接口

### config.py
把以下集中管理(命名清晰,均带注释说明含义与默认值来源):
- 路径常量:`DATA_DIR`(data/)、`SUBJECT_DIR`、`PROMPTS_DIR`(尽量与 dedup/extractor 的路径推断方式一致,不强求大改)
- 权重系数:`W_DUP / W_EXAM / W_TEACHER / W_BIG_Q`(默认 0.4/0.2/0.15/0.25)
- 分流阈值:`WEIGHT_THRESHOLD`(默认 0.6)
- 归一化:`DUP_NORM_MODE`('cap' 或 'max',默认 'cap')、`DUP_NORM_MAX`(默认 5)
- 其余可用常量按需补充。

### weight.py
实现 3 个核心函数 + 1 个习题标记函数 + CLI:
1. `normalize_dup(dup_count, mode=None, norm_max=None)` -> float
   - 'cap': `min(dup_count, norm_max)/norm_max`
   - 'max': `dup_count / global_max`(此时需把全局 max 传入或由调用方计算后传入)
   - 默认取 config 的 DUP_NORM_MODE/DUP_NORM_MAX。
   - guard: dup_count<=0 返回 0;normalize 后 clamp 到 [0,1]。
2. `compute_weight(point, weights=None) -> float`
   - 输入是 `kb_store.get_points()` 返回的一条 dict(含 dup_count/is_exam_hit/is_teacher/is_big_q)。
   - 按公式返回 0~1 分。
   - weights 缺省用 config 系数。
   - 归一化需要的 norm_max / mode 从 config 读(或作为可选参数)。
3. `distribute(kb, threshold=None, dry_run=False) -> dict`
   - 遍历库内所有知识点(get_points()),逐个 compute_weight:
     - `update_weight(point['id'], w)` 写回 weight 字段(幂等覆盖,不累加)。
     - 若 weight >= threshold: `set_kb_type(point['id'], '重点')` 移入重点;否则 '零碎'(若无必要可只对重点调用)。
   - dry_run=True 时:只打印/返回统计,不写库(不 update_weight、不 set_kb_type)。
   - 返回 dict:
     ```
     { total, moved: [{id, weight, chapter, content_snip}...], kept_count,
       moved_count, moved_ratio, weight_hist(分数区间分布) }
     ```
   - 只通过 kb_store 公开接口操作,不直接写裸 SQL。
4. `apply_exam_hits(kb, exercises=None) -> int`
   - 读习题库 or 传入 exercises;对每条习题的 points(json 数组)里每个知识点字符串:
     - 用归一化包含匹配(把字符串去空白/全角转半角后,看该知识点是否作为子串出现在任一 knowledge_point 的 content 里)。
     - 命中则 `set_flags(point_id, is_exam_hit=1)`。
   - 返回标记命中条数。匹配用轻量归一化(可复用 dedup.normalize 的思路,独立实现/import)。
   - 起步用"字符串包含"精确匹配即可,不求语义匹配(留给后续优化)。
5. CLI(参照 dedup.py 的 argparse 风格):
   - `weight.py --db <path>` 对指定库跑 distribute(默认**不写**前面 3 个 flag,但会写 weight 与 kb_type;除非 --dry-run)
   - `--dry-run` 只预演统计不写库(强烈建议 CLI 默认提示先跑 dry-run)
   - `--threshold <float>` 覆盖阈值
   - `--apply-exam-hits` 先跑 apply_exam_hits 再 distribute
   - 输出统计:总数/进重点数/留零碎数/重点占比/分数分布/最高分 Top N。

## 与 kb_store 的衔接
- 通过 importlib 加载 `pipeline/kb/kb_store.py`(与 dedup._load_kb_store 同样的方式,含退化为同目录 kb_store.py)。
- `init_db(db_path)`、`get_points()`、`update_weight`、`set_kb_type`、`set_flags` 都已存在,直接复用。

## 测试约束(重要)
- **绝不修改 `<项目根>\data\mk.db` 主库**、**绝不删除或改动 `data/extract_cache.json` / `data/emb_vecs.json` 缓存**。
- weight 公式与归一化是纯函数:用临时 sqlite 库(Tempfile 或 data/ 下临时名)灌入**有代表性**的数据自测,覆盖:
  1. 归一化:'cap' 模式 min(3,5)/5=0.6、min(7,5)/5=1.0(封顶);'max' 模式相对关系正确;clamp 到 0~1。
  2. compute_weight:构造不同 dup_count + flag 组合,断言分值与公式手算一致。
  3. 「单靠重复度到不了 0.6」:dup 满(0.4)但无任何 flag -> 0.4 < 0.6 留零碎(预期,勿当 bug)。
  4. 组合达线:dup 满(0.4) + is_big_q(0.25)=0.65 -> 进重点。
  5. distribute 分流:造 ≥1 条应进重点、≥1 条应留零碎的样例,验证 moved/kept 与 kb_type 落库正确;再验证 dry_run=True 后库内 weight/kb_type 未被改动。
  6. apply_exam_hits:造一条习题 points 包含某知识点正文的子串,验证对应 knowledge_point 的 is_exam_hit 被置 1。
- 自测完成后确认 `<项目根>\data\mk.db`、`data/extract_cache.json`、`data/emb_vecs.json` 三个文件未被触碰。

## 已知当前测试库形态(供你参考,不是 bug)
- `data/test_chunked.db`(204 条):dup_count 全=1、三个 flag 全=0、weight=0。在这个库上跑 weight 会全得低分(最高 0.4)、无一条进重点——**这是预期行为**,因为它是分片重跑新库、还没跑过 dedup。验证公式逻辑请用你自己造的临时库数据。

## 回报格式
- 改了哪两个文件的哪些位置(函数清单)。
- 自测结果:每个用例的输入/期望/实际断言结果;distribute 分流样例的权重的 kb_type 落库核对。
- 确认 mk.db 主库 + extract_cache.json + emb_vecs.json 未被修改。
- 若你调整了任何系数/阈值/归一化默认值,必须说明理由。

## 注意
- Windows 环境,文件读写 utf-8;脚本入口 `if __name__ == "__main__": sys.exit(main())`。
- 项目路径含单引号,命令行引号注意。
- 只动 `pipeline/config.py` 和 `pipeline/weight.py` 两个文件(可新增临时测试用例脚本,放 data/ 或临时目录,不留下永久无关文件);不要动 dedup/extractor/kb_store 已实现逻辑。若确需小幅修改 kb_store,先说明理由。
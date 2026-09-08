"""三知识库存储模块(②)。

职责:
    SQLite 落库:零碎知识库(全部知识点先入)/ 重点知识库(高分流)/ 习题知识库(考点)。
    维护知识点主表 + 来源映射表 + 习题表。

规划表结构(待实现):
    knowledge_points  知识点主表
        id/content/chapter/source_file/kb_type/weight/
        dup_count/is_exam_hit/is_teacher/is_big_q/created_at
    knowledge_sources 来源映射(多对多)
        id/point_id/source_file/source_kb
    exercises         习题库
        id/question/answer/chapter/points/source_file

规划接口(待实现):
    init_db(db_path)                建表
    add_point(point) -> id          写入知识点(初始进零碎库)
    add_source(point_id, src)       登记来源
    add_exercise(exercise)          习题入库
    update_weight(point_id, w)      更新权重
    move_to_key(point_id)           移入重点知识库
    query(kb_type, chapter=None)    查询
    find_similar(content, thresh)   相似知识点查找(供 dedup 用)
"""

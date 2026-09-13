"""三知识库 SQLite 数据层(②)。

三知识库 = 重点知识库 + 零碎知识库(共用 knowledge_points 表,用 kb_type 区分)
           + 习题知识库(exercises 表)。
另有 knowledge_sources 来源映射表,记录知识点↔来源文件/材料类型的多对多关系。

纯标准库实现(sqlite3),无外部依赖。默认数据库文件:
    <medical_knowledge_pipeline>/data/knowledge.db
由本文件路径自动推断,data 目录不存在时自动创建。
"""

import json
import os
import sqlite3

# 模块级单例连接(懒初始化缓存)与当前连接对应的库文件路径
_conn = None
_db_path = None

# 默认数据库绝对路径:.../medical_knowledge_pipeline/data/knowledge.db
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "knowledge.db",
)

# 建表 DDL(幂等:IF NOT EXISTS)
_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    chapter TEXT,
    source_file TEXT,
    kb_type TEXT NOT NULL DEFAULT '零碎',
    weight REAL DEFAULT 0,
    dup_count INTEGER DEFAULT 1,
    is_exam_hit INTEGER DEFAULT 0,
    is_teacher INTEGER DEFAULT 0,
    is_big_q INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS knowledge_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    point_id INTEGER,
    source_file TEXT,
    source_kb TEXT,
    FOREIGN KEY (point_id) REFERENCES knowledge_points(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT,
    chapter TEXT,
    points TEXT,
    source_file TEXT
);
"""


# --------------------------------------------------------------------------- #
# 连接管理
# --------------------------------------------------------------------------- #

def init_db(db_path=None):
    """初始化数据库:自动建 data 目录并执行幂等建表,返回连接。

    db_path 缺省时使用默认路径(DEFAULT_DB_PATH);重复调用会关闭旧连接、
    切换到新路径重建(表结构已存在则跳过)。
    """
    global _conn, _db_path
    path = os.path.abspath(db_path) if db_path else DEFAULT_DB_PATH
    data_dir = os.path.dirname(path)
    if data_dir and not os.path.isdir(data_dir):
        os.makedirs(data_dir, exist_ok=True)
    # 关闭旧连接,避免句柄泄漏
    if _conn is not None:
        try:
            _conn.close()
        except sqlite3.Error:
            pass
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_DDL)
    conn.commit()
    _conn = conn
    _db_path = path
    return conn


def get_conn():
    """返回模块级单例连接;未初始化时按默认路径自动建库。"""
    global _conn
    if _conn is None:
        init_db()
    return _conn


def close():
    """关闭模块级单例连接并清空缓存。"""
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except sqlite3.Error:
            pass
        _conn = None


# --------------------------------------------------------------------------- #
# 知识点(knowledge_points)
# --------------------------------------------------------------------------- #

def add_point(content, chapter=None, source_file=None, source_kb=None):
    """新增一条知识点,默认归属零碎库;带来源时同时登记 sources 映射。

    单条写操作,内部完成后统一提交。返回新知识点 id。
    """
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO knowledge_points (content, chapter, source_file) "
        "VALUES (?, ?, ?)",
        (content, chapter, source_file),
    )
    point_id = cur.lastrowid
    if source_file:
        _add_source_row(conn, point_id, source_file, source_kb)
    conn.commit()
    return point_id


def add_points(points):
    """批量新增知识点,整体用事务包裹(任一失败全部回滚)。

    points: list[dict],每项至少含 content,可选 chapter/source_file/source_kb。
    返回新知识点 id 列表(与入参顺序一致)。
    """
    conn = get_conn()
    ids = []
    try:
        for item in points:
            cur = conn.execute(
                "INSERT INTO knowledge_points (content, chapter, source_file) "
                "VALUES (?, ?, ?)",
                (item.get("content"), item.get("chapter"), item.get("source_file")),
            )
            point_id = cur.lastrowid
            ids.append(point_id)
            if item.get("source_file"):
                _add_source_row(
                    conn, point_id,
                    item.get("source_file"), item.get("source_kb"),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ids


def get_point(point_id):
    """按 id 查询单条知识点,返回 dict;不存在返回 None。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM knowledge_points WHERE id = ?", (point_id,)
    ).fetchone()
    return dict(row) if row else None


def get_points(kb_type=None, chapter=None):
    """查询知识点列表,可按归属(kb_type)和/或章节筛选,按 id 升序。"""
    conn = get_conn()
    sql = "SELECT * FROM knowledge_points WHERE 1 = 1"
    args = []
    if kb_type:
        sql += " AND kb_type = ?"
        args.append(kb_type)
    if chapter:
        sql += " AND chapter = ?"
        args.append(chapter)
    sql += " ORDER BY id"
    rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def update_weight(point_id, weight):
    """更新指定知识点的综合权重(weight,0~1)。"""
    conn = get_conn()
    conn.execute(
        "UPDATE knowledge_points SET weight = ? WHERE id = ?",
        (weight, point_id),
    )
    conn.commit()


def set_kb_type(point_id, kb_type):
    """变更知识点归属库(kb_type):零碎/重点/习题。"""
    conn = get_conn()
    conn.execute(
        "UPDATE knowledge_points SET kb_type = ? WHERE id = ?",
        (kb_type, point_id),
    )
    conn.commit()


def set_flags(point_id, is_exam_hit=None, is_teacher=None, is_big_q=None):
    """按需更新知识点标记位,仅修改传入的非 None 字段。

    is_exam_hit: 习题库命中 0/1;is_teacher: 课堂重点 0/1;is_big_q: 大题命中 0/1。
    """
    conn = get_conn()
    updates = []
    args = []
    flag_map = {
        "is_exam_hit": is_exam_hit,
        "is_teacher": is_teacher,
        "is_big_q": is_big_q,
    }
    for column, value in flag_map.items():
        if value is not None:
            updates.append("%s = ?" % column)
            args.append(1 if value else 0)
    if updates:
        args.append(point_id)
        conn.execute(
            "UPDATE knowledge_points SET %s WHERE id = ?" % ", ".join(updates),
            args,
        )
        conn.commit()


def incr_dup(point_id):
    """指定知识点的重复度(dup_count)+1,记录跨文件出现次数。"""
    conn = get_conn()
    conn.execute(
        "UPDATE knowledge_points SET dup_count = dup_count + 1 WHERE id = ?",
        (point_id,),
    )
    conn.commit()


def set_dup(point_id, dup_count):
    """把指定知识点的重复度(dup_count)直接设为指定值(非累加)。

    方案X 语义下 dup_count = 该知识点来源的去重 source_file 数,
    归并后由调用方算出该值并用本方法覆盖写入(区别于 incr_dup 的累加)。
    """
    conn = get_conn()
    conn.execute(
        "UPDATE knowledge_points SET dup_count = ? WHERE id = ?",
        (dup_count, point_id),
    )
    conn.commit()


def delete_point(point_id):
    """删除知识点主记录,并级联清理其来源映射(同时显式删 sources 兜底)。"""
    conn = get_conn()
    conn.execute("DELETE FROM knowledge_points WHERE id = ?", (point_id,))
    conn.execute("DELETE FROM knowledge_sources WHERE point_id = ?", (point_id,))
    conn.commit()


# --------------------------------------------------------------------------- #
# 来源映射(knowledge_sources)
# --------------------------------------------------------------------------- #

def _add_source_row(conn, point_id, source_file, source_kb):
    """向指定连接插入一条来源映射(供事务内复用,不自行提交)。"""
    conn.execute(
        "INSERT INTO knowledge_sources (point_id, source_file, source_kb) "
        "VALUES (?, ?, ?)",
        (point_id, source_file, source_kb),
    )


def add_source(point_id, source_file, source_kb):
    """给已存在知识点登记一条来源映射并提交,返回新行 id。"""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO knowledge_sources (point_id, source_file, source_kb) "
        "VALUES (?, ?, ?)",
        (point_id, source_file, source_kb),
    )
    conn.commit()
    return cur.lastrowid


def get_sources(point_id):
    """按知识点 id 查询其全部来源映射,返回 dict 列表。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM knowledge_sources WHERE point_id = ? ORDER BY id",
        (point_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_point_ids_by_source(source_file):
    """按来源文件反查关联的知识点 id 列表(去重后升序)。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT point_id FROM knowledge_sources "
        "WHERE source_file = ? ORDER BY point_id",
        (source_file,),
    ).fetchall()
    return [r["point_id"] for r in rows]


# --------------------------------------------------------------------------- #
# 习题(exercises)
# --------------------------------------------------------------------------- #

def _to_points_json(points):
    """把 points 统一序列化为 JSON 字符串;已是 str 则原样返回,None 返回 None。"""
    if points is None:
        return None
    if isinstance(points, str):
        return points
    return json.dumps(points, ensure_ascii=False)


def add_exercise(question, answer=None, chapter=None, points=None, source_file=None):
    """新增一条习题,points 接受 JSON 字符串或可序列化 list,返回新习题 id。"""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO exercises (question, answer, chapter, points, source_file) "
        "VALUES (?, ?, ?, ?, ?)",
        (question, answer, chapter, _to_points_json(points), source_file),
    )
    conn.commit()
    return cur.lastrowid


def add_exercises(exercises):
    """批量新增习题,整体用事务包裹(任一失败全部回滚)。

    exercises: list[dict],每项键同 add_exercise 参数
    (question/answer/chapter/points/source_file)。返回新习题 id 列表。
    """
    conn = get_conn()
    ids = []
    try:
        for item in exercises:
            cur = conn.execute(
                "INSERT INTO exercises "
                "(question, answer, chapter, points, source_file) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    item.get("question"),
                    item.get("answer"),
                    item.get("chapter"),
                    _to_points_json(item.get("points")),
                    item.get("source_file"),
                ),
            )
            ids.append(cur.lastrowid)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ids


def get_exercises(chapter=None):
    """查询习题库,可按章节筛选,按 id 升序;返回 dict 列表。"""
    conn = get_conn()
    if chapter:
        rows = conn.execute(
            "SELECT * FROM exercises WHERE chapter = ? ORDER BY id", (chapter,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM exercises ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_all_exercise_points():
    """提取全部习题的 points 字段(JSON 数组字符串),解析后扁平拼成知识点列表。

    去重保序;无法解析或非字符串项自动跳过。
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT points FROM exercises "
        "WHERE points IS NOT NULL AND points != ''"
    ).fetchall()
    result = []
    for row in rows:
        try:
            data = json.loads(row["points"])
        except (TypeError, ValueError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, str) and item and item not in result:
                result.append(item)
    return result


# --------------------------------------------------------------------------- #
# 统计
# --------------------------------------------------------------------------- #

def count_by_kb_type():
    """按 kb_type 统计三库内知识点数量,返回 {'零碎': n, '重点': n, '习题': n}。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT kb_type, COUNT(*) AS n FROM knowledge_points GROUP BY kb_type"
    ).fetchall()
    counts = {"零碎": 0, "重点": 0, "习题": 0}
    for row in rows:
        key = row["kb_type"]
        if key in counts:
            counts[key] = row["n"]
    return counts


def stats():
    """返回数据库总览:三张表行数 + 三库分布。"""
    conn = get_conn()
    return {
        "knowledge_points": conn.execute(
            "SELECT COUNT(*) FROM knowledge_points"
        ).fetchone()[0],
        "knowledge_sources": conn.execute(
            "SELECT COUNT(*) FROM knowledge_sources"
        ).fetchone()[0],
        "exercises": conn.execute(
            "SELECT COUNT(*) FROM exercises"
        ).fetchone()[0],
        "by_kb_type": count_by_kb_type(),
    }

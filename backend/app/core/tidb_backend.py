"""TiDB 后端适配层(Phase2 ORM 双后端)

提供与 database.py get_conn() 兼容的连接接口 + 统一执行封装。
DB_BACKEND=tidb 时 database.get_conn() 分发到此模块。

连接参数来自环境变量(TIDB_HOST/PORT/USER/PASSWORD/DB/SSL), 与 Makers 云函数一致。
"""
import os
import threading

DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").lower()

_local = threading.local()


def is_tidb():
    return DB_BACKEND == "tidb"


def get_conn():
    """获取线程本地 TiDB 连接(pymysql DictCursor, 兼容 sqlite3.Row 的 dict 访问)"""
    if not hasattr(_local, "conn") or _local.conn is None:
        import pymysql
        _local.conn = pymysql.connect(
            host=os.environ.get("TIDB_HOST"),
            port=int(os.environ.get("TIDB_PORT", "4000")),
            user=os.environ.get("TIDB_USER"),
            password=os.environ.get("TIDB_PASSWORD"),
            database=os.environ.get("TIDB_DB", "supplykit"),
            ssl={"ca": None} if os.environ.get("TIDB_SSL", "true").lower() != "false" else None,
            connect_timeout=15,
            read_timeout=120,
            write_timeout=120,
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
            charset="utf8mb4",
        )
    else:
        try:
            _local.conn.ping(reconnect=True)
        except Exception:
            _local.conn = None
            return get_conn()
    return _local.conn


def close():
    if hasattr(_local, "conn") and _local.conn:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None


def execute(sql, params=None):
    """执行 SQL(自动方言转换), 返回 dict 列表(兼容 ORM 期望)"""
    from app.core.dialect import to_tidb
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(to_tidb(sql), params or ())
        if cur.description:
            rows = cur.fetchall()
            return rows
        conn.commit()
        return []
    finally:
        cur.close()


def execute_lastrowid(sql, params=None):
    """执行写 SQL 返回 lastrowid(兼容 InsertBuilder 期望)"""
    from app.core.dialect import to_tidb
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(to_tidb(sql), params or ())
        conn.commit()
        return cur.lastrowid
    finally:
        cur.close()


# PRAGMA 探活替代: TiDB 无 quick_check, 用 SELECT 1
def health_check():
    try:
        rows = execute("SELECT 1 AS ok")
        return bool(rows)
    except Exception:
        return False

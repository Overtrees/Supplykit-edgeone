"""TiDB 后端适配层(Phase2 ORM 双后端)

DB_BACKEND=tidb 时 database.get_conn() 返回 _TiDBConnAdapter——
伪装成 sqlite3.Connection 接口, 使项目里 322 处原生 conn.execute(...) 代码
无需改动即可在 TiDB 上运行(SQL 自动方言转换 + ? → %s + 反引号)。

连接参数来自环境变量(TIDB_HOST/PORT/USER/PASSWORD/DB/SSL), 与 Makers 云函数一致。
"""
import os
import threading

DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").lower()

_local = threading.local()


def is_tidb():
    return DB_BACKEND == "tidb"


class _AdapterCursor:
    """兼容 sqlite3.Cursor 的只读结果包装: fetchone/fetchall/rowcount/lastrowid"""
    def __init__(self, rows=None, rowcount=0, lastrowid=0, description=None):
        self._rows = rows or []
        self._i = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.description = description

    def fetchone(self):
        if self._i < len(self._rows):
            r = self._rows[self._i]
            self._i += 1
            return r
        return None

    def fetchall(self):
        r = self._rows[self._i:]
        self._i = len(self._rows)
        return r

    def close(self):
        pass


class _TiDBConnAdapter:
    """伪装 sqlite3.Connection: execute/executemany/executescript/commit/rollback/close/cursor"""

    def __init__(self):
        import pymysql
        self._conn = pymysql.connect(
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

    def _finalize(self, sql):
        from app.core.dialect import to_tidb
        import re
        sql = to_tidb(sql)
        sql = re.sub(r'"([A-Za-z_][A-Za-z0-9_]*)"', r'`\1`', sql)
        return sql.replace("?", "%s")

    def execute(self, sql, params=None):
        from app.core.database import _finalize_sql
        # PRAGMA 是 SQLite 专有: TiDB 无此概念, 直接忽略(busy_timeout/journal_mode/wal_checkpoint 等由平台管理)
        if isinstance(sql, str) and sql.lstrip().upper().startswith("PRAGMA"):
            return _AdapterCursor()
        cur = self._conn.cursor()
        try:
            cur.execute(_finalize_sql(sql), params or ())
            if cur.description:
                rows = cur.fetchall()
                desc = [d[0] for d in cur.description]
                return _AdapterCursor(rows=rows, description=desc)
            self._conn.commit()
            return _AdapterCursor(rowcount=cur.rowcount, lastrowid=cur.lastrowid)
        finally:
            cur.close()

    def executemany(self, sql, seq_of_params):
        from app.core.database import _finalize_sql
        cur = self._conn.cursor()
        try:
            cur.executemany(_finalize_sql(sql), list(seq_of_params))
            self._conn.commit()
            return _AdapterCursor(rowcount=cur.rowcount, lastrowid=cur.lastrowid)
        finally:
            cur.close()

    def executescript(self, sql):
        # 逐条执行(init_db 在 tidb 模式已跳过, 这里兜底)
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                self.execute(stmt)
        return self

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        try:
            self._conn.commit()
        except Exception:
            pass

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def ping(self, reconnect=True):
        try:
            self._conn.ping(reconnect=reconnect)
            return True
        except Exception:
            return False


def get_conn():
    """线程本地 TiDB 适配连接(兼容 sqlite3.Connection 接口)"""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _TiDBConnAdapter()
    else:
        if not _local.conn.ping(reconnect=True):
            _local.conn = _TiDBConnAdapter()
    return _local.conn


def close():
    if hasattr(_local, "conn") and _local.conn:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None


def health_check():
    """PRAGMA quick_check 的 TiDB 替代(探活)"""
    try:
        r = get_conn().execute("SELECT 1 AS ok").fetchone()
        return bool(r)
    except Exception:
        return False

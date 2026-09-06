"""Makers 原生数据层(方案 B: 无 SQLite 适配, 直写 TiDB 方言)

规范:
- pymysql DictCursor, 反引号标识符, %s 占位符, DATE_FORMAT/DATE_SUB/NOW()
- 时间列(DATETIME)存 'YYYY-MM-DD HH:MM:SS' 字符串, 按天聚合用 DATE(col)
- 写操作 autocommit; 读返回 list[dict]
"""
import os
import threading

import pymysql

_local = threading.local()


def conn():
    """线程本地 TiDB 连接"""
    if getattr(_local, "c", None) is None:
        _local.c = pymysql.connect(
            host=os.environ.get("TIDB_HOST"),
            port=int(os.environ.get("TIDB_PORT", "4000")),
            user=os.environ.get("TIDB_USER"),
            password=os.environ.get("TIDB_PASSWORD"),
            database=os.environ.get("TIDB_DB", "supplykit"),
            ssl={"ca": None} if os.environ.get("TIDB_SSL", "true").lower() != "false" else None,
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
            charset="utf8mb4",
            connect_timeout=15,
            read_timeout=90,
            write_timeout=90,
        )
    else:
        try:
            _local.c.ping(reconnect=True)
        except Exception:
            _local.c = None
            return conn()
    return _local.c


def query(sql, params=None):
    """SELECT, 返回 list[dict]"""
    cur = conn().cursor()
    try:
        cur.execute(sql, params or ())
        return cur.fetchall()
    finally:
        cur.close()


def one(sql, params=None):
    r = query(sql, params)
    return r[0] if r else None


def execute(sql, params=None):
    """写操作(autocommit), 返回受影响行数(分批删除等按 rowcount 判断)"""
    cur = conn().cursor()
    try:
        cur.execute(sql, params or ())
        return cur.rowcount or 0
    finally:
        cur.close()


def executemany(sql, seq_of_params):
    cur = conn().cursor()
    try:
        cur.executemany(sql, list(seq_of_params))
    finally:
        cur.close()


def count(sql, params=None):
    r = one(sql, params)
    if not r:
        return 0
    vals = list(r.values())
    try:
        return int(vals[0] or 0)
    except Exception:
        return 0


def esc_ident(name):
    """标识符转义(防注入): 反引号包裹"""
    return "`" + str(name).replace("`", "``") + "`"


def table(name):
    """轻量 CRUD 助手(按需用原生 SQL, 无 ORM 魔法)"""
    return _Table(name)


class _Table:
    def __init__(self, name):
        self.name = name

    def all(self, where="", params=None, order="", limit=0):
        sql = "SELECT * FROM `%s`%s%s%s" % (
            self.name,
            (" WHERE " + where) if where else "",
            (" ORDER BY " + order) if order else "",
            (" LIMIT %d" % limit) if limit else "",
        )
        return query(sql, params)

    def get(self, **kw):
        if not kw:
            return None
        where = " AND ".join("`%s` = %%s" % k for k in kw)
        return one("SELECT * FROM `%s` WHERE %s LIMIT 1" % (self.name, where), list(kw.values()))

    def insert(self, row):
        cols = list(row.keys())
        sql = "INSERT INTO `%s` (%s) VALUES (%s)" % (
            self.name,
            ", ".join("`%s`" % c for c in cols),
            ", ".join(["%s"] * len(cols)),
        )
        return execute(sql, [row[c] for c in cols])

    def upsert(self, row, conflict_cols):
        """INSERT ... ON DUPLICATE KEY UPDATE(需唯一索引)"""
        cols = list(row.keys())
        upd = ", ".join("`%s`=VALUES(`%s`)" % (c, c) for c in cols if c not in conflict_cols)
        sql = "INSERT INTO `%s` (%s) VALUES (%s) ON DUPLICATE KEY UPDATE %s" % (
            self.name,
            ", ".join("`%s`" % c for c in cols),
            ", ".join(["%s"] * len(cols)),
            upd,
        )
        return execute(sql, [row[c] for c in cols])

    def update(self, data, where, params=()):
        sets = ", ".join("`%s` = %%s" % k for k in data)
        return execute("UPDATE `%s` SET %s WHERE %s" % (self.name, sets, where),
                       list(data.values()) + list(params))

    def delete(self, where, params=()):
        return execute("DELETE FROM `%s` WHERE %s" % (self.name, where), list(params))
"""Makers 原生数据层(方案 B: 无 SQLite 适配, 直写 TiDB 方言)

规范:
- pymysql DictCursor, 反引号标识符, %s 占位符, DATE_FORMAT/DATE_SUB/NOW()
- 时间列(DATETIME)存 'YYYY-MM-DD HH:MM:SS' 字符串, 按天聚合用 DATE(col)
- 写操作 autocommit; 读返回 list[dict]
"""
import os
import queue
import threading
from contextlib import contextmanager

import pymysql

_local = threading.local()
_POOL = None
_POOL_LIMIT = int(os.environ.get("DB_POOL_LIMIT", "10"))  # 连接池上限(每实例——TiDB serverless 连接限制保险)

# 2026-11-02 S0 修复: 原实现用 `q.qsize() < _POOL_LIMIT` 判断能否新建连接, 但队列里只有
# 「已归还」的连接, 正在使用的根本不在队列里 —— qsize 几乎永远 < 上限, 于是每次池空都新建,
# 上限形同虚设。复刻原逻辑压测: 上限 10 / 并发 60 → 实际建了 60 条连接, 一次尖峰即可打满
# TiDB Serverless 连接数 → 全站 500。
# 改为用信号量表达「存活连接总数」: 新建前 acquire, 真正 close 时 release。
# 名额与连接一一对应, 池中 + 使用中 永远 ≤ _POOL_LIMIT。
_SEM = threading.BoundedSemaphore(_POOL_LIMIT)


class _PoolFull(Exception):
    """名额耗尽 —— 调用方应等待池中连接归还, 而不是继续新建"""


def _close_conn(c):
    """关闭连接并归还名额。**所有真正的 close 必须走这里**, 否则名额泄漏 → 池永久少一条。"""
    if c is None:
        return
    try:
        c.close()
    except Exception:
        pass
    try:
        _SEM.release()
    except ValueError:
        pass  # BoundedSemaphore 超额释放(重复 close) —— 吞掉, 计名额比崩掉重要


def _new_conn():
    """连接 TiDB(库不存在自动建库——2026-10-17 新集群迁移: 空库首连 Unknown database 时
    无库连接 CREATE DATABASE 后重连; 幂等, 迁移到空集群自动引导)

    2026-11-02: 先抢名额再建连; 建连失败归还名额(否则一次网络抖动就永久吃掉一个配额)。"""
    if not _SEM.acquire(blocking=False):
        raise _PoolFull("连接池名额已满(%d)" % _POOL_LIMIT)
    try:
        _db = os.environ.get("TIDB_DB", "supplykit")
        try:
            return _connect_db(_db)
        except pymysql.err.OperationalError as _e:
            if "Unknown database" in str(_e) or "1049" in str(_e):
                try:
                    _c0 = _connect_db(None)
                    try:
                        cur = _c0.cursor()
                        cur.execute("CREATE DATABASE IF NOT EXISTS `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin" % _db)
                        cur.close()
                    finally:
                        try:
                            _c0.close()
                        except Exception:
                            pass
                    return _connect_db(_db)
                except Exception:
                    pass
            raise
    except Exception:
        _SEM.release()
        raise


def _connect_db(database):
    return pymysql.connect(
        host=os.environ.get("TIDB_HOST"),
        port=int(os.environ.get("TIDB_PORT", "4000")),
        user=os.environ.get("TIDB_USER"),
        password=os.environ.get("TIDB_PASSWORD"),
        database=database,
        ssl={"ca": None} if os.environ.get("TIDB_SSL", "true").lower() != "false" else None,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4",
        connect_timeout=15,
        read_timeout=90,
        write_timeout=90,
    )


def _pool():
    global _POOL
    if _POOL is None:
        _POOL = queue.Queue(maxsize=_POOL_LIMIT)
    return _POOL


def _acquire():
    """池取(空闲连接复用/名额耗尽则等待归还 20s——防无限新建超 TiDB 连接上限)

    2026-11-02: 名额判断交给 _new_conn 内部信号量; 这里只负责"池里没有就尝试新建,
    新建被名额挡住就等别人归还"。"""
    q = _pool()
    try:
        c = q.get_nowait()
    except queue.Empty:
        try:
            c = _new_conn()
        except _PoolFull:
            # 存活连接已达上限 —— 阻塞等池中归还(20s), 不再无脑新建
            try:
                c = q.get(timeout=20)
            except queue.Empty:
                raise RuntimeError(
                    "数据库连接池耗尽(上限 %d, 等待 20s 无归还) —— 检查是否有长事务/未释放连接" % _POOL_LIMIT)
    try:
        c.ping(reconnect=True)
    except Exception:
        _close_conn(c)   # 坏连接: 关闭并**归还名额**
        c = _new_conn()  # 重新占名额
    return c


def _release(c):
    """归还池(事务连接不在此归还——transaction finally 管); 池满关闭并归还名额"""
    if c is None or (getattr(_local, "txn", None) is c):
        return  # 事务连接由 transaction 归还
    try:
        q = _pool()
        q.put_nowait(c)
    except queue.Full:
        _close_conn(c)


def conn():
    """取连接: 事务内返回事务连接(同事务共享); 否则池取"""
    if getattr(_local, "txn", None) is not None:
        return _local.txn
    return _acquire()


@contextmanager
def transaction():
    """事务上下文(临时关 autocommit——FOR UPDATE 行锁/多语句原子; 事务连接隔离——期间 query/execute 用同连接;
    连接池取用, finally 归还)"""
    c = _acquire()
    _local.txn = c
    c.autocommit(False)
    try:
        yield c
        c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
        raise
    finally:
        _local.txn = None
        try:
            c.autocommit(True)
        except Exception:
            pass  # 连接可能已坏 —— 仍要归还名额, 下次 _acquire 的 ping 会重建
        _release(c)


def query(sql, params=None):
    """SELECT, 返回 list[dict](连接用后归还池)"""
    c = conn()
    cur = c.cursor()
    try:
        cur.execute(sql, params or ())
        return cur.fetchall()
    finally:
        cur.close()
        _release(c)


def one(sql, params=None):
    r = query(sql, params)
    return r[0] if r else None


def execute(sql, params=None):
    """写操作(autocommit), 返回受影响行数(分批删除等按 rowcount 判断; 连接用后归还)"""
    c = conn()
    cur = c.cursor()
    try:
        cur.execute(sql, params or ())
        return cur.rowcount or 0
    finally:
        cur.close()
        _release(c)


def execute_id(sql, params=None):
    """写操作返回 lastrowid(自增主键; create 类接口返回真实 id 用, 2026-09-11 体检; 连接用后归还)"""
    c = conn()
    cur = c.cursor()
    try:
        cur.execute(sql, params or ())
        return cur.lastrowid or 0
    finally:
        cur.close()
        _release(c)


def executemany(sql, seq_of_params):
    c = conn()
    cur = c.cursor()
    try:
        cur.executemany(sql, list(seq_of_params))
    finally:
        cur.close()
        _release(c)


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
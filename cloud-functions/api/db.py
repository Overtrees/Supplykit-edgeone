"""Makers 原生数据层(方案 B: 无 SQLite 适配, 直写 TiDB 方言)

规范:
- pymysql DictCursor, 反引号标识符, %s 占位符, DATE_FORMAT/DATE_SUB/NOW()
- 时间列(DATETIME)存 'YYYY-MM-DD HH:MM:SS' 字符串, 按天聚合用 DATE(col)
- 写操作 autocommit; 读返回 list[dict]
"""
import os
import queue
import threading
import time
from contextlib import contextmanager

import pymysql

_local = threading.local()
_POOL = None
_POOL_LIMIT = int(os.environ.get("DB_POOL_LIMIT", "10"))  # 连接池上限(每实例——TiDB serverless 连接限制保险)

# ── 2026-11-07 S0: 重计算并发预算(防"整站雪崩")────────────────────────────
# 事故: aux 单跑 4.5s, 与 stock-risk 并发时 34~35.8s, stock-risk 41.4s, purchase 31.3s,
#   同时段 summary 仍只要 0.8~1.3s —— 慢的不是 SQL, 是**连接池排队**:
#   每个重接口内部又 3~8 路并行, 两个接口就能把 10 条连接吃光, 其余请求在 _acquire 里
#   阻塞 20s 才等到(甚至抛"连接池耗尽")。表现为"所有页面一起卡 10~40s"。
# 原则(§15.8 可靠性): 连接是**全局稀缺资源**, 并发度必须由全局预算分配, 不能各接口自带。
#   HEAVY_CONCURRENCY=3: 同时最多 3 个重计算在跑, 其余排队(排队 1~3s 好过一起卡 40s)。
#   env 可调; 设 0 关闭闸门(紧急回滚用, 不需重新发版)。
# §15.181: 默认由 3 降为 1 做**一次线上实验** —— 见 docs/history §15.181。
#   核对调用点后发现: 全项目 `heavy()` 只有一处调用(core/list_store._cold_worker),
#   即"同时最多 N 个**域**在重建", 与读请求并发无关(读路由不申请名额)。
#   降到 1 = 重建串行, 测"GIL/连接争抢是否为 S5 读延迟的主因";
#   HEAVY_GLOBAL=2 不变, 多实例下仍有 2 路跨实例并行。
#   env 一键回滚: HEAVY_CONCURRENCY=3(不需改代码重发)。
_HEAVY_LIMIT = int(os.environ.get("HEAVY_CONCURRENCY", "1") or 0)
# §15.174: 原实现是 threading.BoundedSemaphore —— **名额会泄漏**。
#   线上 2026-11-08 14:28~14:30 证据: 三个 kind 的冷算全部报
#   "冷算全局名额已满, 本轮跳过(并发上限 3, 已等 2s)", 连续 10 分钟没有一次重建成功,
#   list_head.gen 停在 14:20 不动 → 配置改了补货也不刷新(fp 永远不等)。
#   成因: 云函数实例会被平台冻结/回收, 线程停在 DB IO 上时 finally 可能迟迟不执行(或进程直接消失),
#   Semaphore 计数只减不加 → 该实例此后永久认为"3 个名额都在用"。
#   改法: 槽位 + 租约(超过 _HEAVY_MAX_HOLD 秒的槽位视为僵死, 由下一个申请者回收)。
#   正常重建 4~6s、判定重建 ~30s, 180s 租约不会误杀; 回收动作留痕(try_err)。
_HEAVY_MAX_HOLD = float(os.environ.get("HEAVY_MAX_HOLD", "120") or 120)
_HEAVY_LK = threading.Lock()
_HEAVY_SLOTS = {}      # token -> (start_ts, name)
_HEAVY_SEQ = [0]

# ── 2026-11-08 §15.178 名额分两级：实例内(读路径) + 全局(后台重建) ──────────
# 为什么必须两级: `_HEAVY_SLOTS` 是**进程内** dict, 云函数为并发请求开多个实例,
#   每个实例各有一份 → 线上实测"同时重建 7 个 kind"时, 全局真实并发是 实例数×3,
#   实例内名额根本没起到"全局预算"的作用。探针实测(读并发固定 8):
#     只 stale 1 个 kind → 全站 p95 117ms(单条 SELECT 14ms)
#     全量失效 7 个 kind → p95 962ms, /api/health(222 字节无业务) 193ms
#     全量失效 + 持续写   → p95 3400ms, health 3200ms
#   即"重建本身不贵, **同时**重建才贵"; 而"同时"是跨实例的, 进程内锁管不到。
# 所以: 读路径继续用实例内名额(零额外往返, 90ms 级的接口不能被一次抢槽拖成 180ms);
#   只有后台重建(list_store.submit_background)额外抢一张**全局**槽位(heavy_slot 表),
#   把"全站同时重建的路数"从 实例数×3 压到 HEAVY_GLOBAL(默认 2)。
# 租约 180s 与进程内同理: 实例被冻结时槽位由下一个申请者回收, 不会永久泄漏。
_HEAVY_GLOBAL = int(os.environ.get("HEAVY_GLOBAL", "2") or 0)
_HEAVY_GLOBAL_LEASE = int(float(os.environ.get("HEAVY_GLOBAL_LEASE", "180") or 180))
_GSLOT_DEGRADED = [False]   # 表不可用 → 降级为"只实例内", 只告警一次
_GSLOT_INIT = [False]       # 本进程是否已确保槽位行存在


def _gslot_claim(wait):
    """抢一张全局槽位, 返回 (slot, holder); 抢不到返回 (None, None)。

    锁表不可用(未建表/权限) → (-1, holder) 表示**降级放行**: 治理手段不能变成
    新的不可用来源(红线 6 同理)。
    """
    import uuid
    h = uuid.uuid4().hex[:12]
    if _HEAVY_GLOBAL <= 0:
        return -1, h
    _e = execute                      # 本模块内直连(不经 import, 避免自引用)
    if not _GSLOT_INIT[0]:
        try:
            for s in range(1, _HEAVY_GLOBAL + 1):
                _e("INSERT IGNORE INTO heavy_slot(slot, ts, holder) VALUES(%s, NOW(6), '')", [s])
            _GSLOT_INIT[0] = True
        except Exception:
            pass                      # 建不了表 → 下面抢槽会失败并走降级分支
    dl = time.time() + max(float(wait), 0.0)
    while True:
        for s in range(1, _HEAVY_GLOBAL + 1):
            try:
                # 红线 14: INTERVAL 必须用字符串拼接。这里踩过一次 —— 传 %s + float 180.0
                #   时 TiDB **不报错**但条件恒假(rowcount 恒 0), 于是僵死槽位永不过期,
                #   两个 holder 把全局名额永久占满 → 全部后台重建停摆(线上 16:41~16:47 实测)。
                #   同一语句里 int 180 传参 rowcount=1, float 180.0 传参 rowcount=0, 已对照验证。
                n = _e("UPDATE heavy_slot SET holder=%s, ts=NOW(6) WHERE slot=%s "
                       "AND (holder='' OR ts < DATE_SUB(NOW(6), INTERVAL "
                       + str(int(_HEAVY_GLOBAL_LEASE)) + " SECOND))", [h, s])
                if int(n or 0) == 1:
                    return s, h
            except Exception as _e1:
                if not _GSLOT_DEGRADED[0]:
                    _GSLOT_DEGRADED[0] = True
                    try:
                        from routes.common import try_err as _te
                        _te("db", "全局名额表不可用, 降级为实例内名额", _e1)
                    except Exception:
                        pass
                return -1, h
        if time.time() >= dl:
            return None, None
        time.sleep(0.2)               # 槽位极少(2), 0.05s 轮询会在 2s 内打 80 次 SQL


def _gslot_release(slot, holder):
    if not slot or slot < 0:
        return
    try:
        execute("UPDATE heavy_slot SET holder='' WHERE slot=%s AND holder=%s", [slot, holder])
    except Exception:
        pass                      # 释放失败靠租约回收, 不抛给调用方


class HeavyBusy(RuntimeError):
    """重计算名额耗尽(排队超时) —— 调用方应降级返回旧值/空结构, 不要拖住前端"""


@contextmanager
def heavy(name="", timeout=20.0, global_=False):
    """重计算名额(带租约)。拿不到就抛 HeavyBusy, 让请求**快速失败**而不是阻塞在连接等待上。

    用法: `with heavy("aux"):` 包住整个读路径(含其内部并行)。
    §15.178: `global_=True` 额外占一张**跨实例**槽位(heavy_slot 表), 只给后台重建用。
    """
    if _HEAVY_LIMIT <= 0 and not global_:
        yield
        return
    # 顺序很重要(§15.178): **先抢全局槽, 再抢实例内名额**。
    #   反过来(先占实例内, 再等全局 2s)会让"抢不到全局"的重建线程白占实例内名额 2s:
    #   线上实测 3 个 kind 重建时实例内 3 个名额全被"等待中"占满 → 读请求无名额 →
    #   S2(6读2写) p50 从 297ms 劣化到 1717ms, 且所有接口的 max 都卡在 2.2s(=_HEAVY_WAIT)。
    #   全局槽稀缺且慢, 就该在最外层等; 等不到直接失败, 绝不占着实例名额等。
    gs, gh = (None, None)
    if global_:
        gs, gh = _gslot_claim(timeout)
        if gs is None:
            raise HeavyBusy("全局重建名额已满(上限 %d): %s" % (_HEAVY_GLOBAL, name))
    tok = None
    deadline = time.time() + max(float(timeout), 0.0)
    while _HEAVY_LIMIT > 0:
        now = time.time()
        with _HEAVY_LK:
            # 回收僵死槽位(实例冻结/线程未走到 finally)
            for _k, (_t0, _n) in list(_HEAVY_SLOTS.items()):
                if now - _t0 > _HEAVY_MAX_HOLD:
                    _HEAVY_SLOTS.pop(_k, None)
                    try:
                        from routes.common import try_err as _te
                        _te("db", "重计算名额超期回收(%.0fs): %s" % (now - _t0, _n),
                            RuntimeError("lease_expired"))
                    except Exception:
                        pass
            if len(_HEAVY_SLOTS) < _HEAVY_LIMIT:
                _HEAVY_SEQ[0] += 1
                tok = _HEAVY_SEQ[0]
                _HEAVY_SLOTS[tok] = (now, name)
                break
        if now >= deadline:
            if gs:
                _gslot_release(gs, gh)
            raise HeavyBusy("重计算繁忙(并发上限 %d, 已等 %.1fs): %s"
                            % (_HEAVY_LIMIT, float(timeout), name))
        time.sleep(0.01)
    try:
        yield
    finally:
        if gs:
            _gslot_release(gs, gh)
        with _HEAVY_LK:
            _HEAVY_SLOTS.pop(tok, None)


# 2026-11-06: 线上逐层计时(只读埋点, 不改任何执行路径)
#   背景: 同区(新加坡2)部署后 summary 仍 3.8s, 但 perf_debug 只有"查询 2.5s / 组装 1.2s"两段,
#   无法判断 2.5s 里是"建连握手"还是"SQL 执行"还是"结果传输"。三者优化手段完全不同:
#     建连贵 → 减连接数/改多语句;  执行贵 → 加索引/改 SQL;  传输贵 → 减列/减行/物化。
#   这里把 connect 与每条 stmt 的真实耗时记进进程内 STATS, 由调用方写进 perf_debug。
#   STATS 是**每请求新进程**的实例内变量, 天然只统计本请求, 无跨请求污染。
STATS = {"conn_n": 0, "conn_s": 0.0, "stmt_n": 0, "stmt_s": 0.0, "stmt_max": 0.0}
_STAT_LK = threading.Lock()


def stats_reset():
    """请求开始时清零(同一请求内多线程累加, 由 GIL+锁保证不丢)"""
    with _STAT_LK:
        STATS["conn_n"] = 0
        STATS["conn_s"] = 0.0
        STATS["stmt_n"] = 0
        STATS["stmt_s"] = 0.0
        STATS["stmt_max"] = 0.0
    return STATS


def stats_add(field, sec):
    try:
        with _STAT_LK:
            STATS[field + "_n"] = STATS.get(field + "_n", 0) + 1
            STATS[field + "_s"] = STATS.get(field + "_s", 0.0) + float(sec)
            if field == "stmt" and float(sec) > STATS.get("stmt_max", 0.0):
                STATS["stmt_max"] = float(sec)
    except Exception:
        pass


def stats_snapshot():
    with _STAT_LK:
        return dict(STATS)

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
    import time as _t
    _ts = _t.time()
    try:
        return _connect_db_inner(database)
    finally:
        stats_add("conn", _t.time() - _ts)


def _connect_db_inner(database):
    c = pymysql.connect(
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
    # 2026-11-04 会话时区固定为北京时间(+08:00):
    #   TiDB Cloud Serverless 默认 SYSTEM=UTC, 而业务日期(下单/流水/月份边界)全是北京时间。
    #   不设的话 NOW()/CURRENT_TIMESTAMP 比北京时间早 8 小时 —— 北京时间 00:00–08:00 写入的
    #   订单/流水会被打成"昨天", 月初更会把整月流水甩到上月, 与页面"本月"口径对不上。
    #   这里失败就抛: 时区错了数据就错了, 静默降级反而更难查。
    _cur = c.cursor()
    try:
        _cur.execute("SET time_zone = '+08:00'")
    finally:
        _cur.close()
    return c


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


def _guard_fmt(sql, params):
    """发请求前先模拟 PyMySQL 的 `query % escaped_args`, 把"占位符与参数对不上"
    变成**带 SQL 上下文**的错误。

    2026-11-06 同一类错在 S5 连踩 4 次(认领 SELECT 的 %d 进参数位 / 认领 UPDATE 少一个
    占位 / 完成标记 `IN (%s)` 没插值 / stage 直读 `IN (%s)` 没插值)。PyMySQL 自己抛的是
    `TypeError: not all arguments converted` 或 `%d format: a real number is required`,
    **不带 SQL**, 线上表现是"任务卡住/评估静默失败", 要翻代码才定位。
    守卫只在参数为 list/tuple 且 SQL 含 % 时执行(全库无字面量 % 的 SQL, 不会误伤)。
    """
    if not params or not isinstance(params, (list, tuple)) or "%" not in sql:
        return
    try:
        args = tuple(("'%s'" % str(x)) if isinstance(x, str)
                     else ("NULL" if x is None else str(x)) for x in params)
        sql % args
    except (TypeError, ValueError) as e:
        raise ValueError("SQL 占位符与参数不匹配(%s): 参数 %d 个 | SQL=%s"
                         % (str(e)[:120], len(params), str(sql)[:240]))


def query(sql, params=None):
    """SELECT, 返回 list[dict](连接用后归还池)"""
    _guard_fmt(sql, params)
    c = conn()
    cur = c.cursor()
    try:
        import time as _t
        _ts = _t.time()
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        stats_add("stmt", _t.time() - _ts)
        return rows
    finally:
        cur.close()
        _release(c)


def one(sql, params=None):
    r = query(sql, params)
    return r[0] if r else None


def execute(sql, params=None):
    """写操作(autocommit), 返回受影响行数(分批删除等按 rowcount 判断; 连接用后归还)"""
    _guard_fmt(sql, params)
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
    _guard_fmt(sql, params)
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
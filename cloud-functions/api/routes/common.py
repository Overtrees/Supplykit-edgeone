"""通用工具: 统一响应 + JWT + 密码哈希(与旧 backend 兼容)"""
import base64
import functools
import hashlib
import hmac
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

# GMV 总收入口径(含申请退款流水, net 再扣)——看板 GMV/趋势/漏斗
PAID_STATUSES = ("待发货", "已发货", "已完成", "申请退款")
# 销量池(采购补货的日均实销分母): 仅"已完成"(钱货两清且物理离开仓库=真实消耗库存)
# (待发货锁定扣减/ERP 级联动暂不做——库存/在途以清洗页导入数据为准, 2026-09-09 用户决策)
SALES_STATUSES = ("已完成",)
# 2026-10-29 单一真源: 日销/快照/物化的 SQL 一律引用本常量拼接, 禁止再硬编码状态列表
# (实测口径漂移代价: 3 状态 vs 已完成 = 日销 +72.29%(jd)/+73.31%(other), 直接放大补货/断货)
SALES_SQL = ",".join("'%s'" % s for s in SALES_STATUSES)


def status_cond(col="order_status"):
    """GMV 已支付口径条件(2026-10-11 从 dashboard 提为公共——maintain 与 dashboard 共享同口径)"""
    _paid = tuple(PAID_STATUSES)
    return "%s IN (%s)" % (col, ",".join(["'%s'" % s for s in _paid]))


def ok(data):
    return {"ok": True, "data": data}


def fail(msg, status=400):
    return {"ok": False, "error": msg}


_TE_STATE = {}
_TE_WINDOW = 10.0


# 库存表列单一真源(2026-10-29 根治): 任何 SELECT ... FROM inventory 一律引用本常量
# 背景: 多处查询手写列清单, 漏 barcode → 消费方 r.get("barcode") 恒空 → 两码合并且被
#       products.barcode 兜底掩盖, 按码链路"看起来生效"; 同类漏列还会漏 warehouse/in_transit。
INVENTORY_COLS = ("sku, COALESCE(barcode,'') AS barcode, warehouse, warehouse_type, "
                  "available_qty, in_transit_qty, c_transit, safety_qty, safety_days, "
                  "locked_qty, product_name, beginning_stock")


# 导入目标 → 库存仓型(单一真源; 2026-10-29: 原只在 cleansing.py 定义, cleanse_write.py 引用时报
# NameError → 平台库存/B仓库存导入 100% 失败; 重构拆分时漏迁移)
TARGET_WH = {"inventory": "own", "platform_inv": "platform", "inventory_b": "platform_b"}


def check_cols(rows, cols, src, what):
    """运行时字段自检(2026-10-29 根治"SELECT 漏列"): 首行缺列即留痕, 不抛异常

    背景: 本轮两次踩坑(补货/断货 SELECT 漏 barcode) → 消费方 .get("barcode") 恒 None →
    两码静默合并, 且输出被 products.barcode 兜底掩盖。文本扫描门禁误报率高, 改为数据入口自检。
    """
    try:
        if rows and isinstance(rows[0], dict):
            _miss = [c for c in cols if c not in rows[0]]
            if _miss:
                try_err(src, "%s 查询缺列 %s(按码/按仓链路会静默失效)" % (what, _miss), None)
    except Exception:
        pass


_CH_CACHE = {}
_CH_TS = [0.0]
_CH_TTL = 60


def ch_list(force=False):
    """渠道配置列表(P0 数据驱动; 进程内 60s 缓存 —— 配置极少变, 不增 RU)"""
    import time as _t
    _now = _t.time()
    if not force and _CH_CACHE and (_now - _CH_TS[0]) < _CH_TTL:
        return list(_CH_CACHE.values())
    try:
        from db import query as _q
        rows = _q("SELECT code, name, enabled, supports_b, default_mode, sort "
                  "FROM channels ORDER BY sort, code") or []
        if rows:
            _CH_CACHE.clear()
            for r in rows:
                _CH_CACHE[str(r.get("code"))] = {
                    "code": str(r.get("code") or ""),
                    "name": str(r.get("name") or r.get("code") or ""),
                    "enabled": int(r.get("enabled") or 0),
                    "supports_b": int(r.get("supports_b") or 0),
                    "default_mode": str(r.get("default_mode") or "traditional"),
                    "sort": int(r.get("sort") or 0)}
            _CH_TS[0] = _now
    except Exception:
        pass
    if _CH_CACHE:
        return list(_CH_CACHE.values())
    # 兜底(表未建/新库): 沿用旧硬编码语义, 保证任何时点可用
    return [{"code": "jd", "name": "京东", "enabled": 1, "supports_b": 1,
             "default_mode": "bbcc", "sort": 1},
            {"code": "other", "name": "其他渠道", "enabled": 1, "supports_b": 0,
             "default_mode": "traditional", "sort": 2}]


def ch_conf(code):
    """单渠道配置; 未登记渠道按旧规则兜底(jd → bbcc+支持B, 其余 → traditional)"""
    _code = str(code or "jd")
    for _c in ch_list():
        if _c["code"] == _code:
            return _c
    return {"code": _code, "name": _code, "enabled": 1,
            "supports_b": 1 if _code == "jd" else 0,
            "default_mode": "bbcc" if _code == "jd" else "traditional", "sort": 99}


def ch_codes(enabled_only=True):
    """全部渠道 code 列表(替代硬编码 ["jd","other"])"""
    return [c["code"] for c in ch_list() if (not enabled_only or c["enabled"])]


def ch_supports_b(code):
    """该渠道是否走 BBCC 体系(B 仓调拨链) —— 替代 `channel == "jd"` 判断"""
    return bool(ch_conf(code).get("supports_b"))


def ch_default_mode(code):
    """该渠道默认补货模式 —— 替代 `"bbcc" if channel == "jd" else "traditional"`"""
    return str(ch_conf(code).get("default_mode") or "traditional")


def mark_driver(task_id, driver):
    """标记任务推进来源(前端轮询 / 后端保活) —— 2026-10-29 统一

    原现状: 只有 cleansing 有 /cleansing/driver 接口, seed / cleansing_eval / export
    **从未写过 driver** → 任务卡「后端推进|前端轮询」标签对这三类任务永远不出现。
    现由各 status 函数入口统一调用(前端轮询默认 frontend; continue_tasks 传 backend)。
    失败静默(纯展示标记, 不影响任务推进)。
    """
    if not task_id or driver not in ("frontend", "backend"):
        return
    try:
        from db import execute as _e
        _e("UPDATE sync_tasks SET params=JSON_SET(COALESCE(params,'{}'), '$.driver', %s) "
           "WHERE task_id=%s", [driver, task_id])
    except Exception as _e:
        # 2026-10-29: 原 except: pass 违反 except-pass 红线(纯展示标记也要留痕, 否则同款静默失效无人知)
        try_err('common', 'driver 标记降级', _e)


def try_err(src, what, exc=None, details=""):
    """静默吞异常纪律(2026-09-11 体检): except 分支统一自记 quality_logs 留痕(不阻断降级流程)
    —— 2026-10-07: details 缺省带 traceback 堆栈(limit 3); 同 source 10s 窗口限频+计数合并:
      窗口内首条写库(留痕), 窗口结束补写计数条(×N 次)——防风暴刷屏且不丢异常规模; 自身 except 必须 pass(防递归)"""
    def _write(_w, _c, _e, _d):
        try:
            from db import execute as _e2
            _msg = _w
            if _e is not None:
                _msg += ": %s: %s" % (type(_e).__name__, str(_e)[:200])
            if _c > 1:
                _msg += " · 同源10s内 ×%d 次" % _c
            _det = _d or ""
            if not _det:
                try:
                    import traceback as _tb
                    _det = _tb.format_exc(limit=3)[:300]
                except Exception:
                    _det = ""
            _e2("INSERT INTO quality_logs(log_type, level, message, details, source) "
                "VALUES('quiet_error','warning',%s,%s,%s)",
                (_msg[:200], _det[:300], src))
        except Exception:
            pass  # 写日志失败自吞(防递归)——设计保留

    try:
        import time as _t
        _now = _t.time()
        _st = _TE_STATE.setdefault(src, {"ts": 0.0, "count": 0, "what": "", "exc": None, "det": ""})
        if _now - _st["ts"] >= _TE_WINDOW and _st["count"] > 0:
            # 上窗口未补计数 → 先补写计数条(风暴规模留痕)
            _write(_st["what"], _st["count"] + 1, _st["exc"], _st["det"])
            _st["count"] = 0
        if _now - _st["ts"] < _TE_WINDOW:
            # 窗口内: 合并计数(首条信息保留, 不逐条写)
            _st["count"] += 1
            if not _st["what"]:
                _st["what"], _st["exc"], _st["det"] = what, exc, details or ""
            return
        # 窗口首条: 写库留痕
        _write(what, 1, exc, details)
        _st.update(ts=_now, count=0, what="", exc=None, det="")
    except Exception:
        pass  # 自身异常自吞(防递归)


# ── quality_logs 保留策略(单一真源) ──────────────────────────────────
# 2026-11-02 S1-3: 原策略「只留最近 500 条」是按条数截断, 高峰期(规则评估+清洗+导入)一天就写满,
# 回头查问题时前一天的证据已被冲掉; 低谷期又白留。改为按天保留 + 硬上限兜底。
QUALITY_LOG_KEEP_DAYS = 30
QUALITY_LOG_HARD_CAP = 20000


def prune_quality_logs(keep_days=None, hard_cap=None):
    """清理 quality_logs: ① deleted 超过 keep_days 天的 ② 仍超 hard_cap 则按 id 兜底截断

    返回 {"old": 按天删除数, "cap": 兜底删除数, "total": 清理后总数}。
    调用方(cron / 每日维护)共用本函数, 避免两处策略漂移(历史上 cron 留 500、dashboard 截到 300)。
    INTERVAL 不能用占位符(TiDB 报错), 天数走 int() 内联, 不接受外部输入。
    """
    _kd = int(keep_days if keep_days is not None else QUALITY_LOG_KEEP_DAYS)
    _cap = int(hard_cap if hard_cap is not None else QUALITY_LOG_HARD_CAP)
    out = {"old": 0, "cap": 0, "total": 0}
    from db import execute as _e, one as _o
    if _kd > 0:
        out["old"] = int(_e("DELETE FROM quality_logs WHERE created_at < "
                            "DATE_SUB(NOW(), INTERVAL %d DAY)" % _kd) or 0)
    _c = int((_o("SELECT COUNT(*) AS c FROM quality_logs") or {}).get("c") or 0)
    if _cap > 0 and _c > _cap:
        _k = _o("SELECT id FROM quality_logs ORDER BY id DESC LIMIT %s", [_cap]) or {}
        if _k.get("id"):
            out["cap"] = int(_e("DELETE FROM quality_logs WHERE id < %s", [_k.get("id")]) or 0)
    out["total"] = int((_o("SELECT COUNT(*) AS c FROM quality_logs") or {}).get("c") or 0)
    # pipeline_runs / eval_tasks 同生命周期(30 天): 运行流水只用于"最近一次/最近几天"排查
    if _kd > 0:
        try:
            _e("DELETE FROM pipeline_runs WHERE started_at < DATE_SUB(NOW(), INTERVAL %d DAY)" % _kd)
        except Exception:
            pass  # 表可能尚未建(首次启动) —— 静默
        try:
            _e("DELETE FROM eval_tasks WHERE created_at < DATE_SUB(NOW(), INTERVAL %d DAY)" % _kd)
        except Exception:
            pass
    # 评估任务硬上限(500 条): 防"每天保存几十次规则"把任务表堆成日志; 只留最近的
    try:
        _et = _o("SELECT COUNT(*) AS c FROM eval_tasks") or {}
        if int(_et.get("c") or 0) > 500:
            _ek = _o("SELECT id FROM eval_tasks ORDER BY id DESC LIMIT 500") or {}
            if _ek.get("id"):
                _e("DELETE FROM eval_tasks WHERE id < %s AND status NOT IN ('pending','running')",
                   [_ek.get("id")])
    except Exception:
        pass
    return out


# ── 链路运行流水(2026-11-02 S1-2) ────────────────────────────────────
# 解决「链路不可观测」: 此前排查"每日评估/物化重建到底跑没跑、跑了多久、影响多少行"只能翻
# quality_logs 里零散的审计行, 且那些行只保留 500 条(现已改按天, 见 prune_quality_logs)。
# 现在每次运行留一条结构化记录, /health?detail=1 直接读出「每个 pipeline 最近一次运行」。
class _RunRec(object):
    """运行记录句柄: 调用方在 with 体内写 rec.rows/rec.detail"""

    __slots__ = ("rows", "detail", "status")

    def __init__(self):
        self.rows = 0
        self.detail = None
        self.status = None   # None=按退出状态推断; 'skipped'/'partial' 等可显式指定


@contextmanager
def track_run(pipeline, scope="all"):
    """pipeline 运行记录上下文管理器

    用法:
        with track_run("daily_rules", ch) as rec:
            rec.rows = do_work()

    约束(设计取舍):
    - **绝不因记录失败影响主流程**: 建行/更新行全部 try/except 吞掉(它就是观测设施,
      不能反过来成为故障源); 因此本函数不会抛记录类异常。
    - 异常仍然向外抛(调用方照旧处理降级), 但会先把 status 落成 failed + error 摘要(200 字截断)。
    - 不记录堆栈(避免 quality_logs 里那套 traceback 落到可从公网读的 /health)。
    """
    _t0 = time.time()
    _rid = 0
    try:
        from db import execute_id as _ei
        _rid = int(_ei("INSERT INTO pipeline_runs(pipeline, scope, started_at, status) "
                       "VALUES(%s, %s, NOW(), 'running')", [str(pipeline)[:64], str(scope)[:32]]) or 0)
    except Exception:
        _rid = 0
    rec = _RunRec()
    _err = ""
    try:
        yield rec
    except Exception as _e:
        _err = ("%s: %s" % (type(_e).__name__, str(_e)[:180]))[:200]
        raise
    finally:
        if _rid:
            try:
                from db import execute as _ex
                _st = rec.status or ("failed" if _err else "ok")
                _det = None
                if rec.detail is not None:
                    try:
                        _det = json.dumps(rec.detail, ensure_ascii=False)[:4000]
                    except Exception:
                        _det = None
                _ex("UPDATE pipeline_runs SET status=%s, finished_at=NOW(), duration_ms=%s, "
                    "rows_affected=%s, error=%s, detail=%s WHERE id=%s",
                    [_st, int((time.time() - _t0) * 1000), int(rec.rows or 0),
                     _err or None, _det, _rid])
            except Exception:
                pass


def pipeline_run(name, scope="all", rows_key=None):
    """把一次 pipeline 运行记入 pipeline_runs(S1-2)

    用在 @traced 内层、业务函数外层:
        @router.api_route(...)
        @traced
        @pipeline_run("archive", rows_key="archived")
        async def cron_archive(request): ...

    status 约定(与 /cron/* 统一响应体一致), 目的是让 /health?detail=1 能区分
    「真的跑了」「今天被抢占跳过」「跑挂了」三种情况:
      ok:false      → failed      (含 401/500)
      data.skipped  → skipped     (当日幂等抢占命中, 未执行)
      其余          → ok
    rows 取 data[rows_key] 转 int(取不到/非数值记 0)。
    """
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            with track_run(name, scope) as rec:
                res = await fn(*a, **kw)
                if isinstance(res, dict):
                    if not res.get("ok"):
                        rec.status = "failed"
                    else:
                        d = res.get("data") or {}
                        if isinstance(d, dict) and d.get("skipped"):
                            rec.status = "skipped"
                        elif rows_key and isinstance(d, dict):
                            for _k in ((rows_key,) if isinstance(rows_key, str) else tuple(rows_key)):
                                try:
                                    rec.rows = int(d.get(_k) or 0)
                                    break
                                except Exception:
                                    continue
                return res
        return wrapper
    return deco


def last_runs(limit_pipelines=32):
    """每个 (pipeline, scope) 的最近一次运行 —— 供 /health?detail=1

    只回结构化摘要(不含 error 正文, 只给 has_error) —— /health 匿名可读, 防泄漏表名/SQL 片段。
    stale: running 且已开始超过 30 分钟 → 视为"进程中途被回收"(记录永远停在 running)。
    """
    out = []
    try:
        from db import query as _q
        rows = _q("SELECT r.pipeline, r.scope, r.status, r.started_at, r.finished_at, "
                  "r.duration_ms, r.rows_affected, (r.error IS NOT NULL AND r.error<>'') AS has_error "
                  "FROM pipeline_runs r JOIN (SELECT pipeline, scope, MAX(id) AS mid FROM pipeline_runs "
                  "WHERE started_at > DATE_SUB(NOW(), INTERVAL 7 DAY) "
                  "GROUP BY pipeline, scope) m ON r.id=m.mid "
                  "ORDER BY r.pipeline, r.scope LIMIT %s", [int(limit_pipelines)])
        _now = time.time()
        for r in rows or []:
            _sa = str(r.get("started_at") or "")[:19]
            _st = str(r.get("status") or "")
            _stale = False
            if _st == "running":
                try:
                    from datetime import datetime as _dt
                    _age = _now - _dt.strptime(_sa, "%Y-%m-%d %H:%M:%S").timestamp()
                    _stale = _age > 1800
                except Exception:
                    _stale = False
            out.append({"pipeline": r.get("pipeline"), "scope": r.get("scope"), "status": _st,
                        "started_at": _sa, "duration_ms": int(r.get("duration_ms") or 0),
                        "rows": int(r.get("rows_affected") or 0),
                        "has_error": bool(r.get("has_error")), "stale": _stale})
    except Exception:
        return []   # 表未建/查询失败 → 空列表(调用方按"无数据"展示), 不影响 /health 的 ok
    return out


# ── 增量重建队列(2026-11-02 S2-2) ──────────────────────────────────────
# 核心命题: 清洗页导入的不是"事件流", 而是"一批行"。一批文件可以横跨任意业务日期
#   (补录上月初 / 补上一季度), 而 ordered_at 与"什么时候导入"没有任何关系。
# 所以重建范围只能由**受影响的业务日期集合**决定, 不能由导入时间窗口推导 ——
#   后者会让补录的历史日期永远落不进重建窗口, 看板少算且不报错。
# 用表不用内存: 后台重建线程可能随实例回收被打断, 内存队列会丢;
#   (channel,date) 主键又天然去重, 同一天反复导入只重建一次。

def _norm_day(v):
    """规整为 'YYYY-MM-DD'; 不合法返回空(宁可漏标也不要标错日期)"""
    s = str(v or "")[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-" and s.replace("-", "").isdigit():
        return s
    return ""


# 物化窗口(天): _rebuild_day_agg / 快照 / 对账三处必须一致, 改一处就要改全部
AGG_WINDOW_DAYS = 90


def _shift_day(day, n):
    return (datetime.strptime(str(day)[:10], "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def _runs(days, span=7):
    """把日期集合切成「连续区间」, 单段最多 span 天 —— 供增量重建合批用

    为什么要合段: 逐日重建每天一次事务往返(实测单日 1.3~2.6s, 时间基本花在往返而非计算),
    补录一段 5 天就是 5 次往返。合成的代价只是 SQL 多一个 BETWEEN。
    为什么要限 span: 段越长事务越大, 超过 ~7 天会重新踩到"大事务持锁 → 并发 9004 锁超时"。
    """
    _ds = sorted({_norm_day(d) for d in (days or [])} - {""})
    out = []
    i = 0
    while i < len(_ds):
        j = i
        while (j + 1 < len(_ds) and (j + 2 - i) <= span
               and _shift_day(_ds[j], 1) == _ds[j + 1]):
            j += 1
        out.append((_ds[i], _ds[j]))
        i = j + 1
    return out


def mark_agg_days(channel, days, reason="write"):
    """登记"这些业务日期需要重建"(幂等: 同一天只留一行, 重复标记刷新 ts)

    同步把 agg_dirty 置位 → summary 在此期间**降级为直查 orders**(宁可慢一拍,
    不可读缺数据的物化表)。重建清空队列后自动清位。
    返回去重后的天数; 全部非法或在窗口外返回 0。

    **只登记物化窗口内的日期**(与 _rebuild_day_agg(90)/快照 90 天窗口一致):
    更早的日期 agg 本来就不服务(那部分由 summary 自定义区间 / 历史归档页直查),
    登记了也永远不会被重建 → 队列残留 → agg_dirty 永远置位 → 看板永久降级直查。
    """
    _min = (datetime.now(timezone.utc) - timedelta(days=AGG_WINDOW_DAYS)).strftime("%Y-%m-%d")
    _ds = sorted({_norm_day(d) for d in (days or [])} - {""})
    _ds = [d for d in _ds if d >= _min]
    if not _ds:
        return 0
    from db import executemany as _em
    _sql = ("INSERT INTO agg_dirty_days(channel, `date`, reason, ts) "
            "VALUES(%s,%s,%s,NOW(6)) ON DUPLICATE KEY UPDATE ts=NOW(6), reason=VALUES(reason)")
    _em(_sql, [[channel, d, str(reason)[:32]] for d in _ds])
    try:
        from routes.dashboard_maintain import _mark_agg_dirty
        _mark_agg_dirty()
    except Exception as e:
        try_err("common", "agg 脏标记置位失败", e)
    return len(_ds)


def take_agg_days(limit=60):
    """取一批待重建 (channel, date, ts), 日期升序(先补最老的缺口)

    ts 必须一起取出: 重建完成后要用它判断"期间是否又被标记"。
    """
    from db import query as _q
    rows = _q("SELECT channel, `date` AS d, ts FROM agg_dirty_days "
              "ORDER BY `date` ASC LIMIT %s", [max(1, int(limit))]) or []
    return [(str(r.get("channel") or ""), _norm_day(r.get("d")), r.get("ts")) for r in rows]


def clear_agg_day(channel, day, ts):
    """重建成功后出队 —— **仅当 ts 没被刷新**时才真正删除

    为什么比较 ts: 单日重建要几十~几百毫秒, 这期间用户可能又导入了同一天的新数据。
    直接 DELETE 会把那次写入的标记一起吃掉 → 该日永远等不到重建(要等第二天的 sweep)。
    返回 True=已出队; False=期间有新标记, 保留待下轮(本轮不算完成)。
    """
    from db import execute as _e
    return bool(_e("DELETE FROM agg_dirty_days WHERE channel=%s AND `date`=%s AND ts<=%s",
                   [channel, day, ts]))


def agg_pending(channel=None):
    """待重建天数(供读侧判断是否需要绕过物化)"""
    from db import one as _o
    if channel:
        r = _o("SELECT COUNT(*) AS c FROM agg_dirty_days WHERE channel=%s", [channel]) or {}
    else:
        r = _o("SELECT COUNT(*) AS c FROM agg_dirty_days") or {}
    try:
        return int(r.get("c") or 0)
    except Exception:
        return 0


def agg_has_delete(channel):
    """本批脏日期里是否有"删单类"原因(供日销孤儿行清理判断)

    只有删单/永久删才可能让某个 SKU 从窗口里彻底消失; 导入/补录不会。
    在 `_drain_agg_dirty` 出队**之前**查(出队后原因就没了)。
    """
    from db import one as _o
    try:
        r = _o("SELECT COUNT(*) AS c FROM agg_dirty_days WHERE channel=%s "
               "AND reason IN ('order_delete','order_recycle')", [channel]) or {}
        return int(r.get("c") or 0) > 0
    except Exception:
        return False


# ── JWT (HS256, 零依赖) ──────────────────────────────────────────────
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def jwt_secret():
    return os.environ.get("JWT_SECRET", "")  # 必设(缺失则校验全拒——生产必须显式配置)


def create_token(username: str, expire_hours: int = 720) -> str:
    secret = jwt_secret()
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({
        "sub": username, "iat": int(time.time()),
        "exp": int(time.time()) + expire_hours * 3600}).encode())
    sig = _b64(hmac.new(secret.encode(), ("%s.%s" % (header, payload)).encode(), hashlib.sha256).digest())
    return "%s.%s.%s" % (header, payload, sig)


def verify_token(token: str):
    try:
        secret = jwt_secret()
        if not secret:
            return None
        parts = token.split(".")
        if len(parts) != 3:
            return None
        expected = _b64(hmac.new(secret.encode(), ("%s.%s" % (parts[0], parts[1])).encode(),
                                 hashlib.sha256).digest())
        if parts[2] != expected:
            return None
        payload = json.loads(_b64decode(parts[1]))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("sub")
    except Exception:
        return None


# ── 密码哈希 (PBKDF2-HMAC-SHA256, salt:key, 100k 迭代——兼容旧 backend) ──
def hash_password(password: str) -> str:
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return salt.hex() + ":" + key.hex()


def check_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    if ":" not in stored:
        return hashlib.sha256(password.encode("utf-8")).hexdigest() == stored
    try:
        salt_hex, key_hex = stored.split(":")
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                  bytes.fromhex(salt_hex), 100000)
        return hmac.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


# ── 统一异常追踪装饰器(所有路由自动捕获, 堆栈只入库不回传) ──
def traced(handler):
    """包装 FastAPI 路由: 未捕获异常 → {ok:False, error, detail=异常类型名, request_id}(200 状态避免 Makers 500 转页)

    2026-11-02 S0: 原实现把 detail(str(e)[:400]) + tb(完整堆栈 2000 字符)回传浏览器 ——
    本项目所有路由都套了这个装饰器, 所以它才是 traceback 泄漏的**主通道**(比全局 handler 更常走)。
    堆栈里含文件路径/表名/SQL 片段/依赖版本, 不该出现在响应体。
    现在只回 request_id(用户报障凭它), 完整堆栈写 quality_logs 按 rid 检索。
    """
    from functools import wraps
    import traceback as _tb
    import uuid as _uuid
    import asyncio as _ai

    def _err(e):
        _rid = _uuid.uuid4().hex[:12]
        try:
            from db import execute as _e
            _e("INSERT INTO quality_logs(log_type, level, message, details, source) "
               "VALUES(%s,%s,%s,%s,%s)",
               ("api_error", "error",
                ("%s: %s rid=%s" % (type(e).__name__, str(e)[:260], _rid)),
                _tb.format_exc(limit=15)[-1800:], "api"))
        except Exception as _e:
                try_err('common', '静默降级', _e)
        return {"ok": False, "error": "服务处理失败，请稍后重试（错误码 %s）" % _rid,
                "detail": type(e).__name__, "request_id": _rid}

    if _ai.iscoroutinefunction(handler):
        @wraps(handler)
        async def _awrap(*args, **kwargs):
            try:
                return await handler(*args, **kwargs)
            except Exception as e:
                return _err(e)
        return _awrap

    @wraps(handler)
    def _wrap(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except Exception as e:
            return _err(e)
    return _wrap

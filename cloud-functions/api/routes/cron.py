"""原生定时任务路由(方案 B): EdgeOne schedules 调度的外部化任务

PA 版 APScheduler 10 任务中, TiDB 场景需要外部化的核心任务(备份/磁盘/WAL 已在 PA 版自动跳过):
- snapshot:          每天构建日销快照(增量补全最近 90 天)
- freshness:         每小时检查快照新鲜度(>2 天陈旧自动重建)
- archive:           每天归档 90 天前订单 → daily_stats(写入失败保护不删)
- cleanup-logs:      每天清理 quality_logs(按天保留 30 天 + 2 万条硬上限)
- daily-rules:       每天滞销识别告警 + 孤儿告警清理
- recycle:           每天清理超 30 天软删数据(订单/规则)
- push-alerts:       每 30 分钟推送新告警到 webhook(设置页 webhook_url)

安全: /cron/* 放行鉴权中间件, 路由内校验 CRON_SECRET(仅 X-Cron-Secret 头——弃 query; hmac.compare_digest)
"""
import os
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi import Request

from db import query, one, execute
from routes.common import ok, fail, traced, try_err, log_info
from routes.common import pipeline_run as _pipe  # 2026-11-02 S1-2: 运行流水
from routes.common import ch_codes as _CHANNELS  # 2026-11-02 S1-5: 渠道数据驱动(原各处硬编码 ("jd","other"))
from routes.common import ch_conf as _CH_CONF
from routes.common import SALES_SQL as _PAID_SQL  # 2026-10-29 销量池单一真源(原硬编码 已完成)
from core.alert_lifecycle import close as _close_alerts  # 2026-11-03 D8: 关闭统一入口
from core.clock import now_cn

router = APIRouter(tags=["cron"])
@router.get("/cron/continue-tasks")
@traced
def continue_tasks(request: Request = None):
    """2026-10-14: 续跑所有 running 的 cleansing/seed/export 任务(后端跟进——app 后台/页面不驻留任务也推进)
    由平台调度(每 5 分钟)或手动调用; 每任务触发一次对应 status 续跑(窗口推进)

    2026-11-02 S0: 本端点此前**既无鉴权也无护栏**(唯一一个裸的) —— 匿名 GET 即可反复触发。
    加进程内 30s 限频(正常调用方是中间件 60s 一次 + 平台每天一次, 不会被影响)。

    2026-11-06 P2-13(审计): 补上鉴权。限频只是"别太频繁", 不解决"谁能打" ——
      匿名请求照样能让实例去遍历 running 任务逐个推进(每条都是一个重活), 是可被外部
      消耗 RU 的入口。这里套与其它 cron 端点同一套判据。
      ⚠️ 内部调用是 index.py 的**直接函数调用**(continue_tasks()), 没有 Request 对象 ——
      所以 request is None 一律放行, 中间件那条保活链路零影响。"""
    if not _authed_sync(request):
        return fail("unauthorized", 401)
    if not _throttle("continue-tasks", 30):
        return ok({"skipped": True, "reason": "30 秒内已推进过"})
    from db import query as _qc, execute as _ec, one as _oc
    from datetime import datetime, timedelta as _td2, timezone as _tz2
    done = {"cleansing": 0, "cleansing_eval": 0, "seed": 0, "export": 0, "timeout": 0, "skipped": 0}
    try:
        rows = _qc("SELECT task_id, task_type, updated_at FROM sync_tasks WHERE status='running' LIMIT 20")
        _cutoff = (now_cn() - _td2(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        # 2026-11-05 根治「非真异常连续噪音」: 这行诊断原无条件走 try_err(静默吞异常
        #   通道, 固定 warning/quiet_error)。而空库/无 running 任务时 rows=0 是**正常空态**,
        #   中间件每 60s 调一次 → 每分钟一条警告, 永不停止, 把真异常淹掉。
        #   现在空态零写入(返回值 ok(done) 已表达结果), 真有任务才以 info 留一条摘要;
        #   真异常仍由下面「续跑异常 / 整体异常」两处 try_err 原样留痕。
        if rows:
            log_info('cron', 'continue-tasks: 待推进 %d 个 running 任务' % len(rows))
        for r in (rows or []):
            tt = r.get("task_type") or ""
            tid = r.get("task_id") or ""
            ua = str(r.get("updated_at") or "")[:19].replace("T", " ")  # T vs 空格分隔——字符串比较需归一(否则 10:34 T 版 > cutoff 空格版误判不超时)
            if ua and ua < _cutoff:
                # 超时任务(>30 分钟无更新——前端轮询停/实例冻结)——标记 error 释放防重/结束卡住
                _ec("UPDATE sync_tasks SET status='error', result=%s WHERE task_id=%s",
                    [json.dumps({"result": {"error": "任务超时(30 分钟无更新)——自动标记结束，请重新导入"}}), tid])
                done["timeout"] += 1
                continue
            try:
                if tt == "cleansing":
                    from .cleansing import cleansing_status
                    _row2 = _qc("SELECT params FROM sync_tasks WHERE task_id=%s", [tid])
                    if _row2:
                        _pj = json.loads((_row2[0].get("params") or "{}"))
                        _ec("UPDATE sync_tasks SET params=%s WHERE task_id=%s",
                            [json.dumps({**_pj, "driver": "backend"}, ensure_ascii=False), tid])
                    cleansing_status(tid, page_size=5000, driver="backend")
                    done["cleansing"] += 1
                elif tt == "cleansing_eval":
                    from .cleansing import cleansing_eval_status
                    cleansing_eval_status(tid, page_size=500, driver="backend")
                    done["cleansing"] += 1
                elif tt == "seed":
                    from .task_seed import seed_fill_status
                    seed_fill_status(tid, driver="backend")
                    done["seed"] += 1
                elif tt == "export":
                    from .task_export import export_status
                    _row2 = _qc("SELECT params FROM sync_tasks WHERE task_id=%s", [tid])
                    if _row2:
                        _pj = json.loads((_row2[0].get("params") or "{}"))
                        _ec("UPDATE sync_tasks SET params=%s WHERE task_id=%s",
                            [json.dumps({**_pj, "driver": "backend"}, ensure_ascii=False), tid])
                    export_status(tid, driver="backend")
                    done["export"] += 1
                else:
                    done["skipped"] += 1
            except Exception as _e:
                try_err('cron', 'continue-tasks 续跑异常[%s/%s]' % (tt, tid), _e)
                done["skipped"] += 1
    except Exception as _e:
        try_err('cron', 'continue-tasks 整体异常', _e)
    return ok(done)






# ── 调度护栏(2026-11-02 S0): 把"调用者是谁"换成"今天该不该跑" ──────────────
# 前提事实(非推测, 见 docs/security.md): EdgeOne schedules 的 schema 只有
# name/cron/timezone/method/path/payload, **没有 headers 字段**, payload 又不支持环境变量插值,
# 而 edgeone.json 本身在公开仓库 —— 所以"让平台带着密钥来"在物理上做不到。
# 既无法识别调用方, 就不去猜, 改守"执行次数": 每个重成本/破坏性任务当日只允许真正执行一次,
# 重复调用直接返回 skipped(只花一次 COUNT 级查询)。这样即使路径被公开、被脚本狂刷,
# 后果也只是"多几次 INSERT IGNORE", 而不是"RU 打爆 / 日志被清空 / webhook 被骚扰"。
#
# 两道锁:
#   ① 进程内限频(挡高频轰炸, 冷启动失效, 所以不是唯一防线)
#   ② DB 当日抢占(maintenance_log 主键, 多实例安全) + **失败回滚**(否则当天永久跳过)
_RUN_AT = {}
_RUN_LOCK = threading.Lock()


def _throttle(key, seconds):
    """进程内限频: 同一 key 在 seconds 内只放行一次。返回 True=可以执行"""
    now = time.time()
    with _RUN_LOCK:
        if now - _RUN_AT.get(key, 0) < seconds:
            return False
        _RUN_AT[key] = now
        return True


def _claim(task):
    """当日一次性抢占(多实例安全)。返回 True=抢到, 应执行。
    表异常时放行执行 —— 宁可多跑一次, 也不要因为护栏自己出错而整天空转。"""
    today = now_cn().strftime("%Y-%m-%d")
    try:
        return bool(execute("INSERT IGNORE INTO maintenance_log(`date`, task) VALUES(%s,%s)",
                            [today, task]))
    except Exception as _e:
        try_err('cron', '抢占标记失败(放行执行)', _e)
        return True


def _unclaim(task):
    """执行失败回滚抢占标记 —— 不回滚=当天永久跳过(dashboard._daily_maintenance 就是栽在这)"""
    today = now_cn().strftime("%Y-%m-%d")
    try:
        execute("DELETE FROM maintenance_log WHERE `date`=%s AND task=%s", [today, task])
    except Exception as _e:
        try_err('cron', '回滚抢占标记失败', _e)


# ── 时间窗守卫(2026-11-03): 把"谁在调"换成"现在是不是这个任务该跑的时候" ──────
# 上面已定案: 平台 schedules 无法注入 header, 应用层拿不到可靠身份。既然认不出人,
# 就再叠一层"时间窗" —— 每个任务只在自己的调度窗口内有效, 窗口外直接 skipped 且
# **一行 SQL 都不发**(零 RU)。这让"鉴权缺失"的残余风险从"任何时候可被触发"收敛到
# "只在每日固定窗口内可被触发, 且被 _claim 锁成当日一次": 外部扫描即使猜中路径,
# 在错误时间调用没有任何副作用, 在窗口内抢先调用也只是把该跑的任务提前跑了。
#
# 窗口按 Asia/Shanghai 显式计算 —— 容器跑在 overseas 区域(ap-singapore), 不能依赖 TZ。
# 窗口边界依据(2026-11-03 查 pipeline_runs 实测, 不是拍脑袋):
#   archive 配置 01:05 CST → 实跑 17:04:03 UTC(= 01:04:03 CST)
#   cleanup 03:05→19:04:02 / freshness 03:10→19:09:00 / snapshot 03:35→19:34:01
#   daily-rules 04:05→20:04:01 —— 六个任务**一致地早 57~60 秒**(平台按分钟取整 + 排队)。
# 所以每个窗口两端各留 ≥1.5 小时余量, 既不会被 1 分钟偏差误杀, 也不会把大半天的
# 任意时刻都算作"窗口内"。
_SH_TZ = timezone(timedelta(hours=8))
_WINDOWS = {
    "archive":      (0, 30, 4, 0),
    "cleanup_logs": (2, 30, 5, 30),
    "freshness":    (2, 30, 5, 30),
    "snapshot":     (2, 30, 5, 30),
    "daily_rules":  (3, 30, 6, 0),
    "reconcile":    (3, 30, 6, 0),
    "recycle":      (4, 0, 6, 30),
    "push_alerts":  (8, 0, 10, 0),
    # 账期结账: 平台每月 1 号 00:40 CST 调度, 窗口给到 03:00(排队/取整余量)
    "ledger_close": (0, 30, 3, 0),
}
# 不设窗的两个:
#   ping            —— 保活用, 全天触发无害, 且 UptimeRobot 也在打
#   continue_tasks  —— 被 index.py 请求驱动**直接调用**(不走 HTTP), 设窗会连带打死续跑


def _force_ok(request):
    """force=1 + 正确的 X-Cron-Secret → True(窗口外也放行)

    运维补跑入口。为什么必须有: 数据重建/迁移/修完 bug 之后, 用户要的是"现在补一次",
    而窗口守卫会把它挡回"00:30 再来"。窗口守卫防的是**调度抖动**(平台时间漂移导致的
    重复执行), 不是防运维 —— 所以带密钥的显式 force 应当放行。没密钥/密钥错则完全按
    普通调用走(窗口+当日抢占), 匿名 force 不会变成放大器。
    """
    if str(request.query_params.get("force") or "") not in ("1", "true", "yes"):
        return False
    _sec = os.environ.get("CRON_SECRET", "")
    _h = request.headers.get("X-Cron-Secret", "")
    if not (_sec and _h):
        return False
    try:
        import hmac as _hm
        return _hm.compare_digest(_h.encode(), _sec.encode())
    except Exception:
        return False


def _off_window(task, force_ok=False):
    """None = 在窗口内(可执行); 否则返回 skip 响应体。未登记的任务一律放行。

    force_ok=True 表示调用方已用 `_force_ok(request)` 验过密钥, 跳过窗口限制。
    """
    w = _WINDOWS.get(task)
    if not w or force_ok:
        return None
    now = datetime.now(_SH_TZ)
    cur = now.hour * 60 + now.minute
    lo, hi = w[0] * 60 + w[1], w[2] * 60 + w[3]
    if lo <= cur <= hi:
        return None
    # 窗口外被拒要**留痕**: 万一哪天平台调度时间变了(或配置被改), 任务会静默停跑 ——
    # 这是本守卫唯一的新增风险, 所以每小时最多记一条 warning(限频, 防外部扫描刷日志)。
    # 看到 "在窗口外被拒" 连续出现 = 窗口该改, 不是"攻击"。
    if _throttle("offwin:" + task, 3600):
        try:
            _log("warning", "cron %s 在窗口外被拒: now=%02d:%02d CST, window=%02d:%02d-%02d:%02d"
                 % ((task, now.hour, now.minute) + w))
        except Exception:
            pass
    return {"skipped": True, "reason": "窗口外(非调度时段)",
            "window": "%02d:%02d-%02d:%02d CST" % w}


async def _authed(request: Request) -> bool:
    """cron 鉴权(2026-11-01 定案: 定时任务迁回平台 schedules)。
    EdgeOne schedules 的 schema 只有 name/cron/timezone/method/path/payload, **无法注入自定义请求头**;
    官方文档确认 payload 不支持环境变量插值, 而 edgeone.json 进公开仓库 —— 用 payload 传密钥
    等于把密钥公开(实测网关也不会为普通请求改写 Authorization, 故 q-sign 判据同样不可用)。

    因此回到 security.md 既定的保留方案: **由平台签名(q-sign / 网关侧鉴权)作为保护层**,
    应用层不再强校验密钥。两条通道仍然保留, 供人工/CI 显式调用时使用:
      (1) X-Cron-Secret 头
      (2) POST body 的 _k 字段
    配置了 CRON_SECRET 时, 带正确密钥 → 放行; 不带 → 放行但**记一条审计日志**,
    以便线上观察"无密钥调用"的频率(平台调度会持续产生, 外部扫描也会产生, 可据此发现异常量)。
    如需回到强校验: 设 CRON_STRICT=1 即恢复"无密钥一律拒绝"(此时平台调度会失效, 慎用)。

    2026-11-02 补充: 本函数**不再是唯一防线**。因无法识别调用方(见上方护栏注释),
    每个端点另加 _throttle + _claim 两道执行次数锁 —— 即使本函数放行, 重复调用也不会真正执行。
    审计日志改为限频写(原实现每个匿名请求写一行 quality_logs, 本身就是一条 RU 消耗通道)。
    """
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        return True  # 未配置 → 放行(旧行为)
    import hmac as _hm
    h = request.headers.get("X-Cron-Secret", "")
    if h and _hm.compare_digest(h.encode(), secret.encode()):
        return True
    try:
        raw = await request.body()
        if raw:
            k = str((json.loads(raw) or {}).get("_k", ""))
            if k and _hm.compare_digest(k.encode(), secret.encode()):
                return True
    except Exception:
        pass
    # 无密钥调用: 默认放行(平台 schedules 通道), 严格模式直接拒
    if str(os.environ.get("CRON_STRICT", "")) == "1":
        return False
    try:
        # 限频写: 原实现每个匿名请求都写一行 —— 匿名扫描既耗 RU, 又会把 quality_logs 冲掉
        if _throttle("audit:" + request.url.path, 300):
            _log("info", "cron 无密钥调用(平台调度/外部扫描无法区分): %s %s"
                 % (request.method, request.url.path))
    except Exception:
        pass
    return True


def _authed_sync(request):
    """`_authed` 的同步版(只读 header, 不读 body) —— 供同步端点(continue_tasks)使用

    语义与 `_authed` 保持一致:
      · 未配 CRON_SECRET → 放行(旧行为);
      · 带了正确的 X-Cron-Secret → 放行;
      · CRON_STRICT=1 且没带 → 拒绝;
      · 其余(平台 schedules 无法注入 header)→ 放行, 但限频记一条审计日志。
        "记而不拒"是既定口径: 平台调度和外部扫描无法区分, 强拒会把正常调度一起打死。

    `request is None` → 内部直接函数调用(index.py 中间件), 不走 HTTP, 一律放行。
    """
    if request is None:
        return True
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        return True
    import hmac as _hm
    h = request.headers.get("X-Cron-Secret", "")
    if h and _hm.compare_digest(h.encode(), secret.encode()):
        return True
    if str(os.environ.get("CRON_STRICT", "")) == "1":
        return False
    try:
        if _throttle("audit:" + request.url.path, 300):
            _log("info", "cron 无密钥调用: GET %s" % request.url.path)
    except Exception:
        pass
    return True


def _log(level, message, source="cron"):
    """写 quality_logs —— 参数化(勿用 % 拼接: 多 %s 单值会 TypeError 被吞致全 cron 日志从未写入)"""
    try:
        execute("INSERT INTO quality_logs(log_type, level, message, source) VALUES('cron',%s,%s,%s)",
                (level, message, source))
    except Exception as _e:
        # 记录 _log 自身失败(不再静默)
        try:
            execute("INSERT INTO quality_logs(log_type, level, message, details, source) "
                    "VALUES('maint','error',%s,%s,'cron')",
                    ("_log 写入失败", str(_e)[:300]))
        except Exception as _e:
                try_err('cron', '静默降级', _e)


def _build_snapshot(rebuild_days=90):
    """增量构建日销快照: 聚合最近 rebuild_days 天已支付订单(幂等 upsert)"""
    start = (now_cn() - timedelta(days=rebuild_days)).strftime("%Y-%m-%d")
    execute(
        "INSERT INTO daily_sales_snapshot(date, channel, sku, barcode, warehouse, order_count) "
        "SELECT DATE(ordered_at), channel, sku, COALESCE(barcode,''), warehouse, SUM(quantity) FROM orders "
        "WHERE order_status IN (%s) AND (deleted_at IS NULL OR deleted_at='') "
        "AND ordered_at >= %%s "
        "GROUP BY DATE(ordered_at), channel, sku, COALESCE(barcode,''), warehouse "
        "ON DUPLICATE KEY UPDATE order_count=VALUES(order_count)" % _PAID_SQL, [start])
    r = one("SELECT COUNT(*) AS c FROM daily_sales_snapshot") or {}
    return int(r.get("c") or 0)


@router.api_route("/cron/snapshot", methods=["GET","POST"])
@traced
@_pipe("snapshot", rows_key=("snapshot_rows","rows"))
async def cron_snapshot(request: Request):
    """日销快照构建。2026-11-02 S0: 全量重建 90 天是 RU 最重的操作之一, 加当日一次性抢占"""
    if not await _authed(request):
        return fail("未授权", 401)
    _off = _off_window("snapshot", force_ok=_force_ok(request))
    if _off:
        return ok(_off)
    if not _throttle("snapshot", 300):
        return ok({"skipped": True, "reason": "5 分钟内已执行"})
    if not _claim("snapshot"):
        return ok({"skipped": True, "reason": "今天已构建(另一实例或应用层兜底)"})
    try:
        n = _build_snapshot()
    except Exception:
        _unclaim("snapshot")   # 失败回滚: 否则当天不再重试
        raise
    _log("info", "日销快照构建完成, 快照总行数 %d" % n)
    return ok({"snapshot_rows": n})


@router.api_route("/cron/freshness", methods=["GET","POST"])
@traced
@_pipe("freshness", rows_key=("rows",))
async def cron_freshness(request: Request):
    """快照新鲜度守护: MAX(date) < 今天-2 天 → 重建
    maintenance_log(date+'snapshot') 抢占 —— 与应用层自愈互斥, 一天只重建一次

    2026-11-02 S0 修正: 原实现**先抢占再判断是否陈旧** —— 不陈旧时也占了 'snapshot' 标记,
    导致 3:35 的 build-snapshot 恒返回 skipped(顺序 3:10 freshness 在前)。
    改为"先判断, 确实要重建才抢占"。"""
    if not await _authed(request):
        return fail("未授权", 401)
    _off = _off_window("freshness", force_ok=_force_ok(request))
    if _off:
        return ok(_off)
    r = one("SELECT COALESCE(MAX(date),'') AS m FROM daily_sales_snapshot") or {}
    m = str(r.get("m") or "")
    stale_limit = (now_cn() - timedelta(days=2)).strftime("%Y-%m-%d")
    if m and m >= stale_limit:
        return ok({"rebuilt": False, "max_date": m})
    if not _claim("snapshot"):
        return ok({"skipped": True, "reason": "今天已自愈(应用层兜底或另一实例)"})
    try:
        n = _build_snapshot()
    except Exception:
        _unclaim("snapshot")
        raise
    _log("warning", "快照陈旧(max=%s), 已重建(%d 行)" % (m or "无", n))
    return ok({"rebuilt": True, "rows": n})


@router.api_route("/cron/archive", methods=["GET","POST"])
@traced
@_pipe("archive", rows_key="archived")
async def cron_archive(request: Request):
    """归档 365 天前订单 → daily_stats(2026-10-06 保留期 90→365 天: 年同比价值+容量安全<5GB; 写入成功才删除)

    2026-11-02 S0: 全表拉取 + 逐条 INSERT, 是本项目最重的操作; 加当日一次性抢占。"""
    if not await _authed(request):
        return fail("未授权", 401)
    _off = _off_window("archive", force_ok=_force_ok(request))
    if _off:
        return ok(_off)
    if not _throttle("archive", 600):
        return ok({"skipped": True, "reason": "10 分钟内已执行"})
    if not _claim("cron:archive"):
        return ok({"skipped": True, "reason": "今天已归档"})
    # 2026-11-06 S6/P0-11: 逐条 INSERT(50 万次往返) → core.archive 集合式批次
    #   (每区间 1 条 INSERT...SELECT...GROUP BY + 1 条 DELETE, 同事务)
    from core.archive import archive_orders as _arch
    _r = _arch(cutoff_days=365)
    if _r.get("failed_batches"):
        # 失败区间已整体回滚(账单一致), 其余区间照常；留 error 让监控能看见
        _log("error", "归档部分失败: %d 区间回滚, 已归档 %d 行, 首个错误: %s"
             % (_r["failed_batches"], _r["archived"], (_r.get("error") or "")[:120]))
    _log("info", "归档: %d 订单 → daily_stats(%d 行, %d 区间, 切线 %s)"
         % (_r["archived"], _r["stats_rows"], _r["batches"], _r["cutoff"]))
    return ok(_r)


@router.api_route("/cron/cleanup-logs", methods=["GET","POST"])
@traced
@_pipe("cleanup_logs", rows_key="old")
async def cron_cleanup_logs(request: Request):
    """日志清理: 按天保留(默认30天) + 2万条硬上限 —— 见 common.prune_quality_logs

    2026-11-02 S1-3: 原「保留最近 500 条」按条数截断, 高峰期一天就把前一天的日志冲掉,
    出问题查不到证据; 现改按 created_at 保留(与 dashboard 每日维护共用同一实现)。
    2026-11-02 S0: 删日志不可逆, 加当日一次性抢占。
    """
    if not await _authed(request):
        return fail("未授权", 401)
    _off = _off_window("cleanup_logs", force_ok=_force_ok(request))
    if _off:
        return ok(_off)
    # 月结随本条调度执行(2026-11-04): edgeone.json 的 schedules 上限 10 条已占满,
    #   加第 11 条整组定时任务会丢路由, 所以复用最轻的这条凌晨任务, 不新增调度。
    #   run_ledger_close() 在每月 4 号之后第一行就返回, 一条 SQL 都不发 —— 日均成本为零。
    #   必须放在 _claim 之前: 日志当天已清理过(手动跑过一次)也要照常结账,
    #   不能让"今天已清理"这个抢占把月结吞掉。
    _led = None
    try:
        _led = run_ledger_close()
    except Exception as _e:
        try_err("ledger", "月结(随日志清理调度)异常", _e)
        _led = {"ok": False, "reason": str(_e)[:200]}
    # 只留摘要: 逐渠道明细会跟着接口返回, 平台日志里没必要铺开几十行
    if isinstance(_led, dict):
        _led = {"period": _led.get("period", ""), "ok": _led.get("ok_count", 0),
                "skipped": _led.get("skipped"), "reason": _led.get("reason", ""),
                "blocked": [{"channel": x.get("channel"), "reason": x.get("reason")}
                            for x in (_led.get("blocked") or [])]}
    if not _claim("cron:cleanup_logs"):
        return ok({"skipped": True, "reason": "今天已清理", "ledger": _led})
    from routes.common import prune_quality_logs
    r = prune_quality_logs()
    _log("info", "日志清理: 过期删除 %d 条 / 上限删除 %d 条 / 现存 %d 条"
         % (r.get("old", 0), r.get("cap", 0), r.get("total", 0)))
    r["ledger"] = _led
    return ok(r)


def run_daily_rules(channels=None, deep=True, alert_types=None) -> dict:
    """规则评估 —— 两档(2026-11-03 D4: 改一条规则不该等于跑一整套每日任务)

    deep=True  每日任务口径: 孤儿全表清理 + 旧 SKU 级清理 + 无需求兜底 + 全渠道评估
               + 批次对账 + 健康快照。(cron/daily-rules、应用层兜底走这档)
    deep=False 规则变更口径: 只评指定渠道 + 该渠道孤儿清理; 跳过三条全表 UPDATE、
               批次对账与健康快照 —— 后者与"改了一条规则"毫无关系, 却占了几乎全部成本。
    channels   None=全渠道
    alert_types 只评这些 alert_type(规则变更时精确到受影响规则; None=全部规则)

    证据: 规则保存原来无条件调本函数, 一次文案修改 = 3 条全表 UPDATE(孤儿清理)
            + 10 万行 × N 渠道评估 + 全表 HAVING 对账 + 健康快照。
    """
    # 2026-11-06 S4: 评估开始前先把挂住的 running 收尾 —— 上一次被平台掐断时收尾语句不会执行,
    #   留着"running"会让 /health 与排障永远看到"有任务在跑"(线上实证停了 5 小时)。
    try:
        from core.maintenance import close_stale_runs as _csr
        _csr()
    except Exception as _e_csr:
        try_err('cron', '挂起运行收尾降级', _e_csr)
    _chs = [c for c in (channels or _CHANNELS()) if c] or list(_CHANNELS())
    _ats = [str(a) for a in (alert_types or []) if a]
    _in_ch = "(" + ",".join(["%s"] * len(_chs)) + ")"
    # 1. 孤儿告警清理: active 且 source in (rules_engine,event_bus) 的 alert_type 已无 active 规则 → inactive
    cleaned = 0
    _orphan_rows = query(
        "SELECT DISTINCT alert_type, channel FROM alerts "
        "WHERE status='active' AND source IN ('rules_engine','event_bus')"
        + ("" if deep else " AND channel IN " + _in_ch),
        [] if deep else _chs)
    for r in _orphan_rows:
        at = r.get("alert_type")
        ch = r.get("channel") or "jd"
        has = one("SELECT COUNT(*) AS c FROM rules WHERE alert_type=%s AND channel=%s AND is_active=1 "
                  "AND (deleted_at IS NULL OR deleted_at='')", [at, ch]) or {}
        if not int(has.get("c") or 0):
            cleaned += int(_close_alerts(
                "alert_type=%s AND channel=%s AND source IN ('rules_engine','event_bus')",
                [at, ch], "orphan_rule_missing") or 0)
    if deep:
        # 1.5 旧 SKU 级规则告警(warehouse 为空)清理 —— 告警已逐仓化(SKU×仓), 旧的 SKU 聚合
        #     告警由下方全量评估重新生成逐仓版, 先关闭避免新旧混杂致看板计数翻倍
        #     (迁移性清理: 一次即可, 不该在每次保存规则时重跑)
        try:
            cleaned += int(_close_alerts(
                "source IN ('rules_engine','event_bus') AND (warehouse IS NULL OR warehouse='')",
                [], "legacy_sku_level") or 0)
        except Exception as _e:
            try_err('cron', '静默降级', _e)
        # 1.6 无需求告警兜底(2026-10-15): 无未删订单 且 无在档(products/inventory 均无) 的 active
        #     rules_engine 告警 → inactive —— SKU 只有订单页有(订单删了=无需求)——告警孤儿
        #     注: 去掉表别名, 让 SET 侧(close 统一实现)与子查询同时成立
        try:
            cleaned += int(_close_alerts(
                "source='rules_engine' "
                "AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.sku=alerts.related_sku AND o.channel=alerts.channel "
                "AND (o.deleted_at IS NULL OR o.deleted_at='')) "
                "AND NOT EXISTS (SELECT 1 FROM products p WHERE p.sku=alerts.related_sku AND p.channel=alerts.channel "
                "AND (p.deleted_at IS NULL OR p.deleted_at='')) "
                "AND NOT EXISTS (SELECT 1 FROM inventory i WHERE i.sku=alerts.related_sku AND i.channel=alerts.channel)",
                [], "no_demand") or 0)
        except Exception as _e:
            try_err('cron', '静默降级', _e)
    # 2. 全量规则评估(替代简化滞销逻辑): inventory.changed + scheduled.daily 遍历库存行
    #    双渠道逐仓(其他渠道此前完全无每日规则评估 —— 完整性修复); 全量行(含 SKU×仓)
    from core.rules import evaluate_stock_skus
    triggered = []
    from routes.common import track_run as _tr
    for _ch in _chs:
        # 2026-11-02 S1-2: 逐渠道留一条运行记录(scope=渠道) —— 出问题时一眼看出哪个渠道没评估到
        with _tr("rule_eval", _ch) as _rec:
            _hits = evaluate_stock_skus(_ch, limit=100000, only_ats=_ats or None)
            _rec.rows = len(_hits or [])
            _rec.detail = {"triggered": len(_hits or []), "deep": bool(deep), "ats": _ats}
        triggered.extend(_hits or [])
    # 2026-10-29 批次对账(四维逐一, 用户定案): 
    #   对每个 (仓库类型, SKU, 69码, 仓库) 校验 Σ(batches.qty) ≡ inventory.available_qty
    #   **不能按 SKU 汇总核对** —— 不同仓/不同码的偏差会互相抵消(A 多 100 与 B 少 100 汇总后为 0),
    #   掩盖真实错配。四维逐一比对才能定位到具体仓/具体码。
    #   偏差只告警不自动修(自动修会掩盖导入遗漏/仓名错/批次未填等根因)
    #   D4: 这是全表 HAVING, 只在每日档跑 —— 规则变更不影响批次与库存的一致性
    try:
        from routes.cleanse_write import SELF_WAREHOUSE_TYPES as _SELF
        _ph = ",".join(["%s"] * len(_SELF))
        for _ch in (_chs if deep else []):
            # 2026-11-06 S6/P1-31: 相关子查询(每行 inventory 跑一次 SUM batches, 18 万批次
            #   × 每行一次) → LEFT JOIN 聚合子查询(整批只算一遍) + WHERE 过滤。
            #   原来的 `HAVING ABS(qty-bq)>1` 无 GROUP BY 是 MySQL 扩展写法, 语义等价但
            #   不直观; 改 WHERE 后与常规聚合查询同形, 也便于 EXPLAIN 判断。
            _bad = query(
                "SELECT i.sku, COALESCE(i.barcode,'') AS bc, i.warehouse, "
                "  i.available_qty AS qty, COALESCE(b.bq, 0) AS bq "
                "FROM inventory i "
                "LEFT JOIN (SELECT sku, COALESCE(barcode,'') AS bc, warehouse, warehouse_type, "
                "                  SUM(qty) AS bq FROM batches WHERE channel=%%s "
                "           GROUP BY sku, COALESCE(barcode,''), warehouse, warehouse_type) b "
                "  ON b.sku=i.sku AND b.bc=COALESCE(i.barcode,'') "
                " AND b.warehouse=i.warehouse AND b.warehouse_type=i.warehouse_type "
                "WHERE i.channel=%%s AND i.warehouse_type IN (%s) "
                "  AND ABS(i.available_qty - COALESCE(b.bq, 0)) > 1" % _ph,
                [_ch, _ch] + list(_SELF))
            if _bad:
                _log("warning", "批次对账偏差 %s: %d 项 (Σ批次 ≠ 可用, 按仓库类型+SKU+69码+仓逐一比对)"
                     % (_ch, len(_bad)))
                _s0 = _bad[0]
                execute("INSERT INTO quality_logs(log_type, level, message, source) "
                        "VALUES('quiet_error','warning',%s,'cron')",
                        ("批次对账 %s: %d 项偏差, 例 sku=%s 69码=%s 仓=%s 可用=%s 批次=%s "
                         "(需补全入库/出库的批次信息后重建)"
                         % (_ch, len(_bad), _s0.get("sku"), _s0.get("bc") or "-",
                            _s0.get("warehouse"), _s0.get("qty"), _s0.get("bq")),))
    except Exception as _e_rc:
        try_err('cron', '批次对账降级', _e_rc)
    # 2026-11-06 S2-1: 评估跑完 → 采购需求重判(purchase_need 告警)。
    #   采购告警原来由 `_build_purchase` 顺手写, 于是"有没有人打开采购页"决定了它更不更新;
    #   现在写入收敛到 core.alert_jobs, 这里是四个写侧触发源之一(评估会关闭一批告警,
    #   采购需求必须跟着重判)。异步 + 每渠道 120s 限频, 不拖慢评估本身。
    try:
        from core.alert_jobs import kick_purchase_alerts as _kick_pa
        for _ch in _chs:
            _kick_pa(_ch)
    except Exception as _e_pa:
        try_err('cron', '采购告警同步调度降级', _e_pa)
    _log("info", "规则评估[%s]: 渠道 %s, 孤儿告警清理 %d, 规则触发 %d 个(%s)" % (
        "每日" if deep else "变更", ",".join(_chs), cleaned, len(triggered),
        ",".join(str(t)[:20] for t in triggered[:8])))
    # P2 回溯校验 Lite: 濒临断货判定摘要写入 quality_logs(审计/漏报误报分析基础)
    # D4: 健康快照按天唯一(ON DUPLICATE), 每日写一次即可 —— 规则变更档不重复写
    try:
        from routes.dashboard import _stock_risk, _health_index
        from routes.common import ch_default_mode as _cdm
        for _ch in (_chs if deep else []):
            _rk = _stock_risk(_ch)
            # 2026-11-06 S3: 原 `_health_index(_ch)` 用签名默认 mode="bbcc" → other 渠道按
            #   jd 的 12 天周期算安全线(判出来的"健康"是假的); 且下面又调了第二次
            #   (`_hix`) 只为拿同一个对象 —— 同一个渠道同一次评估算两遍健康分。
            _hi = _health_index(_ch, _cdm(_ch))
            _t = (_rk.get("bcTotal", 0) or 0) + (_rk.get("cTotal", 0) or 0) + (_rk.get("ownTotal", 0) or 0)
            _r = (_rk.get("bcCritical", 0) or 0) + (_rk.get("cCritical", 0) or 0) + (_rk.get("ownCritical", 0) or 0)
            _o = (_rk.get("bcWarning", 0) or 0) + (_rk.get("cWarning", 0) or 0) + (_rk.get("ownWarning", 0) or 0)
            _hs = str(_hi.get("score") if _hi else "-")
            _ho = str((_hi or {}).get("own", {}).get("score") if _hi else "-")
            _hp = str((_hi or {}).get("platform", {}).get("score") if _hi else "-")
            _hb = str((_hi or {}).get("bc", {}).get("score") if _hi else "-")
            try:
                _sc2 = _hi.get("score") if _hi else None
                from db import execute as _exH
                _d0 = now_cn().strftime("%Y-%m-%d")
                _exH("INSERT INTO health_snapshot(date, channel, score, own_score, platform_score, bc_score) "
                     "VALUES(%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE score=VALUES(score), own_score=VALUES(own_score), "
                     "platform_score=VALUES(platform_score), bc_score=VALUES(bc_score)",
                     [_d0, _ch, int(_sc2) if _sc2 is not None else -1,
                      int((_hi.get("own") or {}).get("score") or -1),
                      int((_hi.get("platform") or {}).get("score") or -1),
                      int((_hi.get("bc") or {}).get("score") or -1)])
            except Exception as _e:
                    try_err('cron', '静默降级', _e)
            _msg = "[%s] 濒临断货: 共%d(红%d/橙%d/黄%d), BC=%d C=%d OWN=%d | 健康: %s分(own%s/platform%s/bc%s)" % (
                _ch, _t, _r, _o, max(_t - _r - _o, 0),
                _rk.get("bcTotal", 0) or 0, _rk.get("cTotal", 0) or 0, _rk.get("ownTotal", 0) or 0,
                _hs, _ho, _hp, _hb)
            execute("INSERT INTO quality_logs(log_type, level, message, source) "
                    "VALUES('risk_summary','info',%s,'cron')", (_msg,))
    except Exception as _e:
            try_err('cron', '静默降级', _e)
    return {"orphan_cleaned": cleaned, "rules_triggered": triggered,
            "scope": "deep" if deep else "light", "channels": _chs, "ats": _ats}


@router.api_route("/cron/daily-rules", methods=["GET","POST"])
@traced
@_pipe("daily_rules", rows_key="orphan_cleaned")
async def cron_daily_rules(request: Request, force: int = 0, channels: str = "",
                           alert_types: str = ""):
    """每日规则(EdgeOne schedules 触发路径): 校验后调用共享 run_daily_rules()
    maintenance_log(date+'daily_rules') 抢占 —— 与应用层兜底互斥, 一天只评估一次(先到者执行);
    force=1: 运维强制重跑(重置后/数据重建场景, 绕过当天抢占)

    2026-11-02 S0 两处修正:
      ① force=1 此前匿名可传 —— 等于给了一个"每调一次就跑 10 万行全量评估"的免费放大器;
         现在必须带正确 CRON_SECRET 才认, 否则降级为普通调用。
      ② 执行失败回滚抢占标记 —— 原实现失败后当天不再重试(dashboard._daily_maintenance 同款问题)。

    2026-11-06 运维入口收口: force 的 secret 校验**提前到窗口检查之前** ——
      原顺序是"先查窗口再验密", 于是窗口外 force 永远被 skip 挡住, 带 secret 也进不来;
      而"数据重建后补跑某渠道"(如 other 首次完整评估)恰恰发生在窗口外。
      同时支持 channels=a,b / alert_types=x,y 定向重跑 —— 运维不必为看一个渠道跑全渠道。"""
    if not await _authed(request):
        return fail("未授权", 401)
    # 密钥校验统一走 _force_ok(与 archive/cleanup 等运维补跑同一个判据, 不再各写一份 hmac)
    force = 1 if (force and _force_ok(request)) else 0
    if not force:
        _off = _off_window("daily_rules")
        if _off:
            return ok(_off)
    if not force:
        if not _claim("daily_rules"):
            return ok({"skipped": True, "reason": "今天已评估(应用层兜底或另一实例)"})
        try:
            _r = run_daily_rules()
        except Exception:
            _unclaim("daily_rules")
            raise
        return ok(_r)
    _chs = [c.strip() for c in (channels or "").split(",") if c.strip()] or None
    _ats = [a.strip() for a in (alert_types or "").split(",") if a.strip()] or None
    return ok(run_daily_rules(channels=_chs, deep=True, alert_types=_ats))


@router.api_route("/cron/ping", methods=["GET","POST"])
@traced
async def cron_ping(request: Request):
    """调度连通性测试: 无条件写日志 —— 验证 EdgeOne schedules 是否真正触发函数"""
    if not await _authed(request):
        return fail("未授权", 401)
    _log("info", "schedules ping 触发(平台调度验证)")
    return ok({"pong": True, "ts": now_cn().isoformat()})


@router.api_route("/cron/recycle", methods=["GET","POST"])
@traced
@_pipe("recycle", rows_key="deleted")
async def cron_recycle(request: Request):
    """回收站清理: 软删超过 30 天的订单/规则永久删除(deleted_at != '' 且 < now-30d)

    2026-11-02 S0: 不可逆删除, 加当日一次性抢占; 幂等(DELETE 按条件), 失败可安全回滚重试"""
    if not await _authed(request):
        return fail("未授权", 401)
    _off = _off_window("recycle", force_ok=_force_ok(request))
    if _off:
        return ok(_off)
    if not _claim("cron:recycle"):
        return ok({"skipped": True, "reason": "今天已清理"})
    # 2026-11-02 S2-2: 永久删会改变物化, 受影响日期必须在删之前取出来
    try:
        _gone = query("SELECT DISTINCT channel, DATE(ordered_at) AS d FROM orders "
                      "WHERE deleted_at IS NOT NULL AND deleted_at != '' "
                      "AND deleted_at < DATE_SUB(NOW(), INTERVAL 30 DAY)") or []
    except Exception as _eg:
        try_err('cron', '回收站取受影响日期降级', _eg)
        _gone = []
    try:
        n1 = execute("DELETE FROM orders WHERE deleted_at IS NOT NULL AND deleted_at != '' "
                     "AND deleted_at < DATE_SUB(NOW(), INTERVAL 30 DAY)")
        n2 = execute("DELETE FROM rules WHERE is_active=0 AND deleted_at IS NOT NULL AND deleted_at != '' "
                     "AND deleted_at < DATE_SUB(NOW(), INTERVAL 30 DAY)")
    except Exception:
        _unclaim("cron:recycle")
        raise
    total = int(n1 or 0) + int(n2 or 0)
    if total > 0:
        _log("info", "回收站清理: 永久删除 %d 条(订单 %s + 规则 %s)" % (total, n1, n2))
        from routes.common import mark_agg_days as _mkd
        _by = {}
        for r in _gone:
            _ch, _d = str(r.get("channel") or ""), str(r.get("d") or "")[:10]
            if _ch and _d:
                _by.setdefault(_ch, set()).add(_d)
        for _ch, _ds in _by.items():
            try:
                _mkd(_ch, _ds, reason="order_recycle")
            except Exception as _em:
                try_err('cron', '回收站删单后登记重建降级', _em)
    return ok({"deleted": total})


@router.api_route("/cron/push-alerts", methods=["GET","POST"])
@traced
@_pipe("push_alerts", rows_key="pushed")
async def cron_push_alerts(request: Request):
    """推送最近 60 分钟新增未推送告警到 webhook(钉钉/企微格式)

    2026-11-02 S0: 本端点会向真实 webhook 发消息(可被用于骚扰/刷屏), 加 5 分钟限频。
    不设当日抢占 —— pushed 标记本身幂等, 推送失败需要重试。"""
    if not await _authed(request):
        return fail("未授权", 401)
    _off = _off_window("push_alerts", force_ok=_force_ok(request))
    if _off:
        return ok(_off)
    if not _throttle("push-alerts", 300):
        return ok({"skipped": True, "reason": "5 分钟内已推送"})
    try:
        cfg = one("SELECT value FROM replenishment_config WHERE `key`='webhook_url'") or {}
        url = (cfg.get("value") or "").strip()
        if not url:
            return ok({"pushed": 0, "skipped": "no webhook"})
        rows = query(
            "SELECT id, alert_type, title, description, severity, channel, related_sku FROM alerts "
            "WHERE status='active' AND (pushed IS NULL OR pushed=0) "
            "AND created_at >= DATE_SUB(NOW(), INTERVAL 60 MINUTE) LIMIT 50")
        if not rows:
            return ok({"pushed": 0})
        import requests
        lines = ["【SupplyKit 告警】"]
        for r in rows:
            # 2026-11-02 S1-5: 渠道中文名走配置(原 "京东"/"其他" 二元判断, 新增渠道恒显示"其他")
            _ch = str(_CH_CONF(r.get("channel") or "jd").get("name") or r.get("channel") or "jd")
            lines.append("%s: %s%s (%s%s)" % (r.get("severity", "info"), r.get("title", ""),
                                              (" - " + str(r.get("description"))) if r.get("description") else "",
                                              _ch, (" / " + str(r.get("related_sku"))) if r.get("related_sku") else ""))
        resp = requests.post(url, json={"msgtype": "text", "text": {"content": "\n".join(lines)}}, timeout=15)
        if resp.status_code in (200, 201, 204):
            ids = [r.get("id") for r in rows]
            execute("UPDATE alerts SET pushed=1 WHERE id IN (%s)" % ",".join(["%s"] * len(ids)), ids)
            return ok({"pushed": len(ids)})
        return ok({"pushed": 0, "http": resp.status_code})
    except Exception as e:
        _log("error", "告警推送失败: %s" % str(e)[:200])
        return ok({"pushed": 0, "error": str(e)[:150]})


def _reconcile_gmv(channel, start, end):
    """GMV 物化对账(2026-11-02 S2-3): orders_day_agg vs orders 源表

    口径必须与 dashboard_maintain._rebuild_day_agg **逐字一致**, 否则对账自己就成了噪音源:
      gmv = total_amount - discount_amount + freight_amount + tax_amount, 仅已支付状态
      cnt = COUNT(*) 全部订单(不过滤状态 —— 物化表的 cnt 就是这么算的)
    物化表只有 90 天窗口 → 下界取 max(请求起点, agg 最早日期), 无数据则返回 None(不参与对账)。
    返回 {"agg_gmv","src_gmv","agg_cnt","src_cnt","gmv_diff","cnt_diff"}
    """
    from routes.common import status_cond as _sc
    _mn = one("SELECT COALESCE(MIN(`date`),'') AS mn FROM orders_day_agg WHERE channel=%s",
              [channel]) or {}
    _mnv = str(_mn.get("mn") or "")[:10]
    if not _mnv:
        return None
    _s = max(start, _mnv)
    _cond = _sc()
    _sql = ("SELECT "
            "(SELECT IFNULL(SUM(gmv),0) FROM orders_day_agg WHERE channel=%s AND `date`>=%s AND `date`<=%s) AS agg_gmv,"
            "(SELECT IFNULL(SUM(cnt),0) FROM orders_day_agg WHERE channel=%s AND `date`>=%s AND `date`<=%s) AS agg_cnt,"
            "(SELECT IFNULL(SUM(IF(" + _cond + ", total_amount - COALESCE(discount_amount,0) "
            "+ COALESCE(freight_amount,0) + COALESCE(tax_amount,0), 0)),0) FROM orders "
            "WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='') "
            "AND ordered_at>=%s AND ordered_at<DATE_ADD(%s, INTERVAL 1 DAY)) AS src_gmv,"
            "(SELECT COUNT(*) FROM orders WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='') "
            "AND ordered_at>=%s AND ordered_at<DATE_ADD(%s, INTERVAL 1 DAY)) AS src_cnt")
    r = one(_sql, [channel, _s, end] * 2 + [channel, _s, end] * 2) or {}
    _ag, _sg = round(float(r.get("agg_gmv") or 0), 2), round(float(r.get("src_gmv") or 0), 2)
    _ac, _scnt = int(r.get("agg_cnt") or 0), int(r.get("src_cnt") or 0)
    return {"from": _s, "agg_gmv": _ag, "src_gmv": _sg, "gmv_diff": round(_ag - _sg, 2),
            "agg_cnt": _ac, "src_cnt": _scnt, "cnt_diff": _ac - _scnt}


def run_reconcile(days=90):
    """对账核心(同步, 供 cron 端点与应用层兜底共用) —— 2026-11-03 扩为四段

    ① 日销: [今-days, 昨] 内 SUM(快照 order_count) == SUM(订单 quantity)
       · 排除当天: 当天订单仍在增长, 快照可能滞后(差异属正常)
       · 口径: 销量池 SALES_SQL(=已完成), 与快照写入同源
    ② GMV : orders_day_agg 的 gmv/cnt == 订单源表同口径重算(见 _reconcile_gmv)
       · 原因: 原对账只比"行数/件数", 金额错了根本发现不了; 而看板 GMV 全靠这张物化表
       · 窗口比日销段晚一天收口(到 today-2): 软删残留靠 _rebuild_agg_day 单日精准自愈,
         最近两天可能还没自愈, 报出来是噪音而非数据错

    ③ 告警四维一致性: active 低库存/补货告警的 (渠道, SKU, 69码, 仓) 必须能在 inventory
       找到对应的"格"。找不到 = 孤儿告警, 说明某条关闭链路漏了(规则停用/删单/换码都会造成)。
    ④ 快照抽样到 SKU×69码(而非只比总量): 总量相等但个别 SKU 错位时 ① 段发现不了。
       取近 7 天量最大的 50 个维度比对 —— 高频 SKU 错一位就影响补货, 低频的影响小。

    第三段(SKU 统计 sku_sales_stats)仍不在本函数内: 它是从快照派生的二阶物化, 源头已被①
    覆盖, 全量重算 90 天的成本远高于它能发现的问题 —— 要查请看
    /api/health?detail=1 的 mat_rebuild 耗时与行数。

    差异 ≠ 0 → error 日志(source=cron), 前端质量页可见。
    """
    try:
        _d = max(1, min(int(days or 90), 365))
    except Exception:
        _d = 90
    _now = now_cn()
    _start = (_now - timedelta(days=_d)).strftime("%Y-%m-%d")
    _end = (_now - timedelta(days=1)).strftime("%Y-%m-%d")
    # GMV 段晚一天收口: 软删残留靠 _rebuild_agg_day 单日精准自愈, 最近两天可能尚未重建,
    # 比进去只会产生"看起来是数据错、其实是还没自愈"的噪音。
    _end_gmv = (_now - timedelta(days=2)).strftime("%Y-%m-%d")
    out = {}
    _extra = {}   # 2026-11-03 D8: 告警四维孤儿 / 快照抽样(与渠道维度平级, 不能混进 out)
    _bad = 0
    for _ch in _CHANNELS():
        try:
            _s = one("SELECT COALESCE(SUM(order_count),0) AS q FROM daily_sales_snapshot "
                     "WHERE channel=%s AND `date`>=%s AND `date`<=%s", [_ch, _start, _end]) or {}
            _o = one("SELECT COALESCE(SUM(quantity),0) AS q FROM orders "
                     "WHERE channel=%s AND order_status IN (" + _PAID_SQL + ") "
                     "AND (deleted_at IS NULL OR deleted_at='') "
                     "AND ordered_at >= %s AND ordered_at < DATE_ADD(%s, INTERVAL 1 DAY)",
                     [_ch, _start, _end]) or {}
            _sq = int(_s.get("q") or 0)
            _oq = int(_o.get("q") or 0)
            out[_ch] = {"snapshot": _sq, "orders": _oq, "diff": _sq - _oq}
            if _sq != _oq:
                _bad += 1
                _log("error", "日销对账不一致[%s] %s~%s: 快照=%d 订单=%d 差=%d"
                     % (_ch, _start, _end, _sq, _oq, _sq - _oq))
        except Exception as _e:
            out[_ch] = {"error": str(_e)[:150]}
            try_err('cron', '日销对账降级(%s)' % _ch, _e)
        # ② GMV 段(独立 try: 物化表缺失/未建时不能让日销段一起失败)
        try:
            _g = _reconcile_gmv(_ch, _start, _end_gmv)
            if _g:
                out[_ch]["gmv"] = _g
                # 金额容差 0.01(浮点/分位舍入); 件数必须严格相等
                if abs(_g["gmv_diff"]) > 0.01 or _g["cnt_diff"] != 0:
                    _bad += 1
                    _log("error", "GMV 对账不一致[%s] %s~%s: 物化 gmv=%.2f cnt=%d / 源 gmv=%.2f cnt=%d "
                         "(差 %.2f / %d 单)" % (_ch, _g["from"], _end_gmv, _g["agg_gmv"], _g["agg_cnt"],
                                               _g["src_gmv"], _g["src_cnt"], _g["gmv_diff"], _g["cnt_diff"]))
        except Exception as _e:
            out[_ch]["gmv_error"] = str(_e)[:150]
            try_err('cron', 'GMV 对账降级(%s)' % _ch, _e)
    # ③ 告警 ↔ 库存 四维一致性(2026-11-03 D8): active 低库存/补货告警的
    #    (channel, sku, 69码, 仓) 必须能在 inventory 找到"格"。找不到 = 孤儿告警,
    #    说明关闭链路漏了一条(规则停用/删单/换码都会造成)。用 LEFT JOIN 一次扫完, 零 N+1。
    try:
        _orph = query(
            "SELECT a.alert_type, a.channel, a.related_sku, COALESCE(a.barcode,'') AS bc, a.warehouse "
            "FROM alerts a LEFT JOIN inventory i ON i.channel=a.channel AND i.sku=a.related_sku "
            "  AND COALESCE(i.barcode,'')=COALESCE(a.barcode,'') AND i.warehouse=a.warehouse "
            "WHERE a.status='active' AND a.source='rules_engine' "
            "  AND a.alert_type IN ('low_stock','replenish') AND i.id IS NULL LIMIT 200") or []
        _extra["orphan_alerts"] = len(_orph)
        if _orph:
            _bad += 1
            _s = _orph[0]
            _log("error", "告警四维孤儿 %d 条(库存已无该 格): 例 type=%s ch=%s sku=%s 码=%s 仓=%s"
                 % (len(_orph), _s.get("alert_type"), _s.get("channel"), _s.get("related_sku"),
                    _s.get("bc") or "-", _s.get("warehouse")))
    except Exception as _e:
        _extra["orphan_alerts"] = "error: %s" % str(_e)[:120]
        try_err('cron', '告警四维对账降级', _e)
    # ④ 快照 ↔ 订单 抽样到 SKU×69码(而不是总量): 总量相等但个别 SKU 错位, ①段发现不了。
    #    取近 7 天量最大的 50 个维度比对 —— 高频 SKU 错一位就影响补货, 低频的错位影响小。
    try:
        _d1 = (now_cn() - timedelta(days=7)).strftime("%Y-%m-%d")
        _sample = query(
            "SELECT s.channel, s.sku, s.bc, s.q1, COALESCE(o.q2, 0) AS q2 FROM ("
            "  SELECT channel, sku, COALESCE(barcode,'') AS bc, SUM(order_count) AS q1 "
            "  FROM daily_sales_snapshot WHERE `date`>=%s AND `date`<=%s "
            "  GROUP BY channel, sku, barcode ORDER BY q1 DESC LIMIT 50) s "
            "LEFT JOIN ("
            "  SELECT channel, sku, COALESCE(barcode,'') AS bc, SUM(quantity) AS q2 "
            "  FROM orders WHERE order_status IN (" + _PAID_SQL + ") "
            "    AND (deleted_at IS NULL OR deleted_at='') AND ordered_at>=%s "
            "  GROUP BY channel, sku, barcode) o "
            "  ON o.channel=s.channel AND o.sku=s.sku AND o.bc=s.bc", [_d1, _end, _d1]) or []
        _diff = [r for r in _sample if int(r.get("q1") or 0) != int(r.get("q2") or 0)]
        _extra["sku_sample"] = {"n": len(_sample), "diff": len(_diff)}
        if _diff:
            _bad += 1
            _log("error", "快照抽样对账不一致 %d/%d: 例 ch=%s sku=%s 码=%s 快照=%s 订单=%s"
                 % (len(_diff), len(_sample), _diff[0].get("channel"), _diff[0].get("sku"),
                    _diff[0].get("bc") or "-", _diff[0].get("q1"), _diff[0].get("q2")))
    except Exception as _e:
        _extra["sku_sample"] = "error: %s" % str(_e)[:120]
        try_err('cron', '快照抽样对账降级', _e)
    if not _bad:
        _log("info", "日销对账一致 %s~%s: %s" % (
            _start, _end,
            " / ".join("%s=%d" % (k, v.get("snapshot", 0)) for k, v in out.items() if "snapshot" in v)))
    return {"range": [_start, _end], "channels": out, "mismatch": _bad, **_extra}


def run_agg_sweep(hours=48):
    """兜底扫描核心(同步, 供 cron 端点与每日维护共用)

    按 orders.synced_at 反查「最近被改过的业务日期」并重建 —— 防的是"某个写入口忘了
    调 mark_agg_days"。这种漏法不会报错, 只会让物化长期少算; synced_at 由数据库
    自动维护, 应用代码可以漏, 它漏不了。
    """
    try:
        _h = max(1, min(int(hours or 48), 24 * 30))
        # INTERVAL 后不能走占位符(TiDB 语法错) —— _h 已 clamp 为整数, 内联安全
        rows = query("SELECT channel, DATE(ordered_at) AS d, COUNT(*) AS c FROM orders "
                     "WHERE synced_at >= DATE_SUB(NOW(), INTERVAL %d HOUR) "
                     "GROUP BY channel, DATE(ordered_at)" % _h) or []
    except Exception as _e:
        try_err('cron', 'agg-sweep 扫描降级', _e)
        return {"marked": 0, "error": "扫描失败"}
    from routes.common import mark_agg_days as _mkd
    _by, _rows = {}, 0
    for r in rows:
        _ch, _d = str(r.get("channel") or ""), str(r.get("d") or "")[:10]
        if _ch and _d:
            _by.setdefault(_ch, set()).add(_d)
            _rows += int(r.get("c") or 0)
    _n = 0
    for _ch, _ds in _by.items():
        _n += _mkd(_ch, _ds, reason="sweep")
    # 同步消费一轮(最多 40 天/轮) —— 让调用方返回时数据已追平, 便于直接验证
    from routes.analysis_cache import _drain_agg_dirty
    _res = _drain_agg_dirty()
    if _n:
        _log("info", "agg-sweep: %d 小时内 %d 行变更 → 标记 %d 个业务日期, 重建 %d 天"
             % (_h, _rows, _n, _res.get("days", 0)))
    return {"hours": _h, "rows": _rows, "marked": _n,
            "rebuilt": _res.get("days", 0),
            # pending 用 left(剩余) 而不是入参: 入参永远等于"这轮的全部", 看着像没清干净
            "pending": _res.get("left", _res.get("pending", 0)),
            "mode": _res.get("mode")}


@router.api_route("/cron/agg-sweep", methods=["GET", "POST"])
@traced
@_pipe("agg_sweep", rows_key="marked")
async def cron_agg_sweep(request: Request, hours: int = 48):
    """兜底扫描(2026-11-02 S2-2): 按 orders.synced_at 反查「最近被改过的业务日期」并重建

    为什么需要它: 增量重建的正确性依赖「每个写入口都调了 mark_agg_days」。漏一个入口,
    那条路径的数据就永远进不了物化, 而且**不会报错** —— 只有用户发现数字不对才会知道。
    synced_at 由数据库自动维护(ON UPDATE), 不经过应用代码: 应用代码可以漏, 它漏不了。

    为什么按「改过的日期」而不是按「导入时间窗口」: 补录的 ordered_at 可以是任意历史日期,
    与什么时候写进来无关。这里拿 synced_at 当**触发器**, 拿 ordered_at 当**重建范围**。

    每天一次、只扫最近 N 小时, 成本 = 重建那几天(单日几十毫秒), 与全量 90 天差两个数量级。
    """
    if not await _authed(request):
        return fail("未授权", 401)
    return ok(run_agg_sweep(hours))


@router.api_route("/cron/reconcile", methods=["GET", "POST"])
@traced
@_pipe("reconcile")
async def cron_reconcile(request: Request, days: int = 90):
    """日销对账(cron 入口)——体量与口径见 run_reconcile

    2026-11-02 S0: 加 5 分钟限频(只读, 重复执行无害但耗 RU); days 已在 run_reconcile 内 clamp 到 [1,365]"""
    if not await _authed(request):
        return fail("未授权", 401)
    _off = _off_window("reconcile", force_ok=_force_ok(request))
    if _off:
        return ok(_off)
    if not _throttle("reconcile", 300):
        return ok({"skipped": True, "reason": "5 分钟内已对账"})
    return ok(run_reconcile(days))


# ── 账期自动结账(2026-11-04 用户定案: 自动, 但数据不齐拒绝结) ──────────────
def _own_channels(channels=None):
    """只保留**真有自有仓库存**的渠道

    账期只管自有仓(平台仓/B 仓以后台导出为准)。对没有 own 数据的渠道结账,
    体检必然报"一整月都没有数据" → 白写一条 blocked, 还会让人误判结账坏了。
    查不到渠道清单(表异常)时退回全量, 不静默把结账停掉。
    """
    try:
        rows = query("SELECT DISTINCT channel FROM inventory WHERE warehouse_type='own' "
                     "AND (deleted_at IS NULL OR deleted_at='')") or []
        have = {str(r.get("channel") or "") for r in rows if r.get("channel")}
    except Exception:
        have = set()
    pool = [c for c in (channels or _CHANNELS()) if c]
    return [c for c in pool if c in have] if have else pool


def run_ledger_close(channels=None):
    """把上一个账期结掉(仅自有仓); 1 号主结, 2、3 号自动重试。

    触发方式: 由 `cleanup-logs`(每日 03:05 CST)带入 —— schedules 上限 10 条已占满,
      不单独挂调度。手动/CI 可直接打 `/cron/ledger-close`(窗口 00:30-03:00)。
    非 1~3 号 → 直接 skipped(纯 Python 判断, 一条 SQL 都不发, 零 RU)。
    体检不过(上期流水没导全) → 该渠道拒绝结账并留痕, **不改任何数** ——
    结一个缺流水的假账会把错误结转进下月期初, 比不结账危险得多。
    补导之后走 POST /ledger/reclose 补结, 不必等下次 1 号。
    """
    # 1~3 号都允许重试: 平台调度偶发失败/数据 1 号没导全时, 2、3 号还能自动补上,
    #   不用等人来点; 4 号起停止(再往后就该走 reclose 明确补结, 不能靠自动兜)
    _d = now_cn().day
    if _d > 3:
        return {"skipped": True, "reason": "非结账窗口(每月 1~3 号结上月)", "day": _d}
    from core.ledger import close_period, shift_period, period_of, state_get
    target = shift_period(period_of(), -1)
    if not target:
        return {"skipped": True, "reason": "账期计算异常"}
    out = []
    for ch in _own_channels(channels):
        _skus = []
        # 已结过就不再重复结(幂等, 省一轮重算)
        if state_get(ch, "own").get("period") == target and \
                state_get(ch, "own").get("status") == "closed":
            out.append({"ok": True, "channel": ch, "period": target, "skipped": "该账期已结"})
            continue
        # 结账前后各重算一次, 顺序不能反:
        #   ① 结账**前**: 让 batches 反映"此刻真实余额"(全期窗口), 否则切片出来的
        #      batch_opening 是旧值 —— 一旦切错, 下月指定批次出库全部落空;
        #   ② 结账: 写账期 + 冻结批次月初余额 + 把期末写回成下月期初;
        #   ③ 结账**后**: 用新窗口(本月 1 号起)重建, 池 = 月初余额 + 本月流水, 可用同步刷新。
        # 缺了 ③, 页面上的可用还停在"结账前"的口径, 要等下一次导入才对。
        try:
            from routes.cleanse_write import (_recalc_own_inventory as _rc,
                                              _rebuild_own_batches as _rb)
            _skus = [str(r.get("sku") or "") for r in
                     (query("SELECT DISTINCT sku FROM inventory WHERE channel=%s "
                            "AND warehouse_type='own' AND (deleted_at IS NULL OR deleted_at='')",
                            [ch]) or [])]
            _skus = [s for s in _skus if s]
            if _skus:
                _rc(ch, _skus)
                _rb(ch, _skus)
        except Exception as _e:
            try_err('ledger', '结账前重算降级', _e)
        try:
            r = close_period(target, ch, "own")
        except Exception as _e:
            try_err('ledger', '自动结账异常', _e)
            r = {"ok": False, "period": target, "reason": str(_e)}
        if r.get("ok") and _skus:
            # 结账后只刷库存, **不重建批次**: 下期批次池已由 batch_opening 切片固化,
            #   此刻重建一次要多花 10~20s(2000 SKU 全删重建), 而下一次流水导入必然会重建。
            #   库存必须刷 —— 期初已被改写成期末, 不刷会留下"期初/可用"两套数。
            try:
                _rc(ch, _skus)
            except Exception as _e:
                try_err('ledger', '结账后重算降级', _e)
        r["channel"] = ch
        out.append(r)
    # 2026-11-06 P2-7: 自动结账同样要失效(cron 是凌晨跑, 影响小但口径必须一致:
    #   结账改了期初, 缓存里的进销存/周转还是结账前的数)
    if any(x.get("ok") for x in out):
        try:
            from routes.analysis_cache import invalidate as _invl
            _invl("inventory")
        except Exception as _e:
            try_err('ledger', '自动结账后缓存失效降级', _e)
    return {"period": target,
            "ok_count": len([x for x in out if x.get("ok")]),
            "blocked": [x for x in out if not x.get("ok")],
            "items": out}


@router.api_route("/cron/ledger-close", methods=["GET", "POST"])
@traced
@_pipe("ledger_close")
async def cron_ledger_close(request: Request):
    """账期自动结账入口(平台每月 1 号 00:40 CST 调度)"""
    if not await _authed(request):
        return fail("未授权", 401)
    _off = _off_window("ledger_close", force_ok=_force_ok(request))
    if _off:
        return ok(_off)
    return ok(run_ledger_close())

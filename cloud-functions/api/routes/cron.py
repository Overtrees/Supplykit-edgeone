"""原生定时任务路由(方案 B): EdgeOne schedules 调度的外部化任务

PA 版 APScheduler 10 任务中, TiDB 场景需要外部化的核心任务(备份/磁盘/WAL 已在 PA 版自动跳过):
- snapshot:          每天构建日销快照(增量补全最近 90 天)
- freshness:         每小时检查快照新鲜度(>2 天陈旧自动重建)
- archive:           每天归档 90 天前订单 → daily_stats(写入失败保护不删)
- cleanup-logs:      每天清理 quality_logs 保留最近 500
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
from routes.common import ok, fail, traced, try_err
from routes.common import pipeline_run as _pipe  # 2026-11-02 S1-2: 运行流水
from routes.common import ch_codes as _CHANNELS  # 2026-11-02 S1-5: 渠道数据驱动(原各处硬编码 ("jd","other"))
from routes.common import ch_conf as _CH_CONF
from routes.common import SALES_SQL as _PAID_SQL  # 2026-10-29 销量池单一真源(原硬编码 已完成)

router = APIRouter(tags=["cron"])
@router.get("/cron/continue-tasks")
@traced
def continue_tasks():
    """2026-10-14: 续跑所有 running 的 cleansing/seed/export 任务(后端跟进——app 后台/页面不驻留任务也推进)
    由平台调度(每 5 分钟)或手动调用; 每任务触发一次对应 status 续跑(窗口推进)

    2026-11-02 S0: 本端点此前**既无鉴权也无护栏**(唯一一个裸的) —— 匿名 GET 即可反复触发。
    加进程内 30s 限频(正常调用方是中间件 60s 一次 + 平台每天一次, 不会被影响)。"""
    if not _throttle("continue-tasks", 30):
        return ok({"skipped": True, "reason": "30 秒内已推进过"})
    from db import query as _qc, execute as _ec, one as _oc
    from datetime import datetime, timedelta as _td2, timezone as _tz2
    done = {"cleansing": 0, "cleansing_eval": 0, "seed": 0, "export": 0, "timeout": 0, "skipped": 0}
    try:
        rows = _qc("SELECT task_id, task_type, updated_at FROM sync_tasks WHERE status='running' LIMIT 20")
        _cutoff = (datetime.now(_tz2.utc) - _td2(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        try_err('cron', 'continue-tasks 诊断: rows=%d cutoff=%s' % (len(rows or []), _cutoff))
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
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        return bool(execute("INSERT IGNORE INTO maintenance_log(`date`, task) VALUES(%s,%s)",
                            [today, task]))
    except Exception as _e:
        try_err('cron', '抢占标记失败(放行执行)', _e)
        return True


def _unclaim(task):
    """执行失败回滚抢占标记 —— 不回滚=当天永久跳过(dashboard._daily_maintenance 就是栽在这)"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        execute("DELETE FROM maintenance_log WHERE `date`=%s AND task=%s", [today, task])
    except Exception as _e:
        try_err('cron', '回滚抢占标记失败', _e)


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
    start = (datetime.now(timezone.utc) - timedelta(days=rebuild_days)).strftime("%Y-%m-%d")
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
    r = one("SELECT COALESCE(MAX(date),'') AS m FROM daily_sales_snapshot") or {}
    m = str(r.get("m") or "")
    stale_limit = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
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
    if not _throttle("archive", 600):
        return ok({"skipped": True, "reason": "10 分钟内已执行"})
    if not _claim("cron:archive"):
        return ok({"skipped": True, "reason": "今天已归档"})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")
    old = query(
        "SELECT id, ordered_at, channel, store, sku, order_status, total_amount, quantity "
        "FROM orders WHERE DATE(ordered_at) < %s AND (deleted_at IS NULL OR deleted_at='')",
        [cutoff])
    if not old:
        return ok({"archived": 0, "stats_rows": 0})
    ok_n = 0
    for o in old:
        try:
            execute(
                "INSERT INTO daily_stats(date, channel, store, sku, order_status, gmv, order_count, quantity) "
                "VALUES(%s,%s,%s,%s,%s,%s,1,%s) "
                "ON DUPLICATE KEY UPDATE gmv=gmv+VALUES(gmv), order_count=order_count+1, "
                "quantity=quantity+VALUES(quantity)",
                (str(o.get("ordered_at") or "")[:10], o.get("channel", "jd"), o.get("store", ""),
                 o.get("sku", ""), str(o.get("order_status") or "")[:10],
                 float(o.get("total_amount") or 0), int(o.get("quantity") or 0)))
            ok_n += 1
        except Exception as _e:
                try_err('cron', '静默降级', _e)
    if ok_n != len(old):
        _log("error", "归档中止: daily_stats 写入 %d/%d, 不删除 orders(防数据丢失)" % (ok_n, len(old)))
        return ok({"archived": 0, "stats_rows": ok_n, "aborted": True})
    ids = [o.get("id") for o in old]
    for i in range(0, len(ids), 200):
        batch = ids[i:i + 200]
        execute("DELETE FROM orders WHERE id IN (%s)" % ",".join(["%s"] * len(batch)), batch)
    _log("info", "归档: %d 订单 → daily_stats" % len(old))
    return ok({"archived": len(old), "stats_rows": ok_n})


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
    if not _claim("cron:cleanup_logs"):
        return ok({"skipped": True, "reason": "今天已清理"})
    from routes.common import prune_quality_logs
    r = prune_quality_logs()
    _log("info", "日志清理: 过期删除 %d 条 / 上限删除 %d 条 / 现存 %d 条"
         % (r.get("old", 0), r.get("cap", 0), r.get("total", 0)))
    return ok(r)


def run_daily_rules() -> dict:
    """每日规则全量评估(同步, 供 cron 路由与应用层兜底共用):
    孤儿告警清理 + 旧 SKU 级告警清理 + 双渠道全量规则评估 + 健康分快照 + risk_summary 审计日志"""
    # 1. 孤儿告警清理: active 且 source in (rules_engine,event_bus) 的 alert_type 已无 active 规则 → inactive
    cleaned = 0
    for r in query("SELECT DISTINCT alert_type, channel FROM alerts "
                   "WHERE status='active' AND source IN ('rules_engine','event_bus')"):
        at = r.get("alert_type")
        ch = r.get("channel") or "jd"
        has = one("SELECT COUNT(*) AS c FROM rules WHERE alert_type=%s AND channel=%s AND is_active=1 "
                  "AND (deleted_at IS NULL OR deleted_at='')", [at, ch]) or {}
        if not int(has.get("c") or 0):
            execute("UPDATE alerts SET status='inactive' WHERE alert_type=%s AND channel=%s "
                    "AND status='active' AND source IN ('rules_engine','event_bus')", [at, ch])
            cleaned += 1
    # 1.5 旧 SKU 级规则告警(warehouse 为空)清理 —— 告警已逐仓化(SKU×仓), 旧的 SKU 聚合
    #     告警由下方全量评估重新生成逐仓版, 先关闭避免新旧混杂致看板计数翻倍
    try:
        _old = execute("UPDATE alerts SET status='inactive' WHERE status='active' "
                       "AND source IN ('rules_engine','event_bus') "
                       "AND (warehouse IS NULL OR warehouse='')")
        cleaned += int(_old or 0)
    except Exception as _e:
            try_err('cron', '静默降级', _e)
    # 1.6 无需求告警兜底(2026-10-15): 无未删订单 且 无在档(products/inventory 均无) 的 active rules_engine 告警 → inactive
    #     ——SKU 只有订单页有(订单删了=无需求)——告警孤儿(删单联动同口径; 有在档保留——真缺货/低库存不误伤; 解存量 TypeError 期孤儿)
    try:
        _c2 = execute(
            "UPDATE alerts a SET a.status='inactive' WHERE a.status='active' AND a.source='rules_engine' "
            "AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.sku=a.related_sku AND o.channel=a.channel "
            "AND (o.deleted_at IS NULL OR o.deleted_at='')) "
            "AND NOT EXISTS (SELECT 1 FROM products p WHERE p.sku=a.related_sku AND p.channel=a.channel "
            "AND (p.deleted_at IS NULL OR p.deleted_at='')) "
            "AND NOT EXISTS (SELECT 1 FROM inventory i WHERE i.sku=a.related_sku AND i.channel=a.channel)")
        cleaned += int(_c2 or 0)
    except Exception as _e:
            try_err('cron', '静默降级', _e)
    # 2. 全量规则评估(替代简化滞销逻辑): inventory.changed + scheduled.daily 遍历库存行
    #    双渠道逐仓(其他渠道此前完全无每日规则评估 —— 完整性修复); 全量行(含 SKU×仓)
    from core.rules import evaluate_stock_skus
    triggered = []
    from routes.common import track_run as _tr
    for _ch in _CHANNELS():
        # 2026-11-02 S1-2: 逐渠道留一条运行记录(scope=渠道) —— 出问题时一眼看出哪个渠道没评估到
        with _tr("rule_eval", _ch) as _rec:
            _hits = evaluate_stock_skus(_ch, limit=100000)
            _rec.rows = len(_hits or [])
            _rec.detail = {"triggered": len(_hits or [])}
        triggered.extend(_hits or [])
    # 2026-10-29 批次对账(四维逐一, 用户定案): 
    #   对每个 (仓库类型, SKU, 69码, 仓库) 校验 Σ(batches.qty) ≡ inventory.available_qty
    #   **不能按 SKU 汇总核对** —— 不同仓/不同码的偏差会互相抵消(A 多 100 与 B 少 100 汇总后为 0),
    #   掩盖真实错配。四维逐一比对才能定位到具体仓/具体码。
    #   偏差只告警不自动修(自动修会掩盖导入遗漏/仓名错/批次未填等根因)
    try:
        from routes.cleanse_write import SELF_WAREHOUSE_TYPES as _SELF
        _ph = ",".join(["%s"] * len(_SELF))
        for _ch in _CHANNELS():
            _bad = query(
                "SELECT i.sku, COALESCE(i.barcode,'') AS bc, i.warehouse, "
                "  i.available_qty AS qty, "
                "  COALESCE((SELECT SUM(b.qty) FROM batches b WHERE b.channel=i.channel "
                "    AND b.sku=i.sku AND b.warehouse=i.warehouse "
                "    AND b.warehouse_type=i.warehouse_type "
                # 69码匹配: 必须严格相等(2026-11-01 收紧)
                #   原为 "任一方为空 → 降级匹配", 属双向通配: 库存行有码 + 批次无码时,
                #   会匹配上该仓**所有码**的批次, 偏差被别的码抵消 → 与本节"四维逐一、不能
                #   抵消"的初衷自相矛盾, 等于给错配开了后门。空码只能匹配空码。
                "    AND COALESCE(b.barcode,'')=COALESCE(i.barcode,''))), 0) AS bq "
                "FROM inventory i WHERE i.channel=%%s AND i.warehouse_type IN (%s) "
                "HAVING ABS(qty - bq) > 1" % _ph,
                [_ch] + list(_SELF))
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
    _log("info", "每日规则: 孤儿告警清理 %d, 规则触发 %d 个(%s)" % (
        cleaned, len(triggered), ",".join(str(t)[:20] for t in triggered[:8])))
    # P2 回溯校验 Lite: 濒临断货判定摘要写入 quality_logs(审计/漏报误报分析基础)
    try:
        from routes.dashboard import _stock_risk, _health_index
        for _ch in _CHANNELS():
            _rk = _stock_risk(_ch)
            _hi = _health_index(_ch)
            _t = (_rk.get("bcTotal", 0) or 0) + (_rk.get("cTotal", 0) or 0) + (_rk.get("ownTotal", 0) or 0)
            _r = (_rk.get("bcCritical", 0) or 0) + (_rk.get("cCritical", 0) or 0) + (_rk.get("ownCritical", 0) or 0)
            _o = (_rk.get("bcWarning", 0) or 0) + (_rk.get("cWarning", 0) or 0) + (_rk.get("ownWarning", 0) or 0)
            _hs = str(_hi.get("score") if _hi else "-")
            _ho = str((_hi or {}).get("own", {}).get("score") if _hi else "-")
            _hp = str((_hi or {}).get("platform", {}).get("score") if _hi else "-")
            _hb = str((_hi or {}).get("bc", {}).get("score") if _hi else "-")
            try:
                from routes.dashboard import _health_index as _hix
                _hs2 = _hix(_ch)
                _sc2 = _hs2.get("score")
                from db import execute as _exH
                from datetime import datetime as _dth, timezone as _tzh
                _d0 = _dth.now(_tzh.utc).strftime("%Y-%m-%d")
                _exH("INSERT INTO health_snapshot(date, channel, score, own_score, platform_score, bc_score) "
                     "VALUES(%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE score=VALUES(score), own_score=VALUES(own_score), "
                     "platform_score=VALUES(platform_score), bc_score=VALUES(bc_score)",
                     [_d0, _ch, int(_sc2) if _sc2 is not None else -1,
                      int((_hs2.get("own") or {}).get("score") or -1),
                      int((_hs2.get("platform") or {}).get("score") or -1),
                      int((_hs2.get("bc") or {}).get("score") or -1)])
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
    return {"orphan_cleaned": cleaned, "rules_triggered": triggered}


@router.api_route("/cron/daily-rules", methods=["GET","POST"])
@traced
@_pipe("daily_rules", rows_key="orphan_cleaned")
async def cron_daily_rules(request: Request, force: int = 0):
    """每日规则(EdgeOne schedules 触发路径): 校验后调用共享 run_daily_rules()
    maintenance_log(date+'daily_rules') 抢占 —— 与应用层兜底互斥, 一天只评估一次(先到者执行);
    force=1: 运维强制重跑(重置后/数据重建场景, 绕过当天抢占)

    2026-11-02 S0 两处修正:
      ① force=1 此前匿名可传 —— 等于给了一个"每调一次就跑 10 万行全量评估"的免费放大器;
         现在必须带正确 CRON_SECRET 才认, 否则降级为普通调用。
      ② 执行失败回滚抢占标记 —— 原实现失败后当天不再重试(dashboard._daily_maintenance 同款问题)。"""
    if not await _authed(request):
        return fail("未授权", 401)
    if force:
        _sec = os.environ.get("CRON_SECRET", "")
        _h = request.headers.get("X-Cron-Secret", "")
        if not (_sec and _h):
            force = 0
        else:
            import hmac as _hm2
            if not _hm2.compare_digest(_h.encode(), _sec.encode()):
                force = 0
    if not force:
        if not _claim("daily_rules"):
            return ok({"skipped": True, "reason": "今天已评估(应用层兜底或另一实例)"})
        try:
            _r = run_daily_rules()
        except Exception:
            _unclaim("daily_rules")
            raise
        return ok(_r)
    return ok(run_daily_rules())


@router.api_route("/cron/ping", methods=["GET","POST"])
@traced
async def cron_ping(request: Request):
    """调度连通性测试: 无条件写日志 —— 验证 EdgeOne schedules 是否真正触发函数"""
    if not await _authed(request):
        return fail("未授权", 401)
    _log("info", "schedules ping 触发(平台调度验证)")
    return ok({"pong": True, "ts": datetime.now(timezone.utc).isoformat()})


@router.api_route("/cron/recycle", methods=["GET","POST"])
@traced
@_pipe("recycle", rows_key="deleted")
async def cron_recycle(request: Request):
    """回收站清理: 软删超过 30 天的订单/规则永久删除(deleted_at != '' 且 < now-30d)

    2026-11-02 S0: 不可逆删除, 加当日一次性抢占; 幂等(DELETE 按条件), 失败可安全回滚重试"""
    if not await _authed(request):
        return fail("未授权", 401)
    if not _claim("cron:recycle"):
        return ok({"skipped": True, "reason": "今天已清理"})
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
    """对账核心(同步, 供 cron 端点与应用层兜底共用) —— 2026-11-02 S2-3 扩为两段

    ① 日销: [今-days, 昨] 内 SUM(快照 order_count) == SUM(订单 quantity)
       · 排除当天: 当天订单仍在增长, 快照可能滞后(差异属正常)
       · 口径: 销量池 SALES_SQL(=已完成), 与快照写入同源
    ② GMV : orders_day_agg 的 gmv/cnt == 订单源表同口径重算(见 _reconcile_gmv)
       · 原因: 原对账只比"行数/件数", 金额错了根本发现不了; 而看板 GMV 全靠这张物化表
       · 窗口比日销段晚一天收口(到 today-2): 软删残留靠 _rebuild_agg_day 单日精准自愈,
         最近两天可能还没自愈, 报出来是噪音而非数据错

    第三段(SKU 统计 sku_sales_stats)刻意不做: 它是从快照派生的二阶物化, 源头已被①覆盖,
    而全量重算 90 天 SKU 日销的成本远高于它能发现的问题 —— 真要查, 用
    /api/health?detail=1 看 mat_rebuild 的耗时与行数是否异常。

    差异 ≠ 0 → error 日志(source=cron), 前端质量页可见。
    """
    try:
        _d = max(1, min(int(days or 90), 365))
    except Exception:
        _d = 90
    _now = datetime.now(timezone.utc)
    _start = (_now - timedelta(days=_d)).strftime("%Y-%m-%d")
    _end = (_now - timedelta(days=1)).strftime("%Y-%m-%d")
    # GMV 段晚一天收口: 软删残留靠 _rebuild_agg_day 单日精准自愈, 最近两天可能尚未重建,
    # 比进去只会产生"看起来是数据错、其实是还没自愈"的噪音。
    _end_gmv = (_now - timedelta(days=2)).strftime("%Y-%m-%d")
    out = {}
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
    if not _bad:
        _log("info", "日销对账一致 %s~%s: %s" % (
            _start, _end,
            " / ".join("%s=%d" % (k, v.get("snapshot", 0)) for k, v in out.items() if "snapshot" in v)))
    return {"range": [_start, _end], "channels": out, "mismatch": _bad}


@router.api_route("/cron/reconcile", methods=["GET", "POST"])
@traced
@_pipe("reconcile")
async def cron_reconcile(request: Request, days: int = 90):
    """日销对账(cron 入口)——体量与口径见 run_reconcile

    2026-11-02 S0: 加 5 分钟限频(只读, 重复执行无害但耗 RU); days 已在 run_reconcile 内 clamp 到 [1,365]"""
    if not await _authed(request):
        return fail("未授权", 401)
    if not _throttle("reconcile", 300):
        return ok({"skipped": True, "reason": "5 分钟内已对账"})
    return ok(run_reconcile(days))

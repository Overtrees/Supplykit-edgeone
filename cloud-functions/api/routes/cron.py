"""原生定时任务路由(方案 B): EdgeOne schedules 调度的外部化任务

PA 版 APScheduler 10 任务中, TiDB 场景需要外部化的核心任务(备份/磁盘/WAL 已在 PA 版自动跳过):
- snapshot:          每天构建日销快照(增量补全最近 90 天)
- freshness:         每小时检查快照新鲜度(>2 天陈旧自动重建)
- archive:           每天归档 90 天前订单 → daily_stats(写入失败保护不删)
- cleanup-logs:      每天清理 quality_logs 保留最近 500
- daily-rules:       每天滞销识别告警 + 孤儿告警清理
- recycle:           每天清理超 30 天软删数据(订单/规则)
- push-alerts:       每 30 分钟推送新告警到 webhook(设置页 webhook_url)

安全: /cron/* 放行鉴权中间件, 路由内校验 CRON_SECRET(query secret 或 X-Cron-Secret 头)
"""
import json
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi import Request

from db import query, one, execute
from routes.common import ok, fail, traced

router = APIRouter(tags=["cron"])

PAID = ("待发货", "已发货", "已完成", "申请退款")
_PAID_SQL = ",".join("'%s'" % s for s in PAID)


def _authed(request: Request) -> bool:
    """CRON_SECRET 校验(未配置时依赖平台签名保护放行)"""
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        return True
    q = request.query_params.get("secret", "")
    h = request.headers.get("X-Cron-Secret", "")
    return q == secret or h == secret


def _log(level, message, source="cron"):
    try:
        execute("INSERT INTO quality_logs(log_type, level, message, source) VALUES('cron','%s',%s,%s)"
                % level, (message, source))
    except Exception:
        pass


def _build_snapshot(rebuild_days=90):
    """增量构建日销快照: 聚合最近 rebuild_days 天已支付订单(幂等 upsert)"""
    start = (datetime.now(timezone.utc) - timedelta(days=rebuild_days)).strftime("%Y-%m-%d")
    execute(
        "INSERT INTO daily_sales_snapshot(date, channel, sku, warehouse, order_count) "
        "SELECT DATE(ordered_at), channel, sku, warehouse, SUM(quantity) FROM orders "
        "WHERE order_status IN (%s) AND (deleted_at IS NULL OR deleted_at='') "
        "AND ordered_at >= %%s "
        "GROUP BY DATE(ordered_at), channel, sku, warehouse "
        "ON DUPLICATE KEY UPDATE order_count=VALUES(order_count)" % _PAID_SQL, [start])
    r = one("SELECT COUNT(*) AS c FROM daily_sales_snapshot") or {}
    return int(r.get("c") or 0)


@router.post("/cron/snapshot")
@traced
async def cron_snapshot(request: Request):
    if not _authed(request):
        return fail("未授权", 401)
    n = _build_snapshot()
    _log("info", "日销快照构建完成, 快照总行数 %d" % n)
    return ok({"snapshot_rows": n})


@router.post("/cron/freshness")
@traced
async def cron_freshness(request: Request):
    """快照新鲜度守护: MAX(date) < 今天-2 天 → 重建"""
    if not _authed(request):
        return fail("未授权", 401)
    r = one("SELECT COALESCE(MAX(date),'') AS m FROM daily_sales_snapshot") or {}
    m = str(r.get("m") or "")
    stale_limit = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    if not m or m < stale_limit:
        n = _build_snapshot()
        _log("warning", "快照陈旧(max=%s), 已重建(%d 行)" % (m or "无", n))
        return ok({"rebuilt": True, "rows": n})
    return ok({"rebuilt": False, "max_date": m})


@router.post("/cron/archive")
@traced
async def cron_archive(request: Request):
    """归档 90 天前订单 → daily_stats(写入成功才删除原订单, 防数据丢失)"""
    if not _authed(request):
        return fail("未授权", 401)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
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
        except Exception:
            pass
    if ok_n != len(old):
        _log("error", "归档中止: daily_stats 写入 %d/%d, 不删除 orders(防数据丢失)" % (ok_n, len(old)))
        return ok({"archived": 0, "stats_rows": ok_n, "aborted": True})
    ids = [o.get("id") for o in old]
    for i in range(0, len(ids), 200):
        batch = ids[i:i + 200]
        execute("DELETE FROM orders WHERE id IN (%s)" % ",".join(["%s"] * len(batch)), batch)
    _log("info", "归档: %d 订单 → daily_stats" % len(old))
    return ok({"archived": len(old), "stats_rows": ok_n})


@router.post("/cron/cleanup-logs")
@traced
async def cron_cleanup_logs(request: Request):
    if not _authed(request):
        return fail("未授权", 401)
    r = one("SELECT COUNT(*) AS c FROM quality_logs") or {}
    total = int(r.get("c") or 0)
    deleted = 0
    if total > 500:
        keep = one("SELECT id FROM quality_logs ORDER BY id DESC LIMIT 500") or {}
        kid = keep.get("id")
        if kid:
            deleted = int((one("SELECT COUNT(*) AS c FROM quality_logs WHERE id<%s", [kid]) or {}).get("c") or 0)
            execute("DELETE FROM quality_logs WHERE id<%s", [kid])
    return ok({"total": total, "deleted": deleted})


@router.post("/cron/daily-rules")
@traced
async def cron_daily_rules(request: Request):
    """每日规则: 全量规则评估(低库存/紧急补货/超卖/滞销 + 用户自定义) + 孤儿告警清理"""
    if not _authed(request):
        return fail("未授权", 401)
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
    # 2. 全量规则评估(替代简化滞销逻辑): inventory.changed + scheduled.daily 遍历库存 SKU
    from core.rules import evaluate_stock_skus
    triggered = evaluate_stock_skus("jd", limit=2000)
    _log("info", "每日规则: 孤儿告警清理 %d, 规则触发 %d 个(%s)" % (
        cleaned, len(triggered), ",".join(str(t)[:20] for t in triggered[:8])))
    return ok({"orphan_cleaned": cleaned, "rules_triggered": triggered})


@router.post("/cron/recycle")
@traced
async def cron_recycle(request: Request):
    """回收站清理: 软删超过 30 天的订单/规则永久删除(deleted_at != '' 且 < now-30d)"""
    if not _authed(request):
        return fail("未授权", 401)
    n1 = execute("DELETE FROM orders WHERE deleted_at IS NOT NULL AND deleted_at != '' "
                 "AND deleted_at < DATE_SUB(NOW(), INTERVAL 30 DAY)")
    n2 = execute("DELETE FROM rules WHERE is_active=0 AND deleted_at IS NOT NULL AND deleted_at != '' "
                 "AND deleted_at < DATE_SUB(NOW(), INTERVAL 30 DAY)")
    total = int(n1 or 0) + int(n2 or 0)
    if total > 0:
        _log("info", "回收站清理: 永久删除 %d 条(订单 %s + 规则 %s)" % (total, n1, n2))
    return ok({"deleted": total})


@router.post("/cron/push-alerts")
@traced
async def cron_push_alerts(request: Request):
    """推送最近 60 分钟新增未推送告警到 webhook(钉钉/企微格式)"""
    if not _authed(request):
        return fail("未授权", 401)
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
            _ch = "京东" if (r.get("channel") or "jd") == "jd" else "其他"
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
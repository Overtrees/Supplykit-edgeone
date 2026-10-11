"""原生任务路由(方案 B): 任务列表 + 种子数据填充/重置 + 导出/下载

契约要点(与前端直连 fetch 对齐):
- POST /exports → 平铺 {ok, task_id}(HammerInsights 期待 d.task_id)
- POST /seed/fill → {ok, data:{task_id}} 或 {ok, data:{requires_reset:true}}
- GET  /seed/fill/status → {data:{status: pending|running|done|error|not_found}}
- GET  /tasks → {ok, data:[...]}(TaskPage 期待 d.data 数组)
- 数据文件无文件系统 → 存 export_files 表(惰性建表), download 从 DB 读出
"""
import csv
import io
import json
import random
import time
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import Response

from db import query, one, execute
from routes.common import ok, fail, traced, try_err
from routes.task_export import create_export, export_status, _save_export_part, export_download, _build_export_rows, now_stamp, router as export_router

router = APIRouter(tags=["tasks"])
router.include_router(export_router)  # 导出口/分队(拆分至 task_export 后 include)

_TABLES_RESET = ["orders", "inventory", "products", "daily_sales_snapshot", "alerts",
                 "suppliers", "rules", "replenishment_config", "purchase_orders",
                 "disposal_records", "batches", "quality_logs", "cleansing_errors",
                 "cleansing_templates", "custom_fields", "events", "inbound_records",
                 "outbound_records", "daily_stats", "warehouse_registry", "sync_tasks", "orders_day_agg", "health_snapshot", "maintenance_log", "log_archives"]

_BRANDS_FOOD = ["禾味", "山泉", "椒香", "酱乡", "醋乡", "味源", "禾田", "青禾", "禾风",
                "谷香", "醇味", "鲜禾", "禾记"]
_BRANDS_SNACK = ["薯乐", "果脆", "禾果", "咔脆", "香脆"]
_BRANDS_DAILY = ["净洁", "柔白", "净香", "洁舒", "柔洁", "净白"]
_CATS = ["酱油", "醋", "酱料", "调味汁", "食用油", "饮料", "零食", "休闲食品", "日化清洁"]
_STORES = ["自营旗舰店", "直营店", "调味品专营店", "食品专营店", "日化专营店"]


def _new_task_id(prefix):
    return "%s_%d_%04d" % (prefix, int(time.time()), random.randint(0, 9999))


def _log_task(task_id, task_type, status, channel, result=""):
    execute("INSERT INTO sync_tasks(task_id, task_type, status, params, result, channel) "
            "VALUES(%s,%s,%s,'{}',%s,%s)",
            (task_id, task_type, status, json.dumps(result, ensure_ascii=False), channel))


# ── 任务列表 ──────────────────────────────────────────────────────────────
@router.get("/tasks")
def list_tasks(channel: str = "jd", limit: int = 50):
    rows = query("SELECT task_id, task_type, status, params, result, channel, created_at "
                 "FROM sync_tasks WHERE channel=%s OR channel='' ORDER BY id DESC LIMIT %s",
                 [channel, limit])
    return ok(rows)


@router.get("/seed/fill/status")
def seed_fill_status(task_id: str = ""):
    """任务状态查询(Makers 异步适配: running 时续跑下一步, 客户端轮询推进直到 done)"""
    if not task_id:
        return {"data": {"status": "not_found"}}
    row = one("SELECT status, params FROM sync_tasks WHERE task_id=%s", [task_id])
    if not row:
        return {"data": {"status": "not_found"}}
    status = row.get("status") or "not_found"
    if status in ("done", "error"):
        return {"data": {"status": status}}
    # 防并发续跑(前端 App+Settings 双轮询/多实例): 原子抢占 updated_at 锁
    # 3 秒内已有请求续跑过 → 本次跳过(步骤执行中轮询不再重复触发, 防步骤并发冲突)
    try:
        _locked = execute("UPDATE sync_tasks SET updated_at=NOW() WHERE task_id=%s "
                          "AND (updated_at IS NULL OR updated_at < DATE_SUB(NOW(), INTERVAL 3 SECOND))",
                          [task_id])
        if not _locked:
            return {"data": {"status": "running"}}  # 已有请求在续跑, 跳过本次(防并发)
    except Exception as _e:
            try_err('tasks', '静默降级', _e)
    # running → 续跑下一步(每步 ≤90s, 函数 120s 上限内)
    try:
        import json as _json
        params = _json.loads(row.get("params") or "{}")
        step = int(params.get("step") or 0)
        from routes.seed_fill import prepare_skus, seed_step
        from datetime import datetime, timezone as _tz
        skus_data = prepare_skus()
        today = datetime.now(_tz.utc)
        nxt, part = seed_step(step, today, skus_data)
        new_params = _json.dumps({"step": nxt})
        if nxt >= 10:
            # 全部步骤完成 → done(汇总 part 简单展示)
            execute("UPDATE sync_tasks SET status='done', params=%s, result=%s, updated_at=NOW() WHERE task_id=%s",
                    (new_params, _json.dumps({"result": {"parts": part, "finished": True}}, ensure_ascii=False), task_id))
            from routes.analysis_cache import invalidate_all
            invalidate_all()  # seed 完成 → 分析缓存即时失效
            # 重置后 agg 物化表需重建(防首屏走直查慢): 后台线程重建 90 天, 完成前 summary 陈旧检测降级直查
            try:
                import threading as _th2
                from routes.dashboard import _rebuild_day_agg as _rda
                _th2.Thread(target=_rda, args=(90,), daemon=True).start()
            except Exception as _e:
                    try_err('tasks', '静默降级', _e)
            return {"data": {"status": "done"}}
        execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                (new_params, task_id))
        from routes.analysis_cache import invalidate_all
        invalidate_all()  # 每步数据变化 → 缓存失效
        return {"data": {"status": "running", "step": nxt, "part": part}}
    except Exception as e:
        import traceback as _tb
        try:
            execute("UPDATE sync_tasks SET status='error', result=%s, updated_at=NOW() WHERE task_id=%s",
                    (_json.dumps({"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]}, ensure_ascii=False), task_id))
        except Exception as _e:
                try_err('tasks', '静默降级', _e)
        return {"data": {"status": "error", "error": str(e)[:200]}}


# ── 种子数据填充(异步分步) ───────────────────────────────────────────────
@router.post("/seed/fill")
async def seed_fill(request: Request):
    """空库生成完整演示数据(PA 原版逻辑移植, 异步分步: 任务表驱动, status 轮询续跑)"""
    channel = "jd"
    try:
        d = await request.json()
        channel = d.get("channel", "jd")
    except Exception as _e:
            try_err('tasks', '静默降级', _e)
    cnt = one("SELECT COUNT(*) AS c FROM orders") or {}
    pct = one("SELECT COUNT(*) AS c FROM products") or {}
    alt = one("SELECT (SELECT COUNT(*) FROM alerts)+(SELECT COUNT(*) FROM inventory) AS c") or {}
    if int(cnt.get("c") or 0) > 0 or int(pct.get("c") or 0) > 0 or int(alt.get("c") or 0) > 0:
        return ok({"requires_reset": True})
    task_id = _new_task_id("seed")
    try:
        # 创建 running 任务 + 执行步骤 0(商品/供应商, 秒级)
        import json as _json
        from routes.seed_fill import prepare_skus, seed_step
        from datetime import datetime, timezone as _tz
        execute("INSERT INTO sync_tasks(task_id, task_type, status, params, result, channel) "
                "VALUES(%s,'seed','running',%s,'{}',%s)",
                (task_id, _json.dumps({"step": 0}), channel))
        skus_data = prepare_skus()
        today = datetime.now(_tz.utc)
        nxt, part = seed_step(0, today, skus_data)
        execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                (_json.dumps({"step": nxt}), task_id))
        return ok({"task_id": task_id, "started": True})
    except Exception as e:
        import traceback as _tb
        try:
            execute("UPDATE sync_tasks SET status='error', result=%s, updated_at=NOW() WHERE task_id=%s",
                    (_json.dumps({"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]}, ensure_ascii=False), task_id))
        except Exception as _e:
                try_err('tasks', '静默降级', _e)
        return fail("种子填充启动失败: %s" % str(e)[:200])


# ── 数据维护(admin) ────────────────────────────────────────────────────────
@router.post("/tasks/maintain")
async def tasks_maintain(request: Request):
    """数据维护入口(admin): action=rebuild_agg → 后台重建 orders_day_agg 90 天(重置后/陈旧修复)"""
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    from routes.common import verify_token
    if verify_token(token) != "admin":
        return fail("仅 admin 可用", 403)
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('tasks', '静默降级', _e)
    action = d.get("action") or "rebuild_agg"
    channel = d.get("channel") or "jd"
    mode = d.get("mode") or "bbcc"
    if action == "rebuild_agg":
        import threading as _th3
        from routes.dashboard import _rebuild_day_agg as _rda3
        _th3.Thread(target=_rda3, args=(90,), daemon=True).start()
        from routes.analysis_cache import invalidate_all
        invalidate_all()
        return ok({"started": True, "action": "rebuild_agg"})
    if action == "warmup":
        # 预热各缓存(后台顺序调用 builder, 填 300s 共享表缓存)——部署/重置后调用一次, 首屏不撞重算
        def _w():
            try:
                from routes.dashboard import dashboard_summary, dashboard_aux, stock_risk
                from routes.replenishment import get_replenishment_suggestions
                from routes.purchase import purchase_suggestions
                dashboard_summary(channel, "", "", mode)
                dashboard_aux(channel, mode)
                stock_risk(channel)
                get_replenishment_suggestions(days=28, source="", mode=mode, channel=channel)
                purchase_suggestions(days=28, mode=mode, channel=channel)
            except Exception as _e:
                    try_err('tasks', '预热降级', _e)
        import threading as _th4
        _th4.Thread(target=_w, daemon=True).start()
        return ok({"started": True, "action": "warmup"})
    return fail("未知 action: %s" % action)


# ── 数据重置 ──────────────────────────────────────────────────────────────
@router.post("/seed/reset")
async def seed_reset(request: Request):
    """清空全部业务数据(恢复初始状态)"""
    channel = "jd"
    try:
        d = await request.json()
        channel = d.get("channel", "jd")
    except Exception as _e:
            try_err('tasks', '静默降级', _e)
    task_id = _new_task_id("reset")
    try:
        for t in _TABLES_RESET:
            # 分批删除(TiDB serverless 内存限制: 单条大 DELETE 全表会被取消)
            while True:
                n = execute("DELETE FROM `%s` LIMIT 10000" % t)
                if not n or int(n or 0) < 10000:
                    break
        _log_task(task_id, "reset", "done", channel, {"result": {"reset": "all"}})
        from routes.analysis_cache import invalidate_all
        invalidate_all()
        return ok({"task_id": task_id})
    except Exception as e:
        import traceback as _tb
        _log_task(task_id, "reset", "error", channel,
                  {"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]})
        return fail("重置失败: %s" % str(e)[:200])# ── 导出任务 ──────────────────────────────────────────────────────────────
_EXPORT_DDL = ("CREATE TABLE IF NOT EXISTS export_files ("
               "id BIGINT PRIMARY KEY AUTO_INCREMENT, filename VARCHAR(128) DEFAULT '', "
               "content LONGTEXT, channel VARCHAR(20) DEFAULT 'jd', "
               "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
               "UNIQUE KEY uk_filename (filename))")












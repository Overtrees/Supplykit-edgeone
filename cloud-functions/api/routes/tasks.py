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
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi import Request
from fastapi.responses import Response

from db import query, one, execute, executemany
from routes.common import ok, fail, traced

router = APIRouter(tags=["tasks"])

_TABLES_RESET = ["orders", "inventory", "products", "daily_sales_snapshot", "alerts",
                 "suppliers", "rules", "replenishment_config", "purchase_orders",
                 "disposal_records", "batches", "quality_logs", "cleansing_errors",
                 "cleansing_templates", "custom_fields", "events", "inbound_records",
                 "outbound_records", "daily_stats", "warehouse_registry", "sync_tasks"]

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
@traced
def list_tasks(channel: str = "jd", limit: int = 50):
    rows = query("SELECT task_id, task_type, status, params, result, channel, created_at "
                 "FROM sync_tasks WHERE channel=%s OR channel='' ORDER BY id DESC LIMIT %s",
                 [channel, limit])
    return ok(rows)


@router.get("/seed/fill/status")
@traced
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
            return {"data": {"status": "done"}}
        execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                (new_params, task_id))
        return {"data": {"status": "running", "step": nxt, "part": part}}
    except Exception as e:
        import traceback as _tb
        try:
            execute("UPDATE sync_tasks SET status='error', result=%s, updated_at=NOW() WHERE task_id=%s",
                    (_json.dumps({"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]}, ensure_ascii=False), task_id))
        except Exception:
            pass
        return {"data": {"status": "error", "error": str(e)[:200]}}


# ── 种子数据填充(异步分步) ───────────────────────────────────────────────
@router.post("/seed/fill")
@traced
async def seed_fill(request: Request):
    """空库生成完整演示数据(PA 原版逻辑移植, 异步分步: 任务表驱动, status 轮询续跑)"""
    channel = "jd"
    try:
        d = await request.json()
        channel = d.get("channel", "jd")
    except Exception:
        pass
    cnt = one("SELECT COUNT(*) AS c FROM orders") or {}
    pct = one("SELECT COUNT(*) AS c FROM products") or {}
    if int(cnt.get("c") or 0) > 0 or int(pct.get("c") or 0) > 0:
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
        except Exception:
            pass
        return fail("种子填充启动失败: %s" % str(e)[:200])


# ── 数据重置 ──────────────────────────────────────────────────────────────
@router.post("/seed/reset")
@traced
async def seed_reset(request: Request):
    """清空全部业务数据(恢复初始状态)"""
    channel = "jd"
    try:
        d = await request.json()
        channel = d.get("channel", "jd")
    except Exception:
        pass
    task_id = _new_task_id("reset")
    try:
        for t in _TABLES_RESET:
            # 分批删除(TiDB serverless 内存限制: 单条大 DELETE 全表会被取消)
            while True:
                n = execute("DELETE FROM `%s` LIMIT 10000" % t)
                if not n or int(n or 0) < 10000:
                    break
        _log_task(task_id, "reset", "done", channel, {"result": {"reset": "all"}})
        return ok({"task_id": task_id})
    except Exception as e:
        import traceback as _tb
        _log_task(task_id, "reset", "error", channel,
                  {"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]})
        return fail("重置失败: %s" % str(e)[:200])# ── 导出任务 ──────────────────────────────────────────────────────────────
_EXPORT_DDL = ("CREATE TABLE IF NOT EXISTS export_files ("
               "id BIGINT PRIMARY KEY AUTO_INCREMENT, filename VARCHAR(128) DEFAULT '', "
               "content MEDIUMBLOB, channel VARCHAR(20) DEFAULT 'jd', "
               "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
               "UNIQUE KEY uk_filename (filename))")


@router.post("/exports")
@traced
async def create_export(request: Request):
    """导出任务(POST ?type=replen|purchase_suggestions|purchase|slow&mode=bbcc|traditional&channel=)
    返回平铺 {ok, task_id}(HammerInsights 期待 d.task_id); 生成 xlsx 二进制存 export_files"""
    from urllib.parse import parse_qs
    qs = parse_qs(request.url.query)
    exp_type = (qs.get("type") or ["replen"])[0]
    mode = (qs.get("mode") or ["bbcc"])[0]
    channel = (qs.get("channel") or ["jd"])[0]
    task_id = _new_task_id("exp")
    started = time.time()
    try:
        wh_type = (qs.get("wh_type") or [""])[0]
        rows = _build_export_rows(exp_type, mode, channel, wh_type)
        if rows is None:
            return fail("未知导出类型: " + str(exp_type))
        # orders/inventory 大批量 → CSV 轻量生成(避免 12 万行 xlsx 内存超限); 其他 → xlsx
        if exp_type in ("orders", "inventory"):
            buf = io.StringIO()
            if rows:
                writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            content = buf.getvalue().encode("utf-8")
            filename = "exports_%s_%s_%s.csv" % (exp_type, channel, now_stamp())
        else:
            from openpyxl import Workbook
            from openpyxl.styles import Font
            buf = io.BytesIO()
            wb = Workbook()
            ws = wb.active
            ws.title = "导出"
            if rows:
                headers = list(rows[0].keys())
                ws.append(headers)
                for c in ws[1]:
                    c.font = Font(bold=True)
                for r in rows:
                    ws.append([r.get(k) for k in headers])
            wb.save(buf)
            content = buf.getvalue()
            filename = "exports_%s_%s_%s.xlsx" % (exp_type, channel, now_stamp())
        # content 列可能仍为 TEXT(ALTER 异步/失败)——二进制 base64 兜底
        import base64 as _b64
        try:
            execute("ALTER TABLE export_files MODIFY COLUMN content MEDIUMBLOB")
        except Exception:
            pass
        try:
            execute("INSERT INTO export_files(filename, content, channel) VALUES(%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE content=VALUES(content)",
                    (filename, content, channel))
        except Exception:
            execute("INSERT INTO export_files(filename, content, channel) VALUES(%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE content=VALUES(content)",
                    (filename, "base64:" + _b64.b64encode(content).decode("ascii"), channel))
        _log_task(task_id, "export", "done", channel,
                  {"result": {"filename": filename, "type": exp_type,
                              "rows": len(rows), "elapsed": round(time.time() - started, 1)}})
        return {"ok": True, "task_id": task_id}
    except Exception as e:
        _log_task(task_id, "export", "error", channel, {"error": str(e)[:400]})
        return fail("导出失败: %s" % str(e)[:200])


@router.get("/exports/download/{filename}")
@traced
def export_download(filename: str):
    row = one("SELECT content FROM export_files WHERE filename=%s", [filename])
    if not row:
        return fail("导出文件不存在", 404)
    content = row.get("content")
    if isinstance(content, str):
        if content.startswith("base64:"):
            import base64 as _b64
            data = _b64.b64decode(content[7:])
        else:
            data = content.encode("utf-8")
    elif content is None:
        data = b""
    else:
        data = bytes(content)
    if filename.endswith(".xlsx"):
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        media = "text/csv; charset=utf-8"
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": "attachment; filename=%s" % filename})


def now_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _build_export_rows(exp_type, mode, channel, wh_type=""):
    """按类型生成导出行(复用现有路由逻辑, 均为字典列表)"""
    if exp_type in ("replen", "purchase_suggestions", "purchase"):
        from routes.replenishment import get_replenishment_suggestions
        r = get_replenishment_suggestions(days=28, mode=mode, channel=channel)
        items = (r.get("data") or []) if isinstance(r, dict) else (r or [])
        if exp_type == "purchase_suggestions":
            # 采购口径: 以 C 仓缺口为主视图(备注列区分)
            out = []
            for it in items:
                out.append({
                    "SKU": it.get("sku", ""), "商品名": it.get("product_name", ""),
                    "69码": it.get("barcode", ""), "品牌": it.get("brand", ""),
                    "日销(融合)": it.get("daily_sales", ""), "日销14": it.get("daily_sales_14", ""),
                    "日销28": it.get("daily_sales_28", ""), "系统总缺口": it.get("raw_suggested", ""),
                    "建议采购量": it.get("suggested_qty", ""), "备注": it.get("note", ""),
                })
            return out
        return items
    if exp_type == "slow":
        from routes.insights import slow_moving
        r = slow_moving(channel=channel)
        items = (r.get("data") or []) if isinstance(r, dict) else (r or [])
        return items
    if exp_type == "orders":
        # 订单导出(当前渠道, 最多 5 万行)
        from routes.orders import list_orders
        r = list_orders(channel=channel, page=1, page_size=50000)
        data = (r.get("data") or {}) if isinstance(r, dict) else (r or [])
        items = data.get("items") if isinstance(data, dict) else (data or [])
        out = []
        for o in items:
            out.append({
                "订单号": o.get("order_no", ""), "店铺": o.get("store", ""),
                "仓库": o.get("warehouse", ""), "SKU": o.get("sku", ""),
                "商品名": o.get("product_name", ""), "69码": o.get("barcode", ""),
                "数量": o.get("quantity", ""), "单价": o.get("unit_price", ""),
                "金额": o.get("total_amount", ""), "状态": o.get("order_status", ""),
                "下单时间": str(o.get("ordered_at") or "")[:10],
                "支付时间": str(o.get("paid_at") or "")[:10],
                "平台": o.get("platform", ""), "渠道": o.get("channel", ""),
            })
        return out
    if exp_type == "inventory":
        # 进销存导出(当前渠道+仓型, 最多 5 万行)
        from routes.insights import inventory_with_sales
        r = inventory_with_sales(wh_type=wh_type or "own", channel=channel,
                                 page=1, page_size=50000)
        data = (r.get("data") or {}) if isinstance(r, dict) else (r or [])
        items = data.get("items") if isinstance(data, dict) else (data or [])
        out = []
        for it in items:
            out.append({
                "SKU": it.get("sku", ""), "商品名": it.get("product_name", ""),
                "仓库": it.get("warehouse", ""), "仓库类型": it.get("warehouse_type", ""),
                "可用": it.get("available_qty", ""), "在途": it.get("in_transit_qty", ""),
                "B-C调拨在途": it.get("c_transit", ""), "安全线": it.get("safety_qty", ""),
                "日销": it.get("daily_sales", ""), "周转天数": it.get("turnover_days", ""),
                "当月入库": it.get("month_inbound", ""), "当月出库": it.get("month_outbound", ""),
                "期初": it.get("beginning_stock", ""), "品牌": it.get("brand", ""),
            })
        return out
    return None
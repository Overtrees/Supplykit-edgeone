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
    if not task_id:
        return {"data": {"status": "not_found"}}
    row = one("SELECT status FROM sync_tasks WHERE task_id=%s", [task_id])
    status = (row or {}).get("status") or "not_found"
    return {"data": {"status": status}}


# ── 种子数据填充 ──────────────────────────────────────────────────────────
@router.post("/seed/fill")
@traced
async def seed_fill(request: Request):
    """空库生成完整演示数据(PA 原版逻辑移植: 2000 SKU×2渠道/60天约10万订单), 有数据则 requires_reset"""
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
    started = time.time()
    try:
        from routes.seed_fill import run_seed_fill
        summary = run_seed_fill()
        _log_task(task_id, "seed", "done", channel, {"result": summary})
        return ok({"task_id": task_id, "summary": summary})
    except Exception as e:
        import traceback as _tb
        _log_task(task_id, "seed", "error", channel,
                  {"error": str(e)[:400], "tb": _tb.format_exc()[-1800:]})
        return fail("种子填充失败: %s" % str(e)[:200])


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
        rows = _build_export_rows(exp_type, mode, channel)
        if rows is None:
            return fail("未知导出类型: " + str(exp_type))
        # 生成 xlsx(openpyxl; 空数据也生成带表头的工作簿)
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


def _build_export_rows(exp_type, mode, channel):
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
    return None
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
    """空库生成演示数据(有数据则 requires_reset)"""
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
        random.seed(42)
        now = datetime.now(timezone.utc)

        # 1. 商品 120 个(虚拟品牌)
        products = []
        for i in range(1, 121):
            if i <= 90:
                brand = random.choice(_BRANDS_FOOD)
                cat = random.choice(_CATS[:7])
                name = "%s %s%s %s" % (brand, cat, random.randint(1, 99), random.choice(["500ml", "300ml", "1L", "200g", "450ml"]))
            elif i <= 105:
                brand = random.choice(_BRANDS_SNACK)
                cat = random.choice(_CATS[6:8])
                name = "%s %s%s %s" % (brand, cat, random.randint(1, 99), random.choice(["80g", "120g", "150g"]))
            else:
                brand = random.choice(_BRANDS_DAILY)
                cat = _CATS[8]
                name = "%s %s%s %s" % (brand, cat, random.randint(1, 99), random.choice(["500ml", "1L", "300g"]))
            products.append({
                "sku": "SKU%04d" % i, "product_name": name, "barcode": "69%013d" % (1000000000000 + i),
                "store": random.choice(_STORES), "category": cat, "price": round(random.uniform(5, 40), 1),
                "box_qty": 12 if i % 2 else 24, "unit": "瓶" if i % 3 else "袋",
                "status": "active", "channel": channel,
                "weight": round(random.uniform(0.1, 2), 2), "volume": round(random.uniform(0.1, 3), 2),
                "brand": brand,
            })
        executemany("INSERT INTO products(sku, product_name, barcode, store, category, price, box_qty, unit, status, channel, weight, volume, brand) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [tuple(p[k] for k in ("sku", "product_name", "barcode", "store", "category", "price", "box_qty", "unit", "status", "channel", "weight", "volume", "brand")) for p in products])

        # 2. 供应商 8 家(虚构)
        sup_names = ["云味食品(演示)", "山泉饮品(演示)", "椒香食品(演示)", "酱乡调味(演示)",
                     "禾田农业(演示)", "鲜禾食品(演示)", "净洁日化(演示)", "咔脆零食(演示)"]
        suppliers = []
        for i, nm in enumerate(sup_names):
            suppliers.append({
                "supplier_code": "SUP%03d" % (i + 1), "supplier_name": nm,
                "contact_person": random.choice(["王小明", "李小红", "张伟", "刘芳"]),
                "contact_phone": "010-800%04d" % random.randint(0, 9999),
                "score": random.randint(70, 98), "status": "active", "channel": channel,
                "brand": products[i]["brand"],
            })
        executemany("INSERT INTO suppliers(supplier_code, supplier_name, contact_person, contact_phone, score, status, channel, brand) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                    [tuple(s[k] for k in ("supplier_code", "supplier_name", "contact_person", "contact_phone", "score", "status", "channel", "brand")) for s in suppliers])

        # 3. 订单: 60 天 × 120 SKU(周末 0.8 / 8-12 天促销 2.5 倍 / 部分 SKU 断货窗口)
        base_daily = list(range(3, 46))
        random.shuffle(base_daily)
        orders = []
        order_no = 1
        day_sales = {}  # (date, sku) -> qty
        for day_offset in range(60, 0, -1):
            d = (now - timedelta(days=day_offset)).date()
            is_weekend = d.weekday() >= 5
            for p in products:
                sku = p["sku"]
                base = base_daily[(int(sku[3:]) - 1) % len(base_daily)]
                factor = 0.8 if is_weekend else 1.0
                if 8 <= day_offset <= 12:
                    factor *= 2.5
                if 3 <= day_offset <= 6 and int(sku[3:]) % 37 == 5:
                    factor = 0  # 断货窗口
                qty = max(0, int(base * factor * random.uniform(0.7, 1.3)))
                if qty <= 0:
                    day_sales[(str(d), sku)] = 0
                    continue
                status = random.choices(["已完成", "待发货", "待确认", "申请退款"], [0.72, 0.15, 0.08, 0.05])[0]
                ordered_at = "%s %02d:%02d:%02d" % (d, random.randint(8, 22), random.randint(0, 59), random.randint(0, 59))
                amount = round(p["price"] * qty, 2)
                orders.append({
                    "order_no": "NO%011d" % order_no, "store": p["store"], "warehouse": "华东C仓",
                    "sku": sku, "product_name": p["product_name"], "barcode": p["barcode"],
                    "quantity": qty, "unit_price": p["price"], "total_amount": amount,
                    "order_status": status, "ordered_at": ordered_at,
                    "paid_at": ("%s %02d:%02d:%02d" % (d, random.randint(9, 23), random.randint(0, 59), random.randint(0, 59))) if status in ("已完成", "待发货") else "",
                    "platform": "jd", "channel": channel, "data_source": "seed",
                    "freight_amount": 0, "subsidy_amount": 0, "tax_amount": 0, "discount_amount": 0,
                })
                order_no += 1
                day_sales[(str(d), sku)] = day_sales.get((str(d), sku), 0) + qty
        for i in range(0, len(orders), 500):
            executemany("INSERT INTO orders(order_no, store, warehouse, sku, product_name, barcode, quantity, unit_price, total_amount, order_status, ordered_at, paid_at, platform, channel, data_source, freight_amount, subsidy_amount, tax_amount, discount_amount) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [tuple(o[k] for k in ("order_no", "store", "warehouse", "sku", "product_name", "barcode", "quantity", "unit_price", "total_amount", "order_status", "ordered_at", "paid_at", "platform", "channel", "data_source", "freight_amount", "subsidy_amount", "tax_amount", "discount_amount")) for o in orders[i:i + 500]])

        # 4. 库存: 自有1仓 + C仓3 + B仓1
        c_whs = ["华北C仓", "华东C仓", "华南C仓"]
        warehouse_rows = [("集货仓", "own")] + [(w, "platform") for w in c_whs] + [("B仓1", "platform_b")]
        inv_rows = []
        for p in products:
            sku = p["sku"]
            ds = base_daily[(int(sku[3:]) - 1) % len(base_daily)]
            safety = max(ds * 6, 20)
            for wh, wt in warehouse_rows:
                if wt == "own":
                    avail = random.choice([0, 0, int(safety * random.uniform(2, 4))])
                elif wt == "platform":
                    avail = random.choice([0] * 2 + [int(safety * random.uniform(1, 5))] * 5)
                else:
                    avail = int(safety * random.uniform(2, 6))
                inv_rows.append({
                    "sku": sku, "product_name": p["product_name"], "store": p["store"],
                    "warehouse": wh, "warehouse_type": wt, "available_qty": avail,
                    "in_transit_qty": random.choice([0, 0, int(safety * 0.3)]),
                    "safety_qty": int(safety), "channel": channel, "barcode": p["barcode"],
                })
        for i in range(0, len(inv_rows), 300):
            executemany("INSERT INTO inventory(sku, product_name, store, warehouse, warehouse_type, available_qty, in_transit_qty, safety_qty, channel, barcode) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [tuple(x[k] for k in ("sku", "product_name", "store", "warehouse", "warehouse_type", "available_qty", "in_transit_qty", "safety_qty", "channel", "barcode")) for x in inv_rows[i:i + 300]])

        # 5. 日销快照(按 SKU×日×随机一 C 仓)
        snap = []
        for (ds_key, qty), _ in [(k, v) for k, v in day_sales.items()]:
            d, sku = ds_key
            if qty <= 0:
                continue
            p = next((x for x in products if x["sku"] == sku), None)
            if not p:
                continue
            snap.append({"date": d, "channel": channel, "sku": sku,
                         "warehouse": random.choice(c_whs), "order_count": qty})
        for i in range(0, len(snap), 500):
            executemany("INSERT INTO daily_sales_snapshot(date, channel, sku, warehouse, order_count) "
                        "VALUES(%s,%s,%s,%s,%s)",
                        [tuple(s[k] for k in ("date", "channel", "sku", "warehouse", "order_count")) for s in snap[i:i + 500]])

        # 6. 批次(部分 SKU 2-3 批)
        batches = []
        for p in products[:80]:
            sku = p["sku"]
            for b in range(random.randint(1, 3)):
                prod = (now - timedelta(days=random.randint(30, 200))).date()
                batches.append({
                    "sku": sku, "warehouse": random.choice(c_whs), "warehouse_type": "platform",
                    "channel": channel, "prod_date": str(prod),
                    "exp_date": str(prod + timedelta(days=180)), "qty": random.randint(20, 200),
                })
        if batches:
            for i in range(0, len(batches), 300):
                executemany("INSERT INTO batches(sku, warehouse, warehouse_type, channel, prod_date, exp_date, qty) "
                            "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                            [tuple(b[k] for k in ("sku", "warehouse", "warehouse_type", "channel", "prod_date", "exp_date", "qty")) for b in batches[i:i + 300]])

        # 7. 低库存告警
        low = query("SELECT i.sku, i.product_name, i.available_qty, i.safety_qty FROM inventory i "
                    "WHERE i.channel=%s AND i.warehouse_type='platform' "
                    "AND i.available_qty < i.safety_qty ORDER BY RAND() LIMIT 10", [channel])
        for r in low:
            execute("INSERT INTO alerts(alert_type, title, description, severity, status, source, related_sku, warehouse_type, channel) "
                    "VALUES('low_stock',%s,%s,'warning','active','seed',%s,'platform',%s)",
                    ("库存低于安全线", "SKU %s 可用 %s 低于安全线 %s" % (r.get("sku"), r.get("available_qty"), r.get("safety_qty")),
                     r.get("sku"), channel))

        _log_task(task_id, "seed", "done", channel,
                  {"result": {"orders": len(orders), "products": len(products),
                              "inventory": len(inv_rows), "snapshot": len(snap),
                              "elapsed": round(time.time() - started, 1)}})
        return ok({"task_id": task_id})
    except Exception as e:
        import traceback
        _log_task(task_id, "seed", "error", channel, {"error": str(e)[:400]})
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
            execute("DELETE FROM `%s`" % t)
        _log_task(task_id, "reset", "done", channel, {"result": {"reset": "all"}})
        return ok({"task_id": task_id})
    except Exception as e:
        _log_task(task_id, "reset", "error", channel, {"error": str(e)[:400]})
        return fail("重置失败: %s" % str(e)[:200])# ── 导出任务 ──────────────────────────────────────────────────────────────
_EXPORT_DDL = ("CREATE TABLE IF NOT EXISTS export_files ("
               "id BIGINT PRIMARY KEY AUTO_INCREMENT, filename VARCHAR(128) DEFAULT '', "
               "content MEDIUMTEXT, channel VARCHAR(20) DEFAULT 'jd', "
               "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
               "UNIQUE KEY uk_filename (filename))")


@router.post("/exports")
@traced
async def create_export(request: Request):
    """导出任务(POST ?type=replen|purchase_suggestions|purchase|slow&mode=bbcc|traditional&channel=)
    返回平铺 {ok, task_id}(HammerInsights 期待 d.task_id)"""
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
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        content = buf.getvalue()
        filename = "exports_%s_%s_%s.csv" % (exp_type, channel, now_stamp())
        execute(_EXPORT_DDL)
        execute("INSERT INTO export_files(filename, content, channel) VALUES(%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE content=VALUES(content)",
                (filename, content, channel))
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
    content = row.get("content") or ""
    # filename 纯 ASCII 安全(导出用 ASCII 命名), 直接放 Content-Disposition
    return Response(content=content, media_type="text/csv; charset=utf-8",
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
"""原生采购/处置路由(方案 B): purchase-orders CRUD + insights/purchase + 滞销处置建议"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi import Request

from db import query, one, execute
from routes.common import ok, fail, traced
from biz.sales import load_daily_sales_grouped, load_daily_sales, calc_sales_multi, rolling_predict

router = APIRouter(tags=["purchase"])


# ── 已下单标记(purchase-orders) ─────────────────────────────────────────
@router.get("/purchase-orders")
@traced
def list_purchase_orders(channel: str = "jd"):
    rows = query("SELECT id, sku, store, product_name, suggested_qty, actual_qty, arrival_date, status, channel "
                 "FROM purchase_orders WHERE channel=%s ORDER BY id ASC", [channel])
    return ok(rows)


@router.post("/purchase-orders")
@traced
async def create_purchase_order(request: Request):
    q = request.query_params
    sku = q.get("sku", "")
    store = q.get("store", "")
    channel = q.get("channel", "jd")
    if not sku:
        return fail("缺少 sku")
    product_name = q.get("product_name", "")
    suggested_qty = int(float(q.get("suggested_qty") or 0))
    execute("INSERT INTO purchase_orders(sku, store, product_name, suggested_qty, channel) "
            "VALUES(%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE product_name=VALUES(product_name), suggested_qty=VALUES(suggested_qty)",
            (sku, store, product_name, suggested_qty, channel))
    return ok({})


@router.delete("/purchase-orders")
@traced
async def delete_purchase_order(request: Request):
    q = request.query_params
    sku = q.get("sku", "")
    store = q.get("store", "")
    channel = q.get("channel", "jd")
    execute("DELETE FROM purchase_orders WHERE sku=%s AND store=%s AND channel=%s",
            (sku, store, channel))
    return ok({})


@router.put("/purchase-orders/{pid}")
@traced
async def update_purchase_order(pid: int, request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    sets, params = [], []
    for f in ("arrival_date", "actual_qty", "status"):
        if f in d and d[f] is not None:
            sets.append("`%s` = %%s" % f)
            params.append(d[f])
    if not sets:
        return fail("无更新字段")
    params.append(pid)
    execute("UPDATE purchase_orders SET %s WHERE id=%%s" % ", ".join(sets), params)
    return ok({})


# ── 采购建议(insights/purchase) ─────────────────────────────────────────
@router.get("/insights/purchase")
@traced
def purchase_suggestions(days: int = 28, mode: str = "bbcc", channel: str = "jd",
                         search: str = ""):
    """采购建议: 在补货建议基础上合并自有/B/C 仓口径 + 已下单量 + 补后周转"""
    from routes.replenishment import get_replenishment_suggestions
    r = get_replenishment_suggestions(days=days, mode=mode, channel=channel)
    items = (r.get("data") or []) if isinstance(r, dict) else (r or [])
    if not items:
        return ok({"suggestions": []})

    skus = [x.get("sku") for x in items if x.get("sku")]
    # 库存按仓型聚合
    inv = query("SELECT sku, warehouse_type, available_qty, in_transit_qty FROM inventory "
                "WHERE channel=%s", [channel]) if skus else []
    agg = {}
    for r2 in inv:
        sku = r2.get("sku")
        wt = r2.get("warehouse_type")
        d = agg.setdefault(sku, {"own_avail": 0, "own_transit": 0, "b_avail": 0, "b_transit": 0,
                                 "plat_avail": 0, "plat_transit": 0})
        q = int(r2.get("available_qty") or 0)
        t = int(r2.get("in_transit_qty") or 0)
        if wt == "own":
            d["own_avail"] += q
            d["own_transit"] += t
        elif wt == "platform_b":
            d["b_avail"] += q
            d["b_transit"] += t
        else:
            d["plat_avail"] += q
            d["plat_transit"] += t
    # 已下单(采购订单)
    ordered = {}
    for r3 in query("SELECT sku, store, suggested_qty, arrival_date FROM purchase_orders "
                    "WHERE channel=%s", [channel]):
        ordered[r3.get("sku")] = r3
    cfg = {}
    for r4 in query("SELECT `key`, value FROM replenishment_config WHERE channel=%s OR channel=''", [channel]):
        cfg[r4.get("key") or ""] = r4.get("value") or ""
    target_turn = float(cfg.get("target_turnover_days") or cfg.get("max_turnover_days") or 15)
    lead = int(cfg.get("purchase_lead_days") or 3)

    out = []
    for it in items:
        sku = it.get("sku")
        st = agg.get(sku) or {}
        sys_avail = st.get("own_avail", 0) + st.get("plat_avail", 0) + (st.get("b_avail", 0) if mode == "bbcc" else 0)
        sys_transit = st.get("own_transit", 0) + st.get("plat_transit", 0) + (st.get("b_transit", 0) if mode == "bbcc" else 0)
        ds = float(it.get("daily_sales") or 0)
        need = max(round(ds * lead - sys_avail - sys_transit, 0), 0)
        po = ordered.get(sku)
        actual = int((po or {}).get("suggested_qty") or 0)
        after = (sys_avail + sys_transit + need + actual) / ds if ds > 0 else None
        out.append({
            "sku": sku, "barcode": it.get("barcode", ""), "brand": it.get("brand", ""),
            "product_name": it.get("product_name", ""),
            "warehouse": it.get("warehouse", ""), "store": it.get("store", ""),
            "sys_available": sys_avail, "own_available": st.get("own_avail", 0),
            "b_available": st.get("b_avail", 0), "plat_available": st.get("plat_avail", 0),
            "sys_transit": sys_transit, "own_transit": st.get("own_transit", 0),
            "b_transit": st.get("b_transit", 0), "plat_transit": st.get("plat_transit", 0),
            "daily_sales": round(ds, 1), "daily_sales_7": it.get("daily_sales_7", ""),
            "daily_sales_14": it.get("daily_sales_14", ""), "daily_sales_28": it.get("daily_sales_28", ""),
            "daily_sales_60": it.get("daily_sales_60", ""),
            "purchase_qty": need, "actual_purchase": actual,
            "after_turnover": round(after, 1) if after is not None else None,
            "target_turnover": target_turn,
            "note": "建议采购 %d 件(覆盖 %d 天) | 已下单 %d 件" % (need, lead, actual) if need > 0 or actual > 0 else "库存充足, 无需采购",
        })
    if search:
        sq = search.lower()
        out = [x for x in out if sq in str(x.get("sku", "")).lower() or sq in str(x.get("product_name", "")).lower()]
    return ok({"suggestions": out})


# ── 滞销处置建议(disposal-suggestions) ──────────────────────────────────
@router.get("/insights/disposal-suggestions")
@traced
def disposal_suggestions(channel: str = "jd", page: int = 0, page_size: int = 0,
                         search: str = ""):
    """SKU×仓库粒度滞销识别 + 处置建议(disposed=已有处置记录)"""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    rows = query(
        "SELECT i.sku AS sku, MAX(i.product_name) AS product_name, MAX(i.warehouse) AS warehouse, "
        "MAX(i.warehouse_type) AS warehouse_type, MAX(p.brand) AS brand, "
        "SUM(i.available_qty) AS available_qty, "
        "MAX(IFNULL((SELECT MAX(s.date) FROM daily_sales_snapshot s WHERE s.sku=i.sku AND s.channel=i.channel), '')) AS last_sale "
        "FROM inventory i LEFT JOIN products p ON p.sku=i.sku AND p.channel=i.channel "
        "WHERE i.channel=%s GROUP BY i.sku, i.warehouse", [channel])
    disposed_rows = query("SELECT sku, warehouse FROM disposal_records WHERE channel=%s", [channel])
    disposed = set((str(r.get("sku")), str(r.get("warehouse"))) for r in disposed_rows)

    items = []
    for r in rows:
        avail = int(r.get("available_qty") or 0)
        if avail <= 0:
            continue
        last = str(r.get("last_sale") or "")[:10]
        days_zero = 999 if not last else max((now - datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days, 0)
        sku = r.get("sku")
        wh = str(r.get("warehouse") or "")
        if days_zero < 30:
            continue
        if days_zero >= 90:
            level, suggestion = "black", "尽快清仓/退供应商"
        elif days_zero >= 60:
            level, suggestion = "red", "降价促销/批量清仓"
        else:
            level, suggestion = "yellow", "促销去库/控制到货"
        reasons = ["%d天无销售" % (days_zero if days_zero < 999 else 999)]
        if avail > 0:
            reasons.append("库存 %d 件" % avail)
        items.append({
            "sku": sku, "product_name": r.get("product_name") or sku,
            "brand": r.get("brand") or "", "warehouse": wh,
            "warehouse_type": r.get("warehouse_type") or "",
            "days_zero": days_zero, "stock": avail, "level": level,
            "reason": reasons, "suggestion": suggestion,
            "disposed": (sku, wh) in disposed,
        })
    items.sort(key=lambda x: -x["days_zero"])
    if search:
        sq = search.lower()
        items = [x for x in items if sq in str(x.get("sku", "")).lower()
                 or sq in str(x.get("product_name", "")).lower()]
    if page > 0 and page_size > 0:
        total = len(items)
        return ok({"items": items[(page - 1) * page_size: page * page_size],
                   "total": total, "page": page, "page_size": page_size})
    return ok(items)


# ── 批量处置 ─────────────────────────────────────────────────────────────
@router.post("/disposals/batch")
@traced
async def disposals_batch(request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    channel = d.get("channel", "jd")
    action = d.get("action", "mark")
    note = d.get("note", "")
    items = d.get("items") or []
    n = 0
    for it in items:
        sku = it.get("sku")
        if not sku:
            continue
        execute("INSERT INTO disposal_records(sku, warehouse, warehouse_type, channel, level, turnover_days, reason, action, note) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (sku, it.get("warehouse", ""), it.get("warehouse_type", ""), channel,
                 it.get("level", ""), float(it.get("turnover_days") or 0),
                 it.get("reason") or "", action, note))
        n += 1
    return ok({"updated": n})
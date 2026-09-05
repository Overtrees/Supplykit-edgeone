"""原生 insights 路由(方案 B): 滞销识别 + 进销存 with-sales(契约与旧 backend 一致)"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from db import query, one
from routes.common import ok, traced
from biz.sales import load_daily_sales, calc_sales_multi

router = APIRouter(tags=["insights"])


@router.get("/insights/slow-moving")
@traced
def slow_moving(channel: str = "jd", page: int = 0, page_size: int = 0,
                search: str = "", days_threshold: int = 30):
    """滞销识别: 最后销售日距今 > threshold 且库存>0"""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    rows = query(
        "SELECT i.sku AS sku, MAX(i.product_name) AS product_name, MAX(i.warehouse_type) AS warehouse_type, "
        "SUM(i.available_qty) AS available_qty, SUM(i.safety_qty) AS safety_qty, "
        "MAX(IFNULL((SELECT MAX(s.date) FROM daily_sales_snapshot s WHERE s.sku=i.sku AND s.channel=i.channel), '')) AS last_sale "
        "FROM inventory i WHERE i.channel=%s GROUP BY i.sku", [channel])
    items = []
    for r in rows:
        avail = int(r.get("available_qty") or 0)
        if avail <= 0:
            continue
        last = str(r.get("last_sale") or "")[:10]
        days = 999 if not last else max((now - datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days, 0)
        if days <= days_threshold:
            continue
        items.append({
            "sku": r.get("sku"), "product_name": r.get("product_name") or r.get("sku"),
            "warehouse_type": r.get("warehouse_type") or "",
            "available_qty": avail, "safety_qty": int(r.get("safety_qty") or 0),
            "days_since_last": days, "last_sale": last,
        })
    items.sort(key=lambda x: -x["days_since_last"])
    if search:
        sq = search.lower()
        items = [i for i in items if sq in str(i.get("sku", "")).lower()
                 or sq in str(i.get("product_name", "")).lower()]
    if page > 0 and page_size > 0:
        total = len(items)
        return ok({"items": items[(page - 1) * page_size: page * page_size],
                   "total": total, "page": page, "page_size": page_size})
    return ok(items)


@router.get("/insights/with-sales")
@traced
def inventory_with_sales(wh_type: str = "own", channel: str = "jd", page: int = 0,
                         page_size: int = 0, search: str = ""):
    """进销存台账: 库存 + 日销 + 周转(wh_type=own/platform/platform_b)"""
    where = "channel=%s AND warehouse_type=%s"
    params = [channel, wh_type]
    if search:
        where += " AND (sku LIKE %s OR product_name LIKE %s)"
        params += ["%%%s%%" % search] * 2
    total = one("SELECT COUNT(*) AS c FROM inventory WHERE %s" % where, params) or {}
    total = int(total.get("c") or 0)
    if page > 0 and page_size > 0:
        rows = query("SELECT sku, warehouse, warehouse_type, available_qty, in_transit_qty, c_transit, "
                     "safety_qty, product_name FROM inventory WHERE %s ORDER BY id ASC LIMIT %s OFFSET %s"
                     % (where, page_size, (page - 1) * page_size), params)
    else:
        rows = query("SELECT sku, warehouse, warehouse_type, available_qty, in_transit_qty, c_transit, "
                     "safety_qty, product_name FROM inventory WHERE %s ORDER BY id ASC" % where, params)

    skus = [r.get("sku") for r in rows]
    daily = load_daily_sales(28, channel, skus=set(skus)) if skus else {}
    multi = calc_sales_multi(daily, windows=[7, 14, 28])
    items = []
    for r in rows:
        sku = r.get("sku")
        ds = multi[28].get(sku, 0) or multi[7].get(sku, 0)
        avail = int(r.get("available_qty") or 0)
        transit = int(r.get("in_transit_qty") or 0)
        items.append({
            "sku": sku, "product_name": r.get("product_name") or sku,
            "warehouse": r.get("warehouse", ""), "warehouse_type": r.get("warehouse_type", ""),
            "available_qty": avail, "in_transit_qty": transit, "c_transit": int(r.get("c_transit") or 0),
            "safety_qty": int(r.get("safety_qty") or 0),
            "daily_sales": round(ds, 1),
            "turnover_days": round((avail + transit) / ds, 1) if ds > 0 else None,
            "days_to_empty": round(avail / ds, 1) if ds > 0 else 999,
        })
    if page > 0 and page_size > 0:
        return ok({"items": items, "total": total, "page": page, "page_size": page_size})
    return ok(items)

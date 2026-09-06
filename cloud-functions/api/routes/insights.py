"""原生 insights 路由(方案 B): 滞销识别 + 进销存 with-sales(契约与旧 backend 一致)"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from db import query, one
from routes.common import ok, traced
from biz.sales import load_daily_sales, calc_sales_multi

router = APIRouter(tags=["insights"])


@router.get("/insights/ping")
@traced
def ping():
    """健康检查(免鉴权): 前端 checkApi/设置页连接状态用"""
    return {"ok": True}


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
    """进销存台账: 库存 + 日销 + 周转 + 当月进出(wh_type=own/platform/platform_b)"""
    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc)
    month_start = now.strftime("%Y-%m-01")
    month_end = now.strftime("%Y-%m-%d")
    where = "i.channel=%s AND i.warehouse_type=%s"
    params = [channel, wh_type]
    if search:
        where += " AND (i.sku LIKE %s OR i.product_name LIKE %s)"
        params += ["%%%s%%" % search] * 2
    total = one("SELECT COUNT(*) AS c FROM inventory i WHERE %s" % where, params) or {}
    total = int(total.get("c") or 0)
    sel = ("SELECT i.sku, i.warehouse, i.warehouse_type, i.available_qty, i.in_transit_qty, i.c_transit, "
           "i.safety_qty, i.product_name, i.month_inbound, i.month_outbound, i.beginning_stock, "
           "i.turnover_days, i.barcode, p.brand, p.price, "
           "(SELECT COUNT(*) FROM batches b WHERE b.sku=i.sku AND b.warehouse=i.warehouse AND b.channel=i.channel) AS batch_count "
           "FROM inventory i LEFT JOIN products p ON p.sku=i.sku AND p.channel=i.channel WHERE %s")
    if page > 0 and page_size > 0:
        rows = query((sel + " ORDER BY i.id ASC LIMIT %s OFFSET %s")
                     % (where, page_size, (page - 1) * page_size), params)
    else:
        rows = query((sel + " ORDER BY i.id ASC") % where, params)

    skus = [r.get("sku") for r in rows]
    daily = load_daily_sales(28, channel, skus=set(skus)) if skus else {}
    multi = calc_sales_multi(daily, windows=[7, 14, 28])
    # 当月进销: 记录表实时聚合(inbound_records/outbound_records), 回退 inventory 静态列
    month_in, month_out = {}, {}
    if skus:
        _ph = ",".join(["%s"] * len(skus))
        for _r in query("SELECT sku, warehouse, SUM(quantity) AS q FROM inbound_records "
                        "WHERE channel=%s AND inbound_date>=%s AND sku IN (" + _ph + ") "
                        "GROUP BY sku, warehouse",
                        [channel, month_start] + skus):
            month_in[(_r.get("sku"), _r.get("warehouse") or "")] = int(_r.get("q") or 0)
        for _r in query("SELECT sku, warehouse, SUM(quantity) AS q FROM outbound_records "
                        "WHERE channel=%s AND outbound_date>=%s AND sku IN (" + _ph + ") "
                        "GROUP BY sku, warehouse",
                        [channel, month_start] + skus):
            month_out[(_r.get("sku"), _r.get("warehouse") or "")] = int(_r.get("q") or 0)
    items = []
    for r in rows:
        sku = r.get("sku")
        ds = multi[28].get(sku, 0) or multi[7].get(sku, 0)
        avail = int(r.get("available_qty") or 0)
        transit = int(r.get("in_transit_qty") or 0)
        _k = (sku, r.get("warehouse") or "")
        mi = month_in.get(_k)
        mo = month_out.get(_k)
        month_inbound = mi if mi is not None else int(r.get("month_inbound") or 0)
        month_outbound = mo if mo is not None else int(r.get("month_outbound") or 0)
        items.append({
            "sku": sku, "product_name": r.get("product_name") or sku,
            "warehouse": r.get("warehouse", ""), "warehouse_type": r.get("warehouse_type", ""),
            "available_qty": avail, "in_transit_qty": transit, "c_transit": int(r.get("c_transit") or 0),
            "safety_qty": int(r.get("safety_qty") or 0),
            "daily_sales": round(ds, 1),
            "turnover_days": round((avail + transit) / ds, 1) if ds > 0 else None,
            "days_to_empty": round(avail / ds, 1) if ds > 0 else 999,
            "month_start": month_start, "month_end": month_end,
            "month_inbound": month_inbound,
            "month_outbound": month_outbound,
            "beginning_stock": max(avail - month_inbound + month_outbound, 0),
            "barcode": r.get("barcode") or "", "brand": r.get("brand") or "",
            "price": r.get("price") or 0, "batch_count": int(r.get("batch_count") or 0),
        })
    if page > 0 and page_size > 0:
        return ok({"items": items, "total": total, "page": page, "page_size": page_size,
                   "month_start": month_start, "month_end": month_end})
    return ok(items)

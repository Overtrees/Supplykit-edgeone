"""原生 dashboard 路由: summary(契约与旧 backend 一致)"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from db import query, one
from routes.common import ok, fail, PAID_STATUSES

router = APIRouter(tags=["dashboard"])

_PAID = tuple(PAID_STATUSES)


def _status_cond(col="order_status"):
    """GMV 已支付口径条件"""
    return "%s IN (%s)" % (col, ",".join(["'%s'" % s for s in _PAID]))


@router.get("/dashboard/summary")
def dashboard_summary(channel: str = "jd", start_date: str = "", end_date: str = ""):
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    if start_date and end_date:
        rows = query(
            "SELECT DATE(ordered_at) AS d, order_status, store, "
            "SUM(IF(%s, total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0), 0)) AS g, "
            "SUM(IF(%s, COALESCE(subsidy_amount,0), 0)) AS sub, COUNT(*) AS cnt "
            "FROM orders WHERE channel=%%s AND (deleted_at IS NULL OR deleted_at='') "
            "AND ordered_at >= %%s AND ordered_at < %%s "
            "GROUP BY DATE(ordered_at), order_status, store" % (_status_cond(), _status_cond()),
            (channel, start_date + " 00:00:00", (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") + " 00:00:00"))
        return ok(_assemble(rows, channel, start_date, end_date))

    rows = query(
        "SELECT DATE(ordered_at) AS d, order_status, store, "
        "SUM(IF(%s, total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0), 0)) AS g, "
        "SUM(IF(%s, COALESCE(subsidy_amount,0), 0)) AS sub, COUNT(*) AS cnt "
        "FROM orders WHERE channel=%%s AND (deleted_at IS NULL OR deleted_at='') AND ordered_at >= %%s "
        "GROUP BY DATE(ordered_at), order_status, store" % (_status_cond(), _status_cond()),
        (channel, (now - timedelta(days=59)).strftime("%Y-%m-%d") + " 00:00:00"))
    return ok(_assemble(rows, channel, (now - timedelta(days=29)).strftime("%Y-%m-%d"), today))


def _assemble(rows, channel, start_date, end_date):
    gmv = pending = refund = refund_amt = subsidy = total_orders = paid_orders = 0
    trend = {}
    store_gmv = {}
    store_refund = {}
    store_subsidy = {}
    funnel = {}
    day_rows = {}
    for r in rows:
        d = r.get("d") or ""
        st = r.get("order_status") or "未知"
        store = r.get("store") or ""
        g = float(r.get("g") or 0)
        sub = float(r.get("sub") or 0)
        cnt = int(r.get("cnt") or 0)
        day_rows[(d, st, store)] = (g, sub, cnt)
        total_orders += cnt
        if st in _PAID:
            gmv += g
            subsidy += sub
            paid_orders += cnt
            store_gmv[store] = store_gmv.get(store, 0) + g
            store_subsidy[store] = store_subsidy.get(store, 0) + sub
            if st == "待发货":
                pending += cnt
            elif st == "申请退款":
                refund += cnt
                refund_amt += g
                store_refund[store] = store_refund.get(store, 0) + g
        t = trend.setdefault(d, {"GMV": 0, "订单数": 0})
        if st in _PAID:
            t["订单数"] += cnt
            t["GMV"] += g
        funnel[st] = funnel.get(st, 0) + cnt

    trend_data = [{"日期": k, "GMV": v["GMV"], "订单数": v["订单数"]} for k, v in sorted(trend.items())]
    stores = [{"name": k, "gmv": round(v, 2),
               "refund_amount": round(store_refund.get(k, 0), 2),
               "subsidy_amount": round(store_subsidy.get(k, 0), 2),
               "net_gmv": round(v - store_refund.get(k, 0), 2),
               "payout": round(v - store_refund.get(k, 0) - store_subsidy.get(k, 0), 2)}
              for k, v in sorted(store_gmv.items(), key=lambda x: -x[1])]

    ftotal = total_orders
    stages = [("总订单", ftotal, 100.0)]
    for n in ["待确认", "待发货", "已发货", "已完成"]:
        v = funnel.get(n, 0)
        stages.append((n, v, round(v / ftotal * 100, 1) if ftotal else 0))
    funnel_res = []
    for i, (n, v, pct) in enumerate(stages):
        prev = stages[i - 1][1] if i > 0 else ftotal
        funnel_res.append({"name": n, "value": v, "percentage": pct,
                           "conversion": round(min(v / prev * 100, 100), 1) if prev else 0})

    now = datetime.now(timezone.utc)
    today_s = now.strftime("%Y-%m-%d")
    d1 = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    d7 = (now - timedelta(days=6)).strftime("%Y-%m-%d")
    d8 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    d14 = (now - timedelta(days=13)).strftime("%Y-%m-%d")
    d30 = (now - timedelta(days=29)).strftime("%Y-%m-%d")
    d31 = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    d60 = (now - timedelta(days=59)).strftime("%Y-%m-%d")

    def _agg(d0, d1):
        g = o = 0
        for (d, st, s2), (gv, sb, cn) in day_rows.items():
            if d0 <= (d or "") <= d1 and st in _PAID:
                g += gv
                o += cn
        return round(g, 2), o

    def _refund(d0, d1):
        r = 0.0
        for (d, st, s2), (gv, sb, cn) in day_rows.items():
            if d0 <= (d or "") <= d1 and st == "申请退款":
                r += gv
        return r

    def _sub(d0, d1):
        s = 0.0
        for (d, st, s2), (gv, sb, cn) in day_rows.items():
            if d0 <= (d or "") <= d1 and st in _PAID:
                s += sb
        return s

    tg, to = _agg(today_s, today_s)
    pg, po = _agg(d1, d1)
    wg, wo = _agg(d7, today_s)
    pwg, pwo = _agg(d8, d14)
    mg, mo = _agg(d30, today_s)
    pmg, pmo = _agg(d31, d60)

    periods = {
        "today": {"gmv": tg, "orders": to, "days": 1, "prev_gmv": pg, "prev_orders": po,
                  "net_gmv": round(tg - _refund(today_s, today_s), 2), "subsidy_amount": round(_sub(today_s, today_s), 2)},
        "week": {"gmv": wg, "orders": wo, "days": 7, "prev_gmv": pwg, "prev_orders": pwo,
                 "net_gmv": round(wg - _refund(d7, today_s), 2), "subsidy_amount": round(_sub(d7, today_s), 2)},
        "month": {"gmv": mg, "orders": mo, "days": 30, "prev_gmv": pmg, "prev_orders": pmo,
                  "net_gmv": round(mg - _refund(d30, today_s), 2), "subsidy_amount": round(_sub(d30, today_s), 2)},
    }

    health = _health_index(channel)
    low_stock = one("SELECT COUNT(*) AS c FROM inventory WHERE channel=%s AND available_qty < safety_qty", [channel]) or {}
    alert_count = one("SELECT COUNT(*) AS c FROM alerts WHERE channel=%s AND status='active'", [channel]) or {}
    product_count = one("SELECT COUNT(*) AS c FROM products WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='')", [channel]) or {}
    supplier_count = one("SELECT COUNT(*) AS c FROM suppliers") or {}

    summary = {
        "gmv": round(gmv, 2), "net_gmv": round(gmv - refund_amt, 2),
        "refund_amount": round(refund_amt, 2), "subsidy_amount": round(subsidy, 2),
        "payout": round(gmv - refund_amt - subsidy, 2),
        "total_orders": total_orders, "pending_count": pending, "refund_count": refund,
        "low_stock_count": int(low_stock.get("c") or 0), "active_alerts": int(alert_count.get("c") or 0),
        "total_products": int(product_count.get("c") or 0), "total_suppliers": int(supplier_count.get("c") or 0),
    }
    return {"summary": summary, "periods": periods, "trend": trend_data,
            "funnel": funnel_res, "health_index": health, "stores": stores}


def _health_index(channel):
    hw = {}
    rows = query(
        "SELECT warehouse_type, "
        "SUM(IF(available_qty >= safety_qty, 1, 0)) AS healthy, "
        "SUM(IF(available_qty > 0 AND available_qty < safety_qty, 1, 0)) AS warning, "
        "SUM(IF(available_qty = 0, 1, 0)) AS out_of_stock, COUNT(*) AS total "
        "FROM inventory WHERE channel=%s GROUP BY warehouse_type", [channel])
    for r in rows:
        hw[r.get("warehouse_type") or ""] = r
    bc = one(
        "SELECT SUM(IF(avail >= safety, 1, 0)) AS healthy, "
        "SUM(IF(avail > 0 AND avail < safety, 1, 0)) AS warning, "
        "SUM(IF(avail = 0, 1, 0)) AS out_of_stock, COUNT(*) AS total "
        "FROM (SELECT sku, SUM(available_qty) AS avail, SUM(safety_qty) AS safety "
        "FROM inventory WHERE channel=%s AND warehouse_type IN ('platform','platform_b') GROUP BY sku) t",
        [channel]) or {}

    def _score(r):
        total = int(r.get("total") or 0)
        healthy = int(r.get("healthy") or 0)
        score = round(healthy / total * 100, 0) if total else 100
        return {"score": score, "healthy": healthy, "warning": int(r.get("warning") or 0),
                "out_of_stock": int(r.get("out_of_stock") or 0), "total": total,
                "level": "good" if score >= 85 else ("warning" if score >= 60 else "danger")}

    z = {"healthy": 0, "warning": 0, "out_of_stock": 0, "total": 0}
    all_rows = {"healthy": sum(int(hw.get(k, z).get("healthy") or 0) for k in hw),
                "warning": sum(int(hw.get(k, z).get("warning") or 0) for k in hw),
                "out_of_stock": sum(int(hw.get(k, z).get("out_of_stock") or 0) for k in hw),
                "total": sum(int(hw.get(k, z).get("total") or 0) for k in hw)}
    return {"own": _score(hw.get("own", z)), "platform": _score(hw.get("platform", z)),
            "platform_b": _score(hw.get("platform_b", z)), "bc": _score(bc),
            "score": _score(all_rows)["score"], "level": _score(all_rows)["level"]}

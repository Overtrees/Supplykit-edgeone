"""原生 dashboard 路由: summary(契约与旧 backend 一致)"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from db import query, one
from routes.common import ok, fail, PAID_STATUSES, traced

router = APIRouter(tags=["dashboard"])

_PAID = tuple(PAID_STATUSES)


def _status_cond(col="order_status"):
    """GMV 已支付口径条件"""
    return "%s IN (%s)" % (col, ",".join(["'%s'" % s for s in _PAID]))


@router.get("/dashboard/summary")
@traced
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
        d = str(r.get("d") or "")[:10]  # TiDB DATE() → datetime.date, 统一转 str
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
    pwg, pwo = _agg(d14, d8)
    mg, mo = _agg(d30, today_s)
    pmg, pmo = _agg(d60, d31)

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


@router.get("/dashboard/aux")
@traced
def dashboard_aux(channel: str = "jd"):
    """看板辅助聚合: alerts(分组配额) + alertCounts + stockOverview + bcOutOfStock"""
    from routes.alerts import _FIELDS as _AF
    # alerts 分组配额
    alerts = []
    for atype in ("low_stock", "replenish", None):
        if atype:
            rows = query("SELECT %s FROM alerts WHERE channel=%%s AND status='active' AND alert_type=%%s "
                         "ORDER BY id DESC LIMIT 100" % _AF, [channel, atype])
        else:
            rows = query("SELECT %s FROM alerts WHERE channel=%%s AND status='active' "
                         "AND alert_type NOT IN ('low_stock','replenish') ORDER BY id DESC LIMIT 100" % _AF, [channel])
        alerts.extend(rows)
    counts = query("SELECT alert_type, severity, "
                   "IFNULL(NULLIF(warehouse_type,''),'') AS wt, COUNT(*) AS c "
                   "FROM alerts WHERE channel=%s AND status='active' "
                   "GROUP BY alert_type, severity, wt", [channel])
    by_type, by_sev, by_wh, total = {}, {}, {}, 0
    by_wh_ls, by_wh_slow, by_wh_rp = {}, {}, {}
    for r in counts:
        at = r.get("alert_type") or "other"
        sev = r.get("severity") or "info"
        wt = r.get("wt") or ""
        c = int(r.get("c") or 0)
        total += c
        by_type[at] = by_type.get(at, 0) + c
        by_sev[sev] = by_sev.get(sev, 0) + c
        by_wh[wt] = by_wh.get(wt, 0) + c
        tgt = by_wh_rp if at == "replenish" else (by_wh_slow if at == "slow_moving" else by_wh_ls)
        tgt[wt] = tgt.get(wt, 0) + c

    def _wmap(m):
        b = m.get("platform_b", 0)
        c = m.get("platform", 0)
        o = m.get("own", 0)
        return {"b": b, "c": c, "own": o, "bc": b + c, "unknown": m.get("", 0)}

    _rp = by_type.get("replenish", 0)
    alert_counts_full = {"total": total, "by_type": by_type, "by_severity": by_sev,
                         "replenish": _rp, "non_replenish": total - _rp,
                         "by_warehouse": _wmap(by_wh), "ls_warehouse": _wmap(by_wh_ls),
                         "slow_warehouse": _wmap(by_wh_slow), "rp_warehouse": _wmap(by_wh_rp)}
    # stockOverview(缺货/低库存)
    out = one("SELECT COUNT(*) AS c FROM inventory WHERE channel=%s AND available_qty=0", [channel]) or {}
    low = one("SELECT COUNT(*) AS c FROM inventory WHERE channel=%s AND available_qty>0 AND available_qty<safety_qty", [channel]) or {}
    so_items = query("SELECT sku, product_name, warehouse, warehouse_type, available_qty, safety_qty "
                     "FROM inventory WHERE channel=%s AND available_qty=0 ORDER BY id DESC LIMIT 100", [channel])
    # bc 合计缺货 SKU
    bc_rows = query(
        "SELECT sku, MAX(product_name) AS product_name FROM inventory "
        "WHERE channel=%s AND warehouse_type IN ('platform','platform_b') "
        "GROUP BY sku HAVING SUM(available_qty) <= 0 ORDER BY sku LIMIT 100", [channel])
    bc_out = [{"sku": r.get("sku"), "product_name": r.get("product_name") or r.get("sku"),
               "warehouse_type": "bc"} for r in bc_rows]
    return ok({
        "alerts": alerts,
        "alertCounts": alert_counts_full,
        "stockOverview": {"items": so_items,
                          "out_of_stock_count": int(out.get("c") or 0),
                          "low_stock_count": int(low.get("c") or 0),
                          "total": int(out.get("c") or 0) + int(low.get("c") or 0)},
        "bcOutOfStock": bc_out,
        "stockRisk": _stock_risk(channel),
    })


@router.get("/dashboard/stock-risk")
@traced
def stock_risk(channel: str = "jd", full: int = 0):
    return ok(_stock_risk(channel, full=full))


def _stock_risk(channel, full: int = 0):
    """濒临断货 TOP: B(BBCC)/C(传统)/BC/own 维度"""
    from biz.sales import load_daily_sales_grouped, calc_sales_multi, rolling_predict
    from db import query as _q
    cfg_rows = _q("SELECT `key`, value FROM replenishment_config WHERE channel=%s", [channel])
    cfg = {r.get("key"): r.get("value") for r in cfg_rows}
    b_to_c = int(cfg.get("b_to_c_days", "3") or 3)
    c_safety = int(cfg.get("c_safety_days", "0") or 0)
    lead = b_to_c + c_safety

    inv = _q("SELECT sku, warehouse_type, warehouse, available_qty, in_transit_qty, safety_qty, product_name "
             "FROM inventory WHERE channel=%s", [channel])
    prods = _q("SELECT sku, barcode, product_name FROM products WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='')", [channel])
    pmap = {r.get("sku"): r for r in prods}
    skus = set([r.get("sku") for r in inv]) | set(pmap.keys())

    by_sku, by_sku_wh = load_daily_sales_grouped(28, channel, skus=skus)
    multi = calc_sales_multi(by_sku, windows=[7, 14, 28])
    fused = {s: rolling_predict(multi[7].get(s, 0), multi[14].get(s, 0), multi[28].get(s, 0)) for s in skus}
    # 全国 C 仓日销(BBCC)
    c_whs = {r.get("warehouse") for r in _q("SELECT DISTINCT warehouse FROM inventory WHERE channel=%s AND warehouse_type='platform' AND warehouse!=''", [channel])}
    daily_c = {}
    for wk, wd in by_sku_wh.items():
        base, wh = wk.rsplit("|", 1)
        if wh in c_whs:
            m = daily_c.setdefault(base, {})
            for d, q in wd.items():
                m[d] = m.get(d, 0) + q
    cmulti = calc_sales_multi(daily_c, windows=[7, 14, 28]) if daily_c else {7: {}, 14: {}, 28: {}}
    fused_c = {s: rolling_predict(cmulti[7].get(s, 0), cmulti[14].get(s, 0), cmulti[28].get(s, 0)) for s in daily_c}

    c_stock, b_stock, bc_total, own_stock = {}, {}, {}, {}
    for r in inv:
        sku = r.get("sku")
        wt = r.get("warehouse_type")
        qty = int(r.get("available_qty") or 0)
        safety = int(r.get("safety_qty") or 0)
        tty = int(r.get("in_transit_qty") or 0)
        if wt == "own":
            own_stock[sku] = {"available": own_stock.get(sku, {}).get("available", 0) + qty,
                              "safety": own_stock.get(sku, {}).get("safety", 0) + safety}
        elif wt == "platform_b":
            b_stock[sku] = {"available": b_stock.get(sku, {}).get("available", 0) + qty,
                            "safety": b_stock.get(sku, {}).get("safety", 0) + safety}
        elif wt == "platform":
            c_stock[sku] = {"available": c_stock.get(sku, {}).get("available", 0) + qty,
                            "safety": c_stock.get(sku, {}).get("safety", 0) + safety}
        if wt in ("platform", "platform_b"):
            bc_total[sku] = {"available": bc_total.get(sku, {}).get("available", 0) + qty,
                             "safety": bc_total.get(sku, {}).get("safety", 0) + safety,
                             "transit": bc_total.get(sku, {}).get("transit", 0) + tty}

    result = []
    # B 维度(BBCC)
    for sku, st in b_stock.items():
        b_avail = st["available"]
        if b_avail <= 0:
            continue
        ds = fused_c.get(sku, 0)
        if ds <= 0:
            continue
        c_avail = c_stock.get(sku, {}).get("available", 0)
        c_gap = max(round(ds * lead - c_avail, 0), 0)
        if c_gap <= 0:
            continue
        result.append({"sku": sku, "barcode": (pmap.get(sku) or {}).get("barcode", ""),
                       "product_name": (pmap.get(sku) or {}).get("product_name", st.get("pname", sku)),
                       "warehouse": "B仓", "type": "B", "available_qty": b_avail,
                       "daily_sales": round(ds, 1),
                       "days_to_empty": round(b_avail / (c_gap / lead), 1) if c_gap > 0 else 999,
                       "c_gap": c_gap, "c_avail": c_avail})
    # C 维度(传统)
    c_items = []
    for sku, st in c_stock.items():
        avail = st["available"]
        safety = st["safety"]
        ds = fused_c.get(sku, 0) or fused.get(sku, 0)
        if avail <= 0 or avail >= safety or ds <= 0:
            continue
        c_items.append({"sku": sku, "barcode": (pmap.get(sku) or {}).get("barcode", ""),
                        "product_name": (pmap.get(sku) or {}).get("product_name", sku),
                        "warehouse": "C仓", "type": "C", "available_qty": avail,
                        "daily_sales": round(ds, 1), "days_to_empty": round(avail / ds, 1)})
    # BC 合计
    bc_items = []
    for sku, st in bc_total.items():
        avail = st["available"]
        safety = st["safety"]
        ds = fused_c.get(sku, 0)
        if ds <= 0:
            continue
        if avail <= 0 or avail < safety:
            bc_items.append({"sku": sku, "barcode": (pmap.get(sku) or {}).get("barcode", ""),
                             "product_name": (pmap.get(sku) or {}).get("product_name", sku),
                             "warehouse": "BC", "type": "BC", "available_qty": avail,
                             "daily_sales": round(ds, 1), "days_to_empty": round(avail / ds, 1),
                             "b_avail": b_stock.get(sku, {}).get("available", 0),
                             "c_avail": c_stock.get(sku, {}).get("available", 0)})
    # own 维度
    own_items = []
    for sku, st in own_stock.items():
        avail = st["available"]
        safety = st["safety"]
        ds = fused.get(sku, 0)
        if ds <= 0:
            continue
        if avail <= 0 or (safety > 0 and avail < safety):
            own_items.append({"sku": sku, "barcode": (pmap.get(sku) or {}).get("barcode", ""),
                              "product_name": (pmap.get(sku) or {}).get("product_name", sku),
                              "warehouse": "自有", "type": "OWN", "available_qty": avail,
                              "daily_sales": round(ds, 1), "days_to_empty": round(avail / ds, 1)})

    def _stats(items):
        crit = sum(1 for i in items if i.get("days_to_empty", 999) < 3)
        warn = sum(1 for i in items if 3 <= i.get("days_to_empty", 999) < 7)
        return len(items), crit, warn

    result.sort(key=lambda x: x["days_to_empty"])
    bc_items.sort(key=lambda x: x["days_to_empty"])
    c_items.sort(key=lambda x: x["days_to_empty"])
    own_items.sort(key=lambda x: x["days_to_empty"])
    t, c, w = _stats(result)
    bt, bc, bw = _stats(bc_items)
    ct, cc, cw = _stats(c_items)
    ot, oc, ow = _stats(own_items)
    payload = {"items": result[:10], "total": t, "critical": c, "warning": w,
               "bcItems": bc_items[:10], "bcTotal": bt, "bcCritical": bc, "bcWarning": bw,
               "cItems": c_items[:10], "cTotal": ct, "cCritical": cc, "cWarning": cw,
               "ownItems": own_items[:10], "ownTotal": ot, "ownCritical": oc, "ownWarning": ow}
    return payload

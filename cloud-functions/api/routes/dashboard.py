"""原生 dashboard 路由: summary(契约与旧 backend 一致)"""
from datetime import datetime, timedelta, timezone
import time as _time

from fastapi import APIRouter

from db import query, one
from routes.common import ok, PAID_STATUSES, SALES_STATUSES, traced

router = APIRouter(tags=["dashboard"])

from routes.analysis_cache import register as _register_cache, cache_get as _cache_get
_register_cache(lambda: (_summary_cache.clear(), _aux_cache.clear(), _risk_cache.clear(), _accel_cache.clear()))

_PAID = tuple(PAID_STATUSES)


def _status_cond(col="order_status"):
    """GMV 已支付口径条件"""
    return "%s IN (%s)" % (col, ",".join(["'%s'" % s for s in _PAID]))


_summary_cache = {}
_SUMMARY_TTL = 30



@router.get("/dashboard/health-trend")
@traced
def health_trend(channel: str = "jd", days: int = 14):
    """健康分数趋势(近 N 天每日 score/own/platform/bc, 旧→新; 数据源 health_snapshot)
    主动兜底: 今天无记录时立即计算健康分入库(不依赖 summary upsert 的实例路径), 保证趋势持续积累"""
    try:
        rows = query("SELECT `date`, score, own_score, platform_score, bc_score "
                     "FROM health_snapshot WHERE channel=%s ORDER BY `date` DESC LIMIT %s",
                     [channel, min(int(days), 60)])
        if not rows or str(rows[0].get("date") or "")[:10] != datetime.now(timezone.utc).strftime("%Y-%m-%d"):
            _h = _health_index(channel)
            _sc = _h.get("score")
            execute("INSERT INTO health_snapshot(`date`, channel, score, own_score, platform_score, bc_score) "
                    "VALUES(%s,%s,%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE score=VALUES(score), own_score=VALUES(own_score), "
                    "platform_score=VALUES(platform_score), bc_score=VALUES(bc_score)",
                    [datetime.now(timezone.utc).strftime("%Y-%m-%d"), channel,
                     int(_sc) if _sc is not None else -1,
                     int((_h.get("own") or {}).get("score") or -1),
                     int((_h.get("platform") or {}).get("score") or -1),
                     int((_h.get("bc") or {}).get("score") or -1)])
            rows = query("SELECT `date`, score, own_score, platform_score, bc_score "
                         "FROM health_snapshot WHERE channel=%s ORDER BY `date` DESC LIMIT %s",
                         [channel, min(int(days), 60)])
    except Exception:
        pass
    _out = []
    for r in reversed(rows or []):
        _d = str(r.get("date") or "")[:10]
        if not _d or str(r.get("score") or "") in ("-1", ""):
            continue
        _out.append({"date": _d, "score": int(r.get("score") or 0),
                     "own": int(r.get("own_score") or -1), "platform": int(r.get("platform_score") or -1),
                     "bc": int(r.get("bc_score") or -1)})
    return ok(_out)


@router.get("/dashboard/summary")
@traced
def dashboard_summary(channel: str = "jd", start_date: str = "", end_date: str = ""):
    """看板汇总(30s 共享表缓存——TiDB 表跨实例一致; 写操作 invalidate_all 全局失效, 数据变化最多 30s 可见)"""
    _key = "dash_summary|%s|%s|%s" % (channel, start_date, end_date)
    _result = _cache_get(_key, _SUMMARY_TTL,
                         lambda: _build_summary(channel, start_date, end_date))
    return ok(_result)


def _build_summary(channel, start_date, end_date):
    """summary 计算体(共享缓存 builder; 原函数体迁移)"""
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
        return _assemble(rows, channel, start_date, end_date)

    rows = query(
        "SELECT DATE(ordered_at) AS d, order_status, store, "
        "SUM(IF(%s, total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0), 0)) AS g, "
        "SUM(IF(%s, COALESCE(subsidy_amount,0), 0)) AS sub, COUNT(*) AS cnt "
        "FROM orders WHERE channel=%%s AND (deleted_at IS NULL OR deleted_at='') AND ordered_at >= %%s "
        "GROUP BY DATE(ordered_at), order_status, store" % (_status_cond(), _status_cond()),
        (channel, (now - timedelta(days=59)).strftime("%Y-%m-%d") + " 00:00:00"))
    return _assemble(rows, channel, (now - timedelta(days=29)).strftime("%Y-%m-%d"), today)


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
    # 周期趋势(前端 gmv 小卡图表按今日/本周/本月联动)
    def _trend_range(d0, d1):
        return [{"日期": x.get("日期"), "GMV": x.get("GMV"), "订单数": x.get("订单数")}
                for x in trend_data if d0 <= str(x.get("日期") or "")[:10] <= d1]

    # 周期店铺(前端店铺 GMV 卡周期联动)
    def _stores_range(d0, d1):
        sg = {}
        for (d, st, s2), (gv, sb, cn) in day_rows.items():
            if d0 <= (d or "") <= d1 and st in _PAID:
                sg[s2] = sg.get(s2, 0) + gv
        return [{"name": k, "gmv": round(v, 2),
                 "net_gmv": round(v - store_refund.get(k, 0), 2),
                 "payout": round(v - store_refund.get(k, 0) - store_subsidy.get(k, 0), 2)}
                for k, v in sorted(sg.items(), key=lambda x: -x[1])]

    periods["today_trend"] = _trend_range(today_s, today_s)
    periods["week_trend"] = _trend_range(d7, today_s)
    periods["month_trend"] = _trend_range(d30, today_s)

    # 品牌维度(店铺 GMV 卡切品牌: 近 60 天 paid 订单按品牌聚合, join products)
    brands_map = {}
    try:
        _br = query(
            "SELECT COALESCE(p.brand,'') AS brand, "
            "SUM(IF(o.order_status IN (%s), o.total_amount - COALESCE(o.discount_amount,0) "
            "+ COALESCE(o.freight_amount,0) + COALESCE(o.tax_amount,0), 0)) AS g "
            "FROM orders o LEFT JOIN products p ON o.sku=p.sku AND o.channel=p.channel "
            "WHERE o.channel=%%s AND (o.deleted_at IS NULL OR o.deleted_at='') "
            "AND o.ordered_at>=%%s GROUP BY p.brand" % _status_cond(),
            [channel, d60 + " 00:00:00"])
        for _r in _br:
            _b = _r.get("brand") or "未分类"
            if not _b or _b == "未分类":
                continue
            brands_map[_b] = round(float(_r.get("g") or 0), 2)
    except Exception:
        pass
    brands = [{"name": k, "gmv": v, "net_gmv": v, "payout": v}
              for k, v in sorted(brands_map.items(), key=lambda x: -x[1])]
    def _brands_range(d0):
        """按起始日聚合品牌 GMV(周期联动)"""
        try:
            _bm = {}
            for _r2 in query(
                    "SELECT COALESCE(p.brand,'') AS brand, "
                    "SUM(IF(o.order_status IN (%s), o.total_amount - COALESCE(o.discount_amount,0) "
                    "+ COALESCE(o.freight_amount,0) + COALESCE(o.tax_amount,0), 0)) AS g "
                    "FROM orders o LEFT JOIN products p ON o.sku=p.sku AND o.channel=p.channel "
                    "WHERE o.channel=%%s AND (o.deleted_at IS NULL OR o.deleted_at='') "
                    "AND o.ordered_at>=%%s GROUP BY p.brand" % _status_cond(),
                    [channel, d0 + " 00:00:00"]):
                _b = _r2.get("brand") or ""
                if not _b or _b == "未分类":
                    continue
                _bm[_b] = round(float(_r2.get("g") or 0), 2)
            return [{"name": k, "gmv": v, "net_gmv": v, "payout": v}
                    for k, v in sorted(_bm.items(), key=lambda x: -x[1])]
        except Exception:
            return []
    period_brands = {"today": _brands_range(today_s),
                     "week": _brands_range(d7),
                     "month": _brands_range(d30)}
    period_stores = {"today": _stores_range(today_s, today_s),
                     "week": _stores_range(d7, today_s),
                     "month": _stores_range(d30, today_s)}
    if start_date and end_date:
        period_stores["custom"] = _stores_range(start_date, end_date)
        period_brands["custom"] = _brands_range(start_date)
    def _funnel_range(d0, d1):
        _ft = {}
        for (d, st, s2), (gv, sb, cn) in day_rows.items():
            if d0 <= (d or "") <= d1:
                _ft[st] = _ft.get(st, 0) + cn
        _total = sum(_ft.values())
        _stages = [("总订单", _total, 100.0)]
        for n in ["待确认", "待发货", "已发货", "已完成"]:
            v = _ft.get(n, 0)
            _stages.append((n, v, round(v / _total * 100, 1) if _total else 0))
        _out = []
        for i, (n, v, pct) in enumerate(_stages):
            prev = _stages[i - 1][1] if i > 0 else _total
            _out.append({"name": n, "value": v, "percentage": pct,
                         "conversion": round(min(v / prev * 100, 100), 1) if prev else 0})
        return _out
    period_funnel = {"today": _funnel_range(today_s, today_s),
                     "week": _funnel_range(d7, today_s),
                     "month": _funnel_range(d30, today_s)}
    if start_date and end_date:
        period_funnel["custom"] = _funnel_range(start_date, end_date)

    # 健康分快照(趋势数据源): 每日 upsert(首次 summary 请求记录当天分, 历史积累)
    try:
        _hs = health.get("score")
        execute("INSERT INTO health_snapshot(`date`, channel, score, own_score, platform_score, bc_score) "
                "VALUES(%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE score=VALUES(score), own_score=VALUES(own_score), "
                "platform_score=VALUES(platform_score), bc_score=VALUES(bc_score)",
                [today_s, channel, int(_hs) if _hs is not None else -1,
                 int((health.get("own") or {}).get("score") or -1),
                 int((health.get("platform") or {}).get("score") or -1),
                 int((health.get("bc") or {}).get("score") or -1)])
    except Exception as _hsnap:
        try:
            execute("INSERT INTO quality_logs(log_type, level, message, details, source) "
                    "VALUES('health_snap','error',%s,%s,'dash')",
                    ("health_snapshot upsert 失败", str(_hsnap)[:200]))
        except Exception:
            pass

    return {"summary": summary, "periods": periods, "trend": trend_data,
            "funnel": funnel_res, "period_funnel": period_funnel, "health_index": health, "stores": stores,
            "brands": brands, "period_stores": period_stores, "period_brands": period_brands}


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

    # 健康分档参数: 内置'库存健康监控'规则 params 配置(默认 85/60 与硬编码一致)
    _hrp = _rule_params("health", channel)
    try:
        _hg = float(_hrp.get("health_good", 85))
        _hw = float(_hrp.get("health_warning", 60))
    except Exception:
        _hg, _hw = 85.0, 60.0

    def _score(r):
        total = int(r.get("total") or 0)
        healthy = int(r.get("healthy") or 0)
        # 空维度(total=0, 如 other 渠道无 B 仓) → score=None/level=empty, 前端显示"—"而非 100 分误导
        if total == 0:
            return {"score": None, "healthy": 0, "warning": 0, "out_of_stock": 0, "total": 0,
                    "level": "empty"}
        score = round(healthy / total * 100, 0)
        return {"score": score, "healthy": healthy, "warning": int(r.get("warning") or 0),
                "out_of_stock": int(r.get("out_of_stock") or 0), "total": total,
                "level": "good" if score >= _hg else ("warning" if score >= _hw else "danger")}

    z = {"healthy": 0, "warning": 0, "out_of_stock": 0, "total": 0}
    all_rows = {"healthy": sum(int(hw.get(k, z).get("healthy") or 0) for k in hw),
                "warning": sum(int(hw.get(k, z).get("warning") or 0) for k in hw),
                "out_of_stock": sum(int(hw.get(k, z).get("out_of_stock") or 0) for k in hw),
                "total": sum(int(hw.get(k, z).get("total") or 0) for k in hw)}
    return {"own": _score(hw.get("own", z)), "platform": _score(hw.get("platform", z)),
            "platform_b": _score(hw.get("platform_b", z)), "bc": _score(bc),
            "score": _score(all_rows)["score"], "level": _score(all_rows)["level"]}


_aux_cache = {}
_AUX_TTL = 60
_risk_cache = {}
_RISK_TTL = 30


@router.get("/dashboard/aux")
@traced
def dashboard_aux(channel: str = "jd", mode: str = "bbcc"):
    """看板辅助聚合(60s 共享表缓存——TiDB 表跨实例一致): alerts(分组配额, 低库存组按补货模式过滤维度) + alertCounts
    + stockOverview + bcOutOfStock + stockRisk

    模式跟随: bbcc → 低库存显示 BC 盘(platform/platform_b)+own(集货仓=B仓调拨源);
    traditional → 低库存显示 C 仓(platform)+own(自有/三方仓)
    """
    _key = "dash_aux|%s|%s" % (channel, mode)
    return ok(_cache_get(_key, _AUX_TTL, lambda: _build_aux(channel, mode)))


def _build_aux(channel, mode):
    """aux 计算体(共享缓存 builder)"""
    from routes.alerts import _FIELDS as _AF
    # 低库存维度过滤(跟随补货模式): bbcc → BC+own; traditional → C+own(白名单拼接)
    _wh_in = "('own','platform','platform_b')" if mode == "bbcc" else "('own','platform')"
    _wh_cond = " AND warehouse_type IN " + _wh_in
    # alerts 分组配额
    alerts = []
    for atype in ("low_stock", "replenish", None):
        if atype:
            if atype == "low_stock":
                rows = query("SELECT %s FROM alerts WHERE channel=%%s AND status='active' AND alert_type=%%s%s "
                             "ORDER BY id DESC LIMIT 100" % (_AF, _wh_cond), [channel, atype])
            else:
                rows = query("SELECT %s FROM alerts WHERE channel=%%s AND status='active' AND alert_type=%%s "
                             "ORDER BY id DESC LIMIT 100" % _AF, [channel, atype])
        else:
            rows = query("SELECT %s FROM alerts WHERE channel=%%s AND status='active' "
                         "AND alert_type NOT IN ('low_stock','replenish') ORDER BY id DESC LIMIT 100" % _AF, [channel])
        alerts.extend(rows)
    from routes.alerts import _attach_warehouse as _att
    _att(alerts, channel)
    counts = query("SELECT alert_type, severity, "
                   "IFNULL(NULLIF(warehouse_type,''),'') AS wt, COUNT(*) AS c "
                   "FROM alerts WHERE channel=%s AND status='active' "
                   "AND (alert_type != 'low_stock' OR warehouse_type IN " + _wh_in + ") "
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
    _aux_result = {
        "alerts": alerts,
        "alertCounts": alert_counts_full,
        "stockOverview": {"items": so_items,
                          "out_of_stock_count": int(out.get("c") or 0),
                          "low_stock_count": int(low.get("c") or 0),
                          "total": int(out.get("c") or 0) + int(low.get("c") or 0)},
        "bcOutOfStock": bc_out,
    }
    return _aux_result


@router.get("/dashboard/stock-risk")
@traced
def stock_risk(channel: str = "jd", full: int = 0):
    """濒临断货(30s 共享表缓存 —— 比 aux 实时, 数据变更由 invalidate_all 全局立即失效)"""
    _key = "stock_risk|%s|%s" % (channel, full)
    return ok(_cache_get(_key, _RISK_TTL, lambda: _stock_risk(channel, full=full)))


_accel_cache = {}
_ACCEL_TTL = 60


def _rule_params(alert_type, channel=None):
    """看板计算参数源: 规则 params(alert_type 对应内置规则, 停用/删除=回默认) → {}"""
    import json as _json
    try:
        sql = "SELECT params FROM rules WHERE alert_type=%s AND is_active=1 " \
              "AND (deleted_at IS NULL OR deleted_at='') LIMIT 1"
        _p = [alert_type]
        if channel:
            sql = sql.replace("LIMIT 1", "AND channel=%s LIMIT 1")
            _p.append(channel)
        row = one(sql, _p)
        if row and row.get("params"):
            d = _json.loads(row["params"])
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def _hourly_accel(channel, now, ratio=1.3, min_qty=10.0):
    """P1 加速消耗判定: 当天累计销量 vs 前3天同时刻(当前小时前)累计平均

    比值 ≥ ratio(默认1.3) 且样本量足够(≥min_qty 单) → 视为加速(大促/秒杀), 返回 {sku: 倍率}
    (需求按当前流速放大 → adj_dos 缩短 → 更易判濒临)
    60s 共享表缓存: 当天订单近 1 小时基本不变, 避免 stock-risk 每次重算重复查 orders
    """
    _key = "accel|%s|%s|%s" % (channel, ratio, min_qty)
    return _cache_get(_key, _ACCEL_TTL,
                      lambda: _compute_accel(channel, now, ratio, min_qty))


def _compute_accel(channel, now, ratio, min_qty):
    from datetime import timedelta
    paid = tuple(SALES_STATUSES)  # 加速判定用日销口径(排除申请退款)
    today = now.strftime("%Y-%m-%d")
    since = (now - timedelta(days=3)).strftime("%Y-%m-%d 00:00:00")
    cur_h = now.hour
    # IN 参数化(曾 % 预格式化整条 SQL 只给 IN 参数 → channel/ordered_at %s 无参数 → not enough arguments)
    _in = ",".join(["%s"] * len(paid))
    rows = query(
        "SELECT sku, DATE(ordered_at) AS d, HOUR(ordered_at) AS h, SUM(quantity) AS q "
        "FROM orders WHERE channel=%s AND ordered_at>=%s "
        "AND order_status IN (" + _in + ") AND (deleted_at IS NULL OR deleted_at='') "
        "GROUP BY sku, DATE(ordered_at), HOUR(ordered_at)",
        [channel, since] + list(paid))
    hist = {}
    today_q = {}
    for r in rows:
        sku = str(r.get("sku") or "")
        d = str(r.get("d") or "")[:10]
        h = int(r.get("h") or 0)
        q = int(r.get("q") or 0)
        if d == today:
            today_q[sku] = today_q.get(sku, 0) + q
        elif h < cur_h:
            hist[sku] = hist.get(sku, 0) + q / 3.0
    out = {}
    for sku, tq in today_q.items():
        if tq < min_qty:
            continue
        hq = hist.get(sku, 0)
        if hq >= min_qty and tq / hq >= ratio:
            out[sku] = round(tq / hq, 2)
    return out


def _stock_risk(channel, full: int = 0):
    """濒临断货 TOP: B(BBCC)/C(传统)/BC/own 维度"""
    from biz.sales import load_daily_sales_grouped, calc_sales_multi, rolling_predict
    from db import query as _q
    cfg_rows = _q("SELECT `key`, value FROM replenishment_config WHERE channel=%s", [channel])
    cfg = {r.get("key"): r.get("value") for r in cfg_rows}
    # 有效补货周期 L/T_eff(模式双线, 复用补货配置): bc 维度= B→C 调拨+安全缓冲; 传统维度=采购到货
    # L/T 与补货建议同源(mode 前缀键优先, 平铺兜底)——断货红线=补货周期, 跟随 bbcc/传统模式
    def _mc(key, mode, default):
        v = cfg.get("mode_%s_%s" % (mode, key))
        if v is None:
            v = cfg.get(key)
        try:
            return int(float(v or default))
        except Exception:
            return default
    lit_bbcc = _mc("b_to_c_days", "bbcc", 3) + _mc("c_safety_days", "bbcc", 0)
    lit_trad = _mc("lead_time_days", "traditional", 10)

    inv = _q("SELECT sku, warehouse_type, warehouse, available_qty, in_transit_qty, c_transit, safety_qty, product_name "
             "FROM inventory WHERE channel=%s", [channel])
    prods = _q("SELECT sku, barcode, product_name, supplier_code FROM products WHERE channel=%s "
               "AND (deleted_at IS NULL OR deleted_at='')", [channel])
    pmap = {r.get("sku"): r for r in prods}
    skus = set([r.get("sku") for r in inv]) | set(pmap.keys())
    # 看板计算参数(先于 otif_map: otif_min 用于在途打折, 规则 params 优先/config 兜底/默认保底)
    _rp = _rule_params("stockout", channel)
    def _cf(key, default):
        try:
            return float(_rp.get(key, cfg.get(key, default)))
        except Exception:
            return default
    otif_min = _cf("otif_min", 0.6)
    accel_ratio = _cf("accel_ratio", 1.3)
    accel_min_qty = _cf("accel_min_qty", 10.0)
    ss_z = _cf("ss_z", 1.65)
    buffer_orange = _cf("buffer_orange", 1.2)
    buffer_yellow = _cf("buffer_yellow", 1.0)
    orange_slack = _cf("orange_slack_days", 1.0)
    include_avail_zero = _cf("include_avail_zero", 1.0) > 0.5
    # OTIF 置信系数: suppliers.score/5 → 在途可信度(0.6~1.0, 未评分=1.0 维持现状口径)
    otif_map = {}
    for r in _q("SELECT supplier_code, MAX(score) AS score FROM suppliers GROUP BY supplier_code"):
        try:
            sc = float(r.get("score") or 0)
            otif_map[r.get("supplier_code")] = max(min(sc / 5.0, 1.0), otif_min) if sc > 0 else 1.0
        except Exception:
            pass
    # P1: 需求调整因子 —— 活动系数(season_config, 补货/采购已用) + 当天小时流速加速(加速判定)
    try:
        from routes.replenishment import _season_factor
        factor_trad = _season_factor(channel, "traditional")
        factor_bbcc = _season_factor(channel, "bbcc")
    except Exception:
        factor_trad = factor_bbcc = 1.0
    try:
        from datetime import datetime as _dt, timezone as _tz
        accel = _hourly_accel(channel, _dt.now(_tz.utc), ratio=accel_ratio, min_qty=accel_min_qty)
    except Exception:
        accel = {}

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
    # 逐仓日销(传统多仓: 断货预警按 SKU×仓 粒度, 与补货建议逐仓口径一致)
    wh_multi = calc_sales_multi(by_sku_wh, windows=[7, 14, 28])
    wh_fused = {k: rolling_predict(wh_multi[7].get(k, 0), wh_multi[14].get(k, 0),
                                   wh_multi[28].get(k, 0)) for k in by_sku_wh}
    # P2: 动态安全库存 SS_dyn = Z(1.65) × 日销σ × √L/T —— 波动越大动态安全线越高, 更早濒临
    # (供应链看概率和波动: 平均日销够不代表明天稳定, σ 高 → 缓冲要求高)
    sigma = {}
    for _s, _daily in by_sku.items():
        if len(_daily) >= 7:
            _vals = list(_daily.values())
            _m = sum(_vals) / len(_vals)
            _v = sum((x - _m) ** 2 for x in _vals) / len(_vals)
            sigma[_s] = _v ** 0.5

    def _ss_dyn(sku, lit):
        return ss_z * sigma.get(sku, 0) * (lit ** 0.5) if lit > 0 else 0.0

    # 聚合(BC 合计=platform+platform_b 按 SKU 一盘棋 —— B 仓已含在合计内, 不再单独算 B 维度)
    bc_total = {}
    for r in inv:
        sku = r.get("sku")
        wt = r.get("warehouse_type")
        qty = int(r.get("available_qty") or 0)
        safety = int(r.get("safety_qty") or 0)
        tty = int(r.get("in_transit_qty") or 0)
        ctt = int(r.get("c_transit") or 0)
        if wt in ("platform", "platform_b"):
            st = bc_total.setdefault(sku, {"available": 0, "safety": 0, "transit": 0, "ct": 0})
            st["available"] += qty
            st["safety"] += safety
            # 链路: 供应商统一发 B 仓 → B→C 调拨补 C 缺口 → BC 供应的在途仅 B 仓行(供应商→B)
            # + C 仓行 c_transit(B→C 调拨); C 仓行 in_transit(直发)在 BBCC 链路无业务含义
            if wt == "platform_b":
                st["transit"] += tty
            if wt == "platform":
                st["ct"] += ctt

    def _otif(sku):
        """该 SKU 供应商置信系数(products.supplier_code → suppliers.score/5)"""
        sup = (pmap.get(sku) or {}).get("supplier_code")
        return otif_map.get(sup, 1.0) if sup else 1.0

    def _grade(adj_dos, buffer, lit):
        """三级分级(供应链时间线优先), 返回 (入选?, level); 阈值可由规则页'看板计算'配置"""
        if adj_dos <= lit:
            return True, "red"
        if adj_dos <= lit + orange_slack and buffer <= buffer_orange:
            return True, "orange"
        if buffer <= buffer_yellow:
            return True, "yellow"
        return False, None

    # C 维度(传统多仓: 逐仓粒度 —— 一个 SKU 一个仓库一行, 该仓库存/该仓日销/该仓可撑天数)
    c_items = []
    for r in inv:
        if r.get("warehouse_type") != "platform":
            continue
        sku = r.get("sku")
        wh = str(r.get("warehouse") or "")
        avail = int(r.get("available_qty") or 0)
        safety = int(r.get("safety_qty") or 0)
        tty = int(r.get("in_transit_qty") or 0)
        ctt = int(r.get("c_transit") or 0)
        ds = wh_fused.get("%s|%s" % (sku, wh), 0) or fused_c.get(sku, 0) or fused.get(sku, 0)
        if ds <= 0:
            continue
        if avail <= 0 and not include_avail_zero:
            continue  # include_avail_zero=0: 已断不入断货卡(归健康卡缺货承接)
        # 修正可售天数: (在仓 + 供应商在途×OTIF + B→C调拨在途×1.0) ÷ (日销×活动系数×加速倍率)
        a_rate = accel.get(sku, 1.0)
        ds_eff = ds * factor_trad * a_rate
        adj_dos = (avail + tty * _otif(sku) + ctt) / ds_eff
        buffer = avail / max(max(safety, _ss_dyn(sku, lit_trad)), 1)
        inc, lv = _grade(adj_dos, buffer, lit_trad)
        if inc:
            c_items.append({"sku": sku, "barcode": (pmap.get(sku) or {}).get("barcode", ""),
                            "product_name": (pmap.get(sku) or {}).get("product_name", sku),
                            "warehouse": wh or "C仓", "type": "C", "available_qty": avail,
                            "daily_sales": round(ds, 1), "days_to_empty": round(adj_dos, 1),
                            "level": lv, "accel": (a_rate > 1.1) or None})
    # BC 合计(bbcc 专属: B仓+全国C仓按 SKU 合计, 一盘棋视图 → 标签 BC; 用 bbcc 补货周期)
    bc_items = []
    for sku, st in bc_total.items():
        avail = st["available"]
        safety = st["safety"]
        ds = fused_c.get(sku, 0)
        if ds <= 0:
            continue
        if avail <= 0 and not include_avail_zero:
            continue
        adj_dos = (avail + st["transit"] * _otif(sku) + st["ct"]) / (ds * factor_bbcc * accel.get(sku, 1.0))
        buffer = avail / max(max(safety, _ss_dyn(sku, lit_bbcc)), 1)
        inc, lv = _grade(adj_dos, buffer, lit_bbcc)
        if inc:
            bc_items.append({"sku": sku, "barcode": (pmap.get(sku) or {}).get("barcode", ""),
                             "product_name": (pmap.get(sku) or {}).get("product_name", sku),
                             "warehouse": "BC", "type": "BC", "available_qty": avail,
                             "daily_sales": round(ds, 1), "days_to_empty": round(adj_dos, 1),
                             "level": lv, "accel": (accel.get(sku, 1.0) > 1.1) or None})
    # own 维度(传统: 逐仓 —— jd 集货仓 / other 三方仓, 该仓库存/该仓日销/可撑天数)
    own_items = []
    for r in inv:
        if r.get("warehouse_type") != "own":
            continue
        sku = r.get("sku")
        wh = str(r.get("warehouse") or "")
        avail = int(r.get("available_qty") or 0)
        safety = int(r.get("safety_qty") or 0)
        tty = int(r.get("in_transit_qty") or 0)
        ds = wh_fused.get("%s|%s" % (sku, wh), 0) or fused.get(sku, 0)
        if ds <= 0:
            continue
        if avail <= 0 and not include_avail_zero:
            continue
        adj_dos = (avail + tty * _otif(sku)) / (ds * factor_trad * accel.get(sku, 1.0))
        buffer = avail / max(max(safety, _ss_dyn(sku, lit_trad)), 1)
        inc, lv = _grade(adj_dos, buffer, lit_trad)
        if inc:
            own_items.append({"sku": sku, "barcode": (pmap.get(sku) or {}).get("barcode", ""),
                              "product_name": (pmap.get(sku) or {}).get("product_name", sku),
                              "warehouse": wh or "自有", "type": "OWN", "available_qty": avail,
                              "daily_sales": round(ds, 1), "days_to_empty": round(adj_dos, 1),
                              "level": lv, "accel": (accel.get(sku, 1.0) > 1.1) or None})

    def _stats(items):
        crit = sum(1 for i in items if i.get("level") == "red")
        warn = sum(1 for i in items if i.get("level") == "orange")
        return len(items), crit, warn

    _lv = {"red": 0, "orange": 1, "yellow": 2}
    bc_items.sort(key=lambda x: (_lv.get(x.get("level"), 9), x["days_to_empty"]))
    c_items.sort(key=lambda x: (_lv.get(x.get("level"), 9), x["days_to_empty"]))
    own_items.sort(key=lambda x: (_lv.get(x.get("level"), 9), x["days_to_empty"]))
    bt, bc, bw = _stats(bc_items)
    ct, cc, cw = _stats(c_items)
    ot, oc, ow = _stats(own_items)
    # items(B 维度)已移除: bc 合计覆盖 B 仓, 前端 bbcc 只用 bcItems / traditional 用 cItems+ownItems
    payload = {"items": [], "total": 0, "critical": 0, "warning": 0,
               "bcItems": bc_items if full else bc_items[:10], "bcTotal": bt, "bcCritical": bc, "bcWarning": bw,
               "cItems": c_items if full else c_items[:10], "cTotal": ct, "cCritical": cc, "cWarning": cw,
               "ownItems": own_items if full else own_items[:10], "ownTotal": ot, "ownCritical": oc, "ownWarning": ow}
    return payload

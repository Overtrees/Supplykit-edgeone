"""In-memory dashboard cache, rebuilt on demand or invalidated by events."""
import time, os, sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from app.core.database import get_db, DB_PATH, get_conn

_cache = None
_cache_ts = 0
_cache_dirty = True
_CACHE_TTL = 180

_cache_by_channel = {}
_stock_risk_cache = {}
_cache_version = 0

def _compute_funnel(orders):
    """Order conversion funnel."""
    total = len(orders)
    statuses = {"待确认": 0, "待发货": 0, "已发货": 0, "已完成": 0, "申请退款": 0}
    for x in orders:
        s = x.get("order_status") or "未知"
        if s in statuses: statuses[s] += 1
        else: statuses["未知"] = statuses.get("未知", 0) + 1
    stages = [("总订单", total, 100.0)]
    for name in ["待确认", "待发货", "已发货", "已完成"]:
        v = statuses.get(name, 0)
        stages.append((name, v, round(v / total * 100, 1) if total else 0))
    result = []
    for i, (name, count, pct) in enumerate(stages):
        prev = stages[i - 1][1] if i > 0 else total
        conv = round(min(count / prev * 100, 100), 1) if prev else 0
        result.append({"name": name, "value": count, "percentage": pct, "conversion": conv})
    return result

def _compute_period_trends(conn, ch, today):
    """Compute period trends (today/week/month) using SQL.

    GMV 小卡口径 = 已支付(待发货/已发货/已完成/申请退款): 订单数与 GMV 都是已支付;
    漏斗(订单阶段分布)=全部状态。两卡不同业务口径。
    """
    from datetime import timedelta, timezone
    periods = {}
    # 各维度同时计算上一周期(环比基准): today→昨日, week→上周同7天, month→上月同30天
    for pname, pdays, prev_span in [('today', 1, 1), ('week', 7, 7), ('month', 30, 30)]:
        cutoff = (today - timedelta(days=pdays - 1)).isoformat()
        prev_cutoff = (today - timedelta(days=pdays + prev_span - 1)).isoformat()
        prev_end = (today - timedelta(days=pdays)).isoformat()  # 上一周期截止(不含本周期)
        rows = conn.execute("SELECT ordered_at, SUM(total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0)) as g, SUM(CASE WHEN order_status='申请退款' THEN total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0) ELSE 0 END) as rf, SUM(COALESCE(subsidy_amount,0)) as sub, COUNT(*) as cnt FROM orders WHERE channel=? AND ordered_at>=? AND order_status IN ('待发货','已发货','已完成','申请退款') AND (deleted_at='') GROUP BY ordered_at", (ch, cutoff)).fetchall()
        # 上一周期(仅gmv/orders, 供环比)
        prev_rows = conn.execute("SELECT SUM(total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0)) as g, COUNT(*) as cnt FROM orders WHERE channel=? AND ordered_at>=? AND ordered_at<=? AND order_status IN ('待发货','已发货','已完成','申请退款') AND (deleted_at='')", (ch, prev_cutoff, prev_end)).fetchall()
        daily = {}
        for r in rows:
            date_str = r[0][5:] if r[0] else '未知'
            if date_str not in daily: daily[date_str] = {"gmv": 0, "refund": 0, "subsidy": 0, "orders": 0}
            daily[date_str]["gmv"] += r[1]
            daily[date_str]["refund"] += r[2]
            daily[date_str]["subsidy"] += r[3]
            daily[date_str]["orders"] += r[4]
        _pg = prev_rows[0][0] if prev_rows and prev_rows[0][0] else 0
        _po = prev_rows[0][1] if prev_rows and prev_rows[0][1] else 0
        periods[pname] = {"gmv": sum(v["gmv"] for v in daily.values()), "orders": sum(v["orders"] for v in daily.values()),
                          "net_gmv": round(sum(v["gmv"] for v in daily.values()) - sum(v["refund"] for v in daily.values()), 2),
                          "subsidy_amount": round(sum(v["subsidy"] for v in daily.values()), 2),
                          "payout": round(sum(v["gmv"] for v in daily.values()) - sum(v["refund"] for v in daily.values()) - sum(v["subsidy"] for v in daily.values()), 2),
                          "prev_gmv": round(_pg, 2), "prev_orders": _po}
        periods[pname + "_trend"] = [{"日期": k, "GMV": round(v["gmv"], 2), "订单数": v["orders"]} for k, v in sorted(daily.items())]
    return periods

def _compute_health(inv):
    """Inventory health index."""
    def _score(items):
        total = len(items)
        healthy = sum(1 for x in items if int(x.get("available_qty") or 0) >= int(x.get("safety_qty") or 0))
        warning = sum(1 for x in items if 0 < int(x.get("available_qty") or 0) < int(x.get("safety_qty") or 0))
        out_of_stock = sum(1 for x in items if int(x.get("available_qty") or 0) == 0)
        score = round(healthy / total * 100, 0) if total else 100
        return {"score": score, "healthy": healthy, "warning": warning, "out_of_stock": out_of_stock,
                "total": total, "level": "good" if score >= 85 else ("warning" if score >= 60 else "danger")}
    own = [x for x in inv if x.get('warehouse_type') == 'own']
    plat = [x for x in inv if x.get('warehouse_type') == 'platform']
    platformB = [x for x in inv if x.get('warehouse_type') == 'platform_b']
    bc = plat + platformB
    return {"own": _score(own), "platform": _score(plat), "platform_b": _score(platformB), "bc": _score(bc),
            "score": _score(inv)["score"], "level": _score(inv)["level"]}

from app.core.database import DB_PATH as _DB_PATH

# 已支付订单状态(计入 GMV/净GMV口径): 待发货/已发货/已完成 + 申请退款(已付款产生流水, 净GMV再扣除)
# 不计入: 待确认(待付款)、空。GMV 卡(金额/订单数/趋势)=已支付; 漏斗(订单阶段分布)=全部状态, 两卡不同业务口径
_PAID_STATUSES = ('待发货', '已发货', '已完成', '申请退款')

def _rebuild(channel='jd'):
    """Full rebuild of dashboard data from database using SQL aggregation."""
    conn = get_conn()
    ch = channel
    from datetime import timedelta, timezone
    from concurrent.futures import ThreadPoolExecutor
    _today = datetime.now(timezone.utc).date()
    _cut90 = (_today - timedelta(days=90)).isoformat()
    bj_now = datetime.now(timezone.utc) + timedelta(hours=8)
    bj_date = bj_now.date()
    _month_cut = (bj_date - timedelta(days=29)).isoformat()
    _week_cut = (bj_date - timedelta(days=6)).isoformat()
    
    # 单次扫描: date,status,store 三维 GROUP BY——替代原3次扫orders(90天d,status + 90天store + 30天d,store),
    # PA慢磁盘12万行×3次=~10s → 1次(~3s)。Python一次遍历拆出所有维度
    def _q_all():
        import sqlite3
        _c = sqlite3.connect(_DB_PATH)
        _c.row_factory = sqlite3.Row
        _c.execute("PRAGMA busy_timeout=30000")
        return _c.execute(
            "SELECT substr(ordered_at,1,10) as d, order_status, store, "
            "SUM(CASE WHEN order_status IN ('待发货','已发货','已完成','申请退款') THEN total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0) ELSE 0 END) as g, "
            "SUM(CASE WHEN order_status IN ('待发货','已发货','已完成','申请退款') THEN COALESCE(subsidy_amount,0) ELSE 0 END) as sub, "
            "COUNT(*) as cnt "
            "FROM orders WHERE channel=? AND ordered_at>=? AND (deleted_at='') GROUP BY d, order_status, store",
            (ch, _cut90)).fetchall()
    all_rows = _q_all()

    # 品牌聚合: 独立 GROUP BY sku 查询(仅~9000 sku行, 替代3次 orders LEFT JOIN products 全量扫描)
    # 一次取90天(sku级), Python 按 sku→brand 映射累计; 30天窗口单独一次
    _brand_map = {}
    try:
        for r in conn.execute("SELECT sku, brand FROM products WHERE channel=? AND (deleted_at='' OR deleted_at IS NULL)", (ch,)).fetchall():
            _brand_map[str(r[0])] = r[1] or ''
    except Exception:
        pass
    def _load_brand_rows(_from):
        return conn.execute(
            "SELECT sku, substr(ordered_at,1,10) as d, "
            "SUM(CASE WHEN order_status IN ('待发货','已发货','已完成','申请退款') THEN total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0) ELSE 0 END) as g, "
            "SUM(CASE WHEN order_status='申请退款' THEN total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0) ELSE 0 END) as rf, "
            "SUM(CASE WHEN order_status IN ('待发货','已发货','已完成','申请退款') THEN COALESCE(subsidy_amount,0) ELSE 0 END) as sub, "
            "SUM(CASE WHEN order_status IN ('待发货','已发货','已完成','申请退款') THEN 1 ELSE 0 END) as c "
            "FROM orders WHERE channel=? AND ordered_at>=? AND (deleted_at='') GROUP BY sku, d",
            (ch, _from)).fetchall()

    # 一遍遍历 all_rows 拆: gmv/pending/refund/refund_amount/subsidy/total + trend + status_dist + stores + 30天周期(ps/pf)
    gmv = pending = refund = refund_amount = subsidy_all = total_orders = 0
    by_date = {}
    _status_agg = {}
    _store_map = {}
    _ps_agg = {}
    _ps_refund = {}
    _ps_subsidy = {}
    _pf_agg = {}
    for r in all_rows:
        _d = r[0] or ''
        _st = r[1] or '未知'
        _store = r[2] or ''
        _g = r[3] or 0
        _sub = r[4] or 0
        _cnt = r[5] or 0
        total_orders += _cnt
        # GMV=已支付流水(待发货/已发货/已完成/申请退款); 净GMV在末尾扣申请退款金额
        if _st in _PAID_STATUSES:
            gmv += _g
            subsidy_all += _sub
            if _st == '待发货':
                pending += _cnt
            elif _st == '申请退款':
                refund += _cnt
                refund_amount += _g
        _key = _d[5:] if len(_d) >= 10 else _d
        if _key not in by_date: by_date[_key] = {"订单数": 0, "GMV": 0}
        if _st in _PAID_STATUSES:
            by_date[_key]["订单数"] += _cnt
            by_date[_key]["GMV"] += _g
        _status_agg[_st] = _status_agg.get(_st, 0) + _cnt
        _sm = _store_map.setdefault(_store, {"name": _store, "orders": 0, "gmv": 0, "refund_amount": 0, "subsidy_amount": 0})
        if _st in _PAID_STATUSES:
            _sm["orders"] += _cnt
            _sm["gmv"] += _g
            _sm["subsidy_amount"] += _sub
            if _st == '申请退款':
                _sm["refund_amount"] += _g
        if _d >= _month_cut:
            _pf_agg[(_d, _st)] = _pf_agg.get((_d, _st), 0) + _cnt
            if _st in _PAID_STATUSES:
                _ps_agg[(_d, _store)] = _ps_agg.get((_d, _store), 0) + _g
                _ps_subsidy[(_d, _store)] = _ps_subsidy.get((_d, _store), 0) + _sub
                if _st == '申请退款':
                    _ps_refund[(_d, _store)] = _ps_refund.get((_d, _store), 0) + _g
    trend = [{"日期": k, **v} for k, v in sorted(by_date.items())]
    status_dist = [{"name": k, "value": v} for k, v in _status_agg.items()]
    store_rows = sorted(_store_map.values(), key=lambda x: x['name'])
    stores = [{"name": r["name"], "orders": r["orders"], "gmv": r["gmv"],
               "refund_amount": round(r.get("refund_amount", 0), 2),
               "subsidy_amount": round(r.get("subsidy_amount", 0), 2),
               "net_gmv": round(r["gmv"] - r.get("refund_amount", 0), 2),
               "payout": round(r["gmv"] - r.get("refund_amount", 0) - r.get("subsidy_amount", 0), 2)} for r in store_rows]

    # ── 品牌GMV(品牌看渗透, 跨店归集; 与店铺看盘子正交)——orders无brand列, 用sku→brand映射
    # 已支付口径与 GMV 表达式同 stores; 90天总量 + 30天周期(供 今日/本周/本月 维度)
    def _brand_accum(_rows, _dmin=None):
        _acc = {}
        for r in _rows:
            if _dmin is not None and (r[1] or '') < _dmin:
                continue
            _br = _brand_map.get(str(r[0] or ''), '') or '未分类'
            _a = _acc.setdefault(_br, [0, 0, 0, 0])
            _a[0] += r[2] or 0; _a[1] += r[3] or 0; _a[2] += r[4] or 0; _a[3] += r[5] or 0
        return [{"name": k, "orders": v[3], "gmv": round(v[0], 2), "refund_amount": round(v[1], 2),
                 "subsidy_amount": round(v[2], 2), "net_gmv": round(v[0] - v[1], 2),
                 "payout": round(v[0] - v[1] - v[2], 2)} for k, v in sorted(_acc.items(), key=lambda x: -x[1][0])]
    _brow90 = _load_brand_rows(_cut90)
    brands = _brand_accum(_brow90)
    # 周期品牌: 30天窗口按日期切 今日/本周/本月
    _brow_m = _load_brand_rows(_month_cut)
    period_brands = {
        'today': _brand_accum(_brow_m, _dmin=bj_date.isoformat()),
        'week': _brand_accum(_brow_m, _dmin=(bj_date - timedelta(days=6)).isoformat()),
        'month': _brand_accum(_brow_m),
    }
    
    # SQL 聚合替代 Python 遍历(等价重构: 同一 inventory, CASE与Python比较一致)
    _store_low_rows = conn.execute(
        "SELECT store, SUM(CASE WHEN available_qty < safety_qty THEN 1 ELSE 0 END) as low FROM inventory WHERE channel=? GROUP BY store", (ch,)).fetchall()
    store_low = {r[0] or '': r[1] for r in _store_low_rows}
    low_stock = sum(store_low.values())
    for s in stores:
        s['low_stock'] = store_low.get(s['name'], 0)
    
    product_count = conn.execute("SELECT COUNT(*) FROM products WHERE channel=?", (ch,)).fetchone()[0]
    supplier_count = conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
    alert_count = conn.execute("SELECT COUNT(*) FROM alerts WHERE channel=? AND status='active'", (ch,)).fetchone()[0]
    cat_rows = conn.execute("SELECT category, COUNT(*) FROM products WHERE channel=? GROUP BY category", (ch,)).fetchall()
    cat_dist = [{"name": r[0] or '未分类', "value": r[1]} for r in cat_rows]
    # health: SQL GROUP BY warehouse_type(替代 _compute_health Python 5次遍历)
    _hw_rows = conn.execute(
        "SELECT warehouse_type, SUM(CASE WHEN available_qty >= safety_qty THEN 1 ELSE 0 END) as healthy, "
        "SUM(CASE WHEN available_qty > 0 AND available_qty < safety_qty THEN 1 ELSE 0 END) as warning, "
        "SUM(CASE WHEN available_qty = 0 THEN 1 ELSE 0 END) as out_of_stock, COUNT(*) as total "
        "FROM inventory WHERE channel=? GROUP BY warehouse_type", (ch,)).fetchall()
    _hw = {}
    for r in _hw_rows:
        _hw[r[0]] = {"healthy": r[1], "warning": r[2], "out_of_stock": r[3], "total": r[4]}
    def _score_hw(cls):
        healthy = cls.get('healthy', 0); total = cls.get('total', 0)
        score = round(healthy / total * 100, 0) if total else 100
        return {"score": score, "healthy": healthy, "warning": cls.get('warning', 0),
                "out_of_stock": cls.get('out_of_stock', 0), "total": total,
                "level": "good" if score >= 85 else ("warning" if score >= 60 else "danger")}
    _Z = {"healthy": 0, "warning": 0, "out_of_stock": 0, "total": 0}
    _own_h = _hw.get('own', _Z); _plat_h = _hw.get('platform', _Z); _pb_h = _hw.get('platform_b', _Z)
    # bc(京东主体) = B+C 按 SKU 合计判断(bbcc 全盘视角): 同一 SKU 在 B 仓与 C 仓的可用/安全线
    # 先合计再判断健康/偏低/缺货——行级相加会把"单仓缺但合计够"的 SKU 误判为缺货(更不精准)
    try:
        _bc_rows = conn.execute(
            "SELECT SUM(CASE WHEN s_avail >= s_safety THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN s_avail > 0 AND s_avail < s_safety THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN s_avail = 0 THEN 1 ELSE 0 END), COUNT(*) "
            "FROM (SELECT sku, SUM(available_qty) s_avail, SUM(safety_qty) s_safety "
            "FROM inventory WHERE channel=? AND warehouse_type IN ('platform','platform_b') GROUP BY sku)",
            (ch,)).fetchone()
        _bc_h = {"healthy": _bc_rows[0] or 0, "warning": _bc_rows[1] or 0,
                 "out_of_stock": _bc_rows[2] or 0, "total": _bc_rows[3] or 0}
    except Exception:
        _bc_h = _Z
    _all_h = {"healthy": sum(r.get('healthy',0) for r in _hw.values()), "warning": sum(r.get('warning',0) for r in _hw.values()),
              "out_of_stock": sum(r.get('out_of_stock',0) for r in _hw.values()), "total": sum(r.get('total',0) for r in _hw.values())}
    health = {"own": _score_hw(_own_h), "platform": _score_hw(_plat_h), "platform_b": _score_hw(_pb_h),
              "bc": _score_hw(_bc_h), "score": _score_hw(_all_h)["score"], "level": _score_hw(_all_h)["level"]}
    
    # ── 周期聚合：店铺 + 漏斗（纯 Python，30 天数据已收集）
    period_stores = {}
    period_funnel = {}
    for pname, pdays in [('today', 1), ('week', 7), ('month', 30)]:
        cutoff = bj_date - timedelta(days=pdays - 1)
        cutoff_str = cutoff.isoformat()
        _store_gmv = {}
        _store_refund = {}
        _store_subsidy = {}
        for (d, s), g in _ps_agg.items():
            if d >= cutoff_str: _store_gmv[s] = _store_gmv.get(s, 0) + g
        for (d, s), rf in _ps_refund.items():
            if d >= cutoff_str: _store_refund[s] = _store_refund.get(s, 0) + rf
        for (d, s), su in _ps_subsidy.items():
            if d >= cutoff_str: _store_subsidy[s] = _store_subsidy.get(s, 0) + su
        period_stores[pname] = [{"name": k, "gmv": v, "refund_amount": round(_store_refund.get(k, 0), 2),
                                 "subsidy_amount": round(_store_subsidy.get(k, 0), 2),
                                 "net_gmv": round(v - _store_refund.get(k, 0), 2),
                                 "payout": round(v - _store_refund.get(k, 0) - _store_subsidy.get(k, 0), 2)} for k, v in sorted(_store_gmv.items())]
        _st_cnt = {}
        for (d, st), c in _pf_agg.items():
            if d >= cutoff_str: _st_cnt[st] = _st_cnt.get(st, 0) + c
        ptotal = sum(_st_cnt.values())
        stages = [("总订单", ptotal, 100.0)]
        for name in ["待确认", "待发货", "已发货", "已完成"]:
            v = _st_cnt.get(name, 0)
            stages.append((name, v, round(v / ptotal * 100, 1) if ptotal else 0))
        result = []
        for i, (name, count, pct) in enumerate(stages):
            prev = stages[i - 1][1] if i > 0 else ptotal
            conv = round(min(count / prev * 100, 100), 1) if prev else 0
            result.append({"name": name, "value": count, "percentage": pct, "conversion": conv})
        period_funnel[pname] = result
    
    periods = _compute_period_trends(conn, ch, bj_date)
    
    return {
        "summary": {
            "gmv": round(gmv, 2), "net_gmv": round(gmv - refund_amount, 2), "refund_amount": round(refund_amount, 2),
            "subsidy_amount": round(subsidy_all, 2), "payout": round(gmv - refund_amount - subsidy_all, 2),
            "pending_count": pending, "refund_count": refund,
            "low_stock_count": low_stock, "total_orders": total_orders,
            "total_products": product_count, "total_suppliers": supplier_count, "active_alerts": alert_count,
        },
        "periods": periods,
        "trend": trend, "stores": stores, "period_stores": period_stores,
        "period_funnel": period_funnel,
        "status_distribution": status_dist, "category_distribution": cat_dist,
        "health_index": health,
        "brands": brands, "period_brands": period_brands,
    }

def get_cached_dashboard(channel):
    global _cache_by_channel, _cache_version, _cache_dirty
    now = time.time()
    # 修正: stale 必须比对"缓存构建时的版本 vs DB 当前版本"——曾直接取 DB 版本值当布尔,
    # 只要 _cache_version>0 就恒真 → 每次请求强制重建(缓存永不命中), 看板 summary 60s+ 主因
    db_ver = check_db_version() or 0
    cached = _cache_by_channel.get(channel)
    fresh = cached is not None and (cached.get('ver', -1) == db_ver) and (now - cached['ts']) <= _CACHE_TTL and not _cache_dirty
    if fresh:
        return cached['data']
    # 无缓存/版本变化/超 TTL/脏标记 → 同步重建
    try:
        data = _rebuild(channel)
        _cache_by_channel[channel] = {'data': data, 'ts': time.time(), 'ver': db_ver}
        _cache_dirty = False
        return data
    except Exception as e:
        import logging; logging.warning(f"[dash-cache] rebuild: {e}")
        if cached: return cached['data']
        raise


def get_dashboard_sync(channel):
    """强制同步重建并返回新数据（填充/导入/重置完成后前端主动触发）

    数据精度优先：不返回旧值，清缓存 → 递增版本号 → 同步重建（阻塞几秒可接受，
    因为用户刚完成关键操作，明确预期等待）。
    """
    global _cache_dirty, _cache_by_channel, _cache_version
    _cache_by_channel.pop(channel, None)
    _cache_dirty = True
    _cache_version += 1
    try:
        conn = get_conn()
        conn.execute("INSERT OR REPLACE INTO replenishment_config(key,value) VALUES('_cache_version',?)", (str(_cache_version),))
        conn.commit()
    except Exception as e:
        import logging; logging.warning(f"[dash-cache] persist version: {e}")
    data = _rebuild(channel)
    _cache_by_channel[channel] = {'data': data, 'ts': time.time()}
    _cache_dirty = False
    return data

def check_db_version():
    try:
        conn = get_conn()
        v = conn.execute("SELECT value FROM replenishment_config WHERE key='_cache_version'").fetchone()
        return int(v[0]) if v else 0
    except Exception as e:
        import logging; logging.warning(f"[dash-cache] check version: {e}")
        return 0

def invalidate():
    global _cache_dirty, _cache_by_channel, _cache_version, _stock_risk_cache
    _cache_dirty = True
    _stock_risk_cache.clear()
    _cache_version += 1
    # 持久化版本号到 DB（跨 worker 兼容），但 busy_timeout=0 不阻塞
    # 如果被其他写操作锁住，立即失败，不影响当前请求处理
    try:
        import sqlite3
        _c = sqlite3.connect(DB_PATH)
        _c.execute("PRAGMA busy_timeout=0")
        _c.execute("INSERT OR REPLACE INTO replenishment_config(key,value) VALUES('_cache_version',?)", (str(_cache_version),))
        _c.commit()
        _c.close()
    except Exception:
        pass  # 写失败不影响功能（下次请求 stale 检测时版本号一致，但缓存仍有效）

def get_stock_risk(channel='jd'):
    global _stock_risk_cache
    now = time.time()
    cached = _stock_risk_cache.get(channel)
    if cached is None or (now - cached['ts']) > 300:
        conn = get_conn()
        inv = conn.execute("SELECT sku, product_name, available_qty, safety_qty, warehouse_type FROM inventory WHERE channel=? AND warehouse_type!='platform_b'", (channel,)).fetchall()
        items = []
        for r in inv:
            q = int(r[2] or 0)
            s = int(r[3] or 0)
            if 0 < q <= s:
                items.append({"sku": r[0], "product_name": r[1], "available_qty": q, "safety_qty": s, "warehouse_type": r[4]})
        items.sort(key=lambda x: x['available_qty'] / max(x['safety_qty'], 1))
        _stock_risk_cache[channel] = {'data': items[:10], 'ts': now}
    return _stock_risk_cache[channel]['data']
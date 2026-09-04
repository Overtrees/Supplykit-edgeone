from fastapi import APIRouter
from app.core.dashboard_cache import get_cached_dashboard as get_dashboard, invalidate, _compute_health
from app.core.database import get_db, get_conn, DB_PATH
from app.core.sales_utils import calc_sales, rolling_predict
import sqlite3
from app.core.response import ok, fail
from datetime import datetime, timedelta, timezone
import time

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_stock_risk_cache = {}
_STOCK_CACHE_TTL = 300  # 5 分钟

@router.get("/summary")
def dashboard_summary(channel: str = 'jd', start_date: str = '', end_date: str = '', refresh: bool = False):
    if refresh:
        # 强制同步重建（填充/导入/重置完成后前端主动触发，确保即时准确）
        from app.core.dashboard_cache import get_dashboard_sync
        return ok(get_dashboard_sync(channel))
    if start_date and end_date:
        # 自定义日期范围：实时计算，不缓存
        # SQL 单次扫描聚合替代旧版「全表 orders 加载 + Python 遍历」——与标准 summary 同口径:
        #   - 只取日期范围内、本渠道订单(旧版未过滤 channel, 混入另一渠道数据 → 已修)
        #   - trend GMV 只计「已完成」、订单数计全部(旧版相反 → 已对齐标准)
        #   - health_index: bc = platform + platform_b(B+C 总和, 京东主体口径, 勿拆成单独 B 仓)
        from datetime import datetime as dt, timezone
        from collections import defaultdict
        _conn = sqlite3.connect(DB_PATH)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA busy_timeout=30000")
        _rows = _conn.execute(
            "SELECT substr(ordered_at,1,10) as d, order_status, store, "
            "SUM(CASE WHEN order_status IN ('待发货','已发货','已完成','申请退款') THEN total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0) ELSE 0 END) as g, "
            "SUM(CASE WHEN order_status IN ('待发货','已发货','已完成','申请退款') THEN COALESCE(subsidy_amount,0) ELSE 0 END) as sub, "
            "COUNT(*) as cnt "
            "FROM orders WHERE channel=? AND (deleted_at='') AND substr(ordered_at,1,10) BETWEEN ? AND ? "
            "GROUP BY d, order_status, store", (channel, start_date, end_date)).fetchall()
        from app.core.dashboard_cache import _PAID_STATUSES
        gmv = pending = refund = refund_amount = subsidy_all = total_orders = paid_orders = 0
        trend = {}
        store_gmv = defaultdict(float)
        store_refund = defaultdict(float)
        store_subsidy = defaultdict(float)
        funnel = defaultdict(int)
        for r in _rows:
            _d = r[0] or ''
            _st = r[1] or '未知'
            _store = r[2] or ''
            _g = float(r[3] or 0)
            _sub = float(r[4] or 0)
            _cnt = r[5] or 0
            total_orders += _cnt
            # GMV=已支付流水(待发货/已发货/已完成/申请退款); 净GMV末尾扣申请退款金额
            if _st in _PAID_STATUSES:
                gmv += _g
                subsidy_all += _sub
                paid_orders += _cnt
                store_gmv[_store or '其他'] += _g
                store_subsidy[_store or '其他'] += _sub
                if _st == '待发货':
                    pending += _cnt
                elif _st == '申请退款':
                    refund += _cnt
                    refund_amount += _g
                    store_refund[_store or '其他'] += _g
            t = trend.setdefault(_d, {"GMV": 0, "订单数": 0})
            # GMV 卡口径: 订单数=已支付(与漏斗"全部状态"是不同业务口径)
            if _st in _PAID_STATUSES:
                t["订单数"] += _cnt
                t["GMV"] += _g
            funnel[_st] += _cnt
        trend_data = [{"日期": k, "GMV": v["GMV"], "订单数": v["订单数"]} for k, v in sorted(trend.items())]
        stores = [{"name": k, "gmv": round(v, 2), "refund_amount": round(store_refund.get(k, 0), 2),
                   "subsidy_amount": round(store_subsidy.get(k, 0), 2),
                   "net_gmv": round(v - store_refund.get(k, 0), 2),
                   "payout": round(v - store_refund.get(k, 0) - store_subsidy.get(k, 0), 2)} for k, v in sorted(store_gmv.items(), key=lambda x: -x[1])]
        # 漏斗(与 dashboard_cache._compute_funnel 同口径)
        _ftotal = total_orders
        _stages = [("总订单", _ftotal, 100.0)]
        for _n in ["待确认", "待发货", "已发货", "已完成"]:
            _v = funnel.get(_n, 0)
            _stages.append((_n, _v, round(_v / _ftotal * 100, 1) if _ftotal else 0))
        funnel_res = []
        for _i, (_n, _c, _pct) in enumerate(_stages):
            _prev = _stages[_i - 1][1] if _i > 0 else _ftotal
            funnel_res.append({"name": _n, "value": _c, "percentage": _pct,
                               "conversion": round(min(_c / _prev * 100, 100), 1) if _prev else 0})
        # 计数类指标 SQL(与标准 summary 同口径)
        low_stock = _conn.execute("SELECT COUNT(*) FROM inventory WHERE channel=? AND available_qty < safety_qty", (channel,)).fetchone()[0]
        alert_count = _conn.execute("SELECT COUNT(*) FROM alerts WHERE channel=? AND status='active'", (channel,)).fetchone()[0]
        product_count = _conn.execute("SELECT COUNT(*) FROM products WHERE channel=? AND (deleted_at='' OR deleted_at IS NULL)", (channel,)).fetchone()[0]
        supplier_count = _conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
        # 健康指数: SQL GROUP BY warehouse_type(与 dashboard_cache._rebuild 同构)
        _hw_rows = _conn.execute(
            "SELECT warehouse_type, "
            "SUM(CASE WHEN available_qty >= safety_qty THEN 1 ELSE 0 END) as healthy, "
            "SUM(CASE WHEN available_qty > 0 AND available_qty < safety_qty THEN 1 ELSE 0 END) as warning, "
            "SUM(CASE WHEN available_qty = 0 THEN 1 ELSE 0 END) as out_of_stock, COUNT(*) as total "
            "FROM inventory WHERE channel=? GROUP BY warehouse_type", (channel,)).fetchall()
        _hw = {r[0]: {"healthy": r[1], "warning": r[2], "out_of_stock": r[3], "total": r[4]} for r in _hw_rows}
        def _score_hw(_cls):
            _healthy = _cls.get('healthy', 0); _total = _cls.get('total', 0)
            _score = round(_healthy / _total * 100, 0) if _total else 100
            return {"score": _score, "healthy": _healthy, "warning": _cls.get('warning', 0),
                    "out_of_stock": _cls.get('out_of_stock', 0), "total": _total,
                    "level": "good" if _score >= 85 else ("warning" if _score >= 60 else "danger")}
        _Z = {"healthy": 0, "warning": 0, "out_of_stock": 0, "total": 0}
        _own_h = _hw.get('own', _Z); _plat_h = _hw.get('platform', _Z); _pb_h = _hw.get('platform_b', _Z)
        # 京东主体: BC tab = B仓(platform_b) + C仓(platform) 总和, 不是单独 B 仓
        _bc_h = {"healthy": _plat_h.get('healthy', 0) + _pb_h.get('healthy', 0),
                 "warning": _plat_h.get('warning', 0) + _pb_h.get('warning', 0),
                 "out_of_stock": _plat_h.get('out_of_stock', 0) + _pb_h.get('out_of_stock', 0),
                 "total": _plat_h.get('total', 0) + _pb_h.get('total', 0)}
        _all_h = {"healthy": sum(x.get('healthy', 0) for x in _hw.values()),
                  "warning": sum(x.get('warning', 0) for x in _hw.values()),
                  "out_of_stock": sum(x.get('out_of_stock', 0) for x in _hw.values()),
                  "total": sum(x.get('total', 0) for x in _hw.values())}
        health = {"own": _score_hw(_own_h), "platform": _score_hw(_plat_h), "platform_b": _score_hw(_pb_h),
                  "bc": _score_hw(_bc_h), "score": _score_hw(_all_h)["score"], "level": _score_hw(_all_h)["level"]}
        _conn.close()
        summary = {
            "gmv": round(gmv, 2), "net_gmv": round(gmv - refund_amount, 2), "refund_amount": round(refund_amount, 2),
            "subsidy_amount": round(subsidy_all, 2), "payout": round(gmv - refund_amount - subsidy_all, 2),
            "total_orders": total_orders, "pending_count": pending, "refund_count": refund,
            "low_stock_count": low_stock, "active_alerts": alert_count,
            "total_products": product_count, "total_suppliers": supplier_count,
        }
        day_count = max(1, (dt.strptime(end_date, "%Y-%m-%d") - dt.strptime(start_date, "%Y-%m-%d")).days + 1)
        return ok({
            "summary": summary,
            "periods": {"custom": {"gmv": round(gmv, 2), "orders": paid_orders, "days": day_count,
                                   "net_gmv": round(gmv - refund_amount, 2),
                                   "subsidy_amount": round(subsidy_all, 2),
                                   "payout": round(gmv - refund_amount - subsidy_all, 2)}},
            "trend": trend_data,
            "funnel": funnel_res, "health_index": health,
            "stores": stores,
        })
    data = get_dashboard(channel=channel)
    return ok(data)

@router.get("/aux")
def dashboard_aux(channel: str = 'jd', db = get_db()):
    """看板辅助数据聚合(alerts+stockRisk+stockOverview)——次要卡片一次请求

    与 summary 独立(不被 summary 同步重建拖累); 3个次要请求合并为1(省HTTP+排队)
    """
    # alerts: 非 replenish 优先(低库存/滞销告警先返回——补货告警量大占limit时低库存卡仍可见)
    # 分组配额取列表：补货告警(replenish)每次跑建议会重生成数千条，按 id DESC 取 limit 会被其
    # 占满窗口把低库存/滞销挤出 → 看板卡片空白。排序只决定组内顺序，可见性由分组配额保证。
    # 同时返回 alertCounts 精确计数，看板「(N 严重)」等指标不得再从截断列表 filter 得出。
    try:
        from app.api.routes.alerts import fetch_alerts_grouped, alert_counts
        _conn = sqlite3.connect(DB_PATH)
        alerts = fetch_alerts_grouped(_conn, channel, per_group_limit=200)
        alert_counts_data = alert_counts(_conn, channel)
        _conn.close()
    except Exception:
        alerts = []
        alert_counts_data = None
    # stock-risk（复用缓存逻辑）
    try:
        sr = stock_risk(channel)
        stock_risk_data = sr.get('data', {}) if isinstance(sr, dict) else sr
    except Exception:
        stock_risk_data = []
    # stock-overview（缺货列表）
    try:
        from app.api.routes.inventory import stock_overview
        so = stock_overview(channel=channel, db=db)
        so_data = so.get('data', {}) if isinstance(so, dict) else so
    except Exception:
        so_data = {"items": [], "out_of_stock_count": 0, "low_stock_count": 0, "total": 0}
    # bc(京东主体) 合计缺货 SKU: 同一 SKU 在 B+C 可用合计=0 才算缺货(bbcc 全盘口径,
    # 与 health.bc 的 SKU 级合计判断一致; 健康卡 bc 视图的缺货列表用它)
    bc_out = []
    try:
        _conn = sqlite3.connect(DB_PATH)
        _bc_rows = _conn.execute(
            "SELECT sku, MAX(product_name) FROM inventory "
            "WHERE channel=? AND warehouse_type IN ('platform','platform_b') "
            "GROUP BY sku HAVING SUM(available_qty) <= 0 ORDER BY sku LIMIT 100", (channel,)).fetchall()
        bc_out = [{"sku": str(r[0]), "product_name": str(r[1] or r[0]), "warehouse_type": "bc"} for r in _bc_rows]
        _conn.close()
    except Exception:
        bc_out = []
    return ok({"alerts": alerts, "alertCounts": alert_counts_data, "stockRisk": stock_risk_data,
               "stockOverview": so_data, "bcOutOfStock": bc_out})


@router.get("/stock-risk")
def stock_risk(channel: str = 'jd', full: int = 0):
    now = time.time()
    # 读取数据库版本号（跨进程缓存失效）
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("SELECT value FROM replenishment_config WHERE key='_cache_version' AND channel='jd'")
        row = cur.fetchone()
        db_ver = int(row[0]) if row else 0
        conn.close()
    except Exception as e:
        import logging; logging.warning(f"[dash] read db version error: {e}")
        db_ver = 0
    cached = _stock_risk_cache.get(channel)
    if cached and cached.get('ver') == db_ver and now - cached['ts'] < _STOCK_CACHE_TTL:
        # full=1: 弹窗显示完整列表(缓存存全量 _all/bcAll/cAll/ownAll)
        _d = cached['data']
        if full:
            _d = dict(_d)
            _d['items'] = _d.get('_all', []) or _d.get('items', [])
            _d['bcItems'] = _d.get('bcAll', []) or _d.get('bcItems', [])
            _d['cItems'] = _d.get('cAll', []) or _d.get('cItems', [])
            _d['ownItems'] = _d.get('ownAll', []) or _d.get('ownItems', [])
            _d.pop('_all', None); _d.pop('bcAll', None); _d.pop('cAll', None); _d.pop('ownAll', None)
        else:
            # 非 full: 必须剥离全量列表, 否则响应数MB截断(曾致 bcTotal=0 假象)
            _d = dict(_d)
            _d.pop('_all', None); _d.pop('bcAll', None); _d.pop('cAll', None); _d.pop('ownAll', None)
        return ok(_d)
    """
    濒临断货 TOP 10 — 日销复用三窗口融合值，参考补货建议计算逻辑

    B 仓维度（BBCC）：
      1. 前置期 = b_to_c_days + c_safety_days
      2. C仓缺口 = max(融合日销×前置期 - C仓可用 - C仓在途, 0)
      3. 缺口 > 0 → B仓每天需调拨 = 缺口/前置期 → B仓可撑天数
      缺口 ≤ 0 → C仓库存充足，B仓不纳入风险

    C 仓维度（传统）：
      C仓可用 / 融合日销，可用 < 安全线 且 > 0
    """
    db = get_db()
    now = datetime.now(timezone.utc)

    # 读取补货参数
    cfg_rows = db.table("replenishment_config").select("*").eq("channel", channel).execute().data or []
    cfg = {r['key']: r['value'] for r in cfg_rows}
    b_to_c = int(cfg.get('b_to_c_days', '3'))
    c_safety = int(cfg.get('c_safety_days', '0'))
    bbcc_lead = b_to_c + c_safety

    # 原始 SQL 只取所需字段(替代 ORM select(*) 17000行全字段转dict——PA慢磁盘慢)
    _conn = get_conn()
    _inv_rows = _conn.execute("SELECT sku, warehouse_type, warehouse, available_qty, in_transit_qty, safety_qty, product_name FROM inventory WHERE channel=?", (channel,)).fetchall()
    inv = [{"sku": r[0], "warehouse_type": r[1] or '', "warehouse": r[2] or '', "available_qty": r[3] or 0,
            "in_transit_qty": r[4] or 0, "safety_qty": r[5] or 0, "product_name": r[6] or r[0]} for r in _inv_rows]
    _prod_rows = _conn.execute("SELECT sku, barcode, product_name FROM products WHERE deleted_at='' AND channel=?", (channel,)).fetchall()
    products = {str(r[0]): {"sku": str(r[0]), "barcode": r[1] or '', "product_name": r[2] or ''} for r in _prod_rows}
    sku_barcode_map = {sku: p['barcode'] for sku, p in products.items()}

    # 统一数据源：快照+当天，不用 orders 全表扫描
    # 优化: calc_sales_multi 一次遍历算 7/14/28 三窗口(替代3次calc_sales_from_daily) 
    #      + load_daily_sales 只加载库存 SKU(减少快照读取)
    from app.core.sales_utils import load_daily_sales_grouped, calc_sales_multi, rolling_predict
    all_skus = set(sku_barcode_map.keys()) | {i.get('sku','') for i in inv if i.get('sku')}
    _skus_list = list(all_skus) if all_skus else None
    # 双口径日销: by_sku(全盘合计, BBCC/BC维度) + by_sku_wh(逐仓, 传统C仓维度)
    daily28_all, daily28_wh = load_daily_sales_grouped(28, db, sku_barcode_map=sku_barcode_map, channel=channel, skus=_skus_list)
    _multi = calc_sales_multi(daily28_all, windows=[7, 14, 28])
    s7, s14, s28 = _multi[7], _multi[14], _multi[28]
    fused = {}
    for sku in all_skus:
        s7v = s7.get(sku, 0) or s7.get(f"{sku}|{sku_barcode_map.get(sku,'')}", 0)
        s14v = s14.get(sku, 0) or s14.get(f"{sku}|{sku_barcode_map.get(sku,'')}", 0)
        s28v = s28.get(sku, 0) or s28.get(f"{sku}|{sku_barcode_map.get(sku,'')}", 0)
        fused[sku] = rolling_predict(s7v, s14v, s28v)
    # 逐仓融合日销(传统多仓口径): 该 SKU×仓 的三窗口融合
    fused_wh = {}
    _whm = calc_sales_multi(daily28_wh, windows=[7, 14, 28])
    _ws7, _ws14, _ws28 = _whm[7], _whm[14], _whm[28]
    for wk in daily28_wh:
        base = wk.rsplit('|', 1)[0]  # key|wh → key
        sku = base.split('|')[0]
        v7 = _ws7.get(wk, 0) or _ws7.get(base, 0)
        v14 = _ws14.get(wk, 0) or _ws14.get(base, 0)
        v28 = _ws28.get(wk, 0) or _ws28.get(base, 0)
        fused_wh[wk] = rolling_predict(v7, v14, v28)
    # 全国 C 仓合计日销(BBCC 口径, 与传统一致): 仅 platform 仓名的逐仓日销累加——
    # 曾用全渠道(含 B/自有仓销量, 会高估 C 仓需求)
    _c_whs = set(r[0] for r in _conn.execute("SELECT DISTINCT warehouse FROM inventory WHERE channel=? AND warehouse_type='platform'", (channel,)).fetchall() if r[0])
    # daily28_wh = {key|wh: {date:qty}} 复合 key —— 拆出仓名合并 C 仓日销
    daily28_c = {}
    for wk, wd in daily28_wh.items():
        _base = wk.rsplit('|', 1)[0]
        _w = wk.rsplit('|', 1)[1]
        if _w in _c_whs:  # 只算明确C仓名——源头已杜绝无仓订单(seed只落C仓/清洗未映射预警), 未知归属不入BBCC需求
            _m = daily28_c.setdefault(_base, {})
            for _dd, _qq in wd.items():
                _m[_dd] = _m.get(_dd, 0) + _qq
    fused_c = {}
    if daily28_c:
        _cm = calc_sales_multi(daily28_c, windows=[7, 14, 28])
        _cs7, _cs14, _cs28 = _cm[7], _cm[14], _cm[28]
        for _k in daily28_c:
            fused_c[_k] = rolling_predict(_cs7.get(_k, 0), _cs14.get(_k, 0), _cs28.get(_k, 0))

    # 分类型汇总库存
    c_stock = {}   # C 仓：按 (sku, warehouse) 分
    c_total = {}   # C 仓：按 sku 汇总（可用+在途）
    b_stock = {}   # B 仓：按 sku 汇总
    bc_total = {}  # B+C 合计（bbcc 全盘视角，按 sku）
    own_stock = {} # 自有/三方仓：按 sku 汇总(传统模式断货预警 own 维度)

    for i in inv:
        wt = i.get("warehouse_type", "")
        sku = i.get("sku", "")
        if not sku: continue
        qty = int(i.get("available_qty", 0) or 0)
        tty = int(i.get("in_transit_qty", 0) or 0)
        safety = int(i.get("safety_qty", 0) or 0)
        pname = i.get("product_name", "")

        if wt == "own":
            if sku not in own_stock:
                own_stock[sku] = {"available": 0, "safety": 0, "transit": 0, "product_name": pname, "warehouse": i.get("warehouse", "")}
            own_stock[sku]["available"] += qty
            own_stock[sku]["safety"] += safety
            own_stock[sku]["transit"] += tty

        if wt in ("platform", "platform_b"):
            if sku not in bc_total:
                bc_total[sku] = {"available": 0, "safety": 0, "transit": 0, "product_name": pname}
            bc_total[sku]["available"] += qty
            bc_total[sku]["safety"] += safety
            # 在途: C 仓在途 + B仓在途(调拨/运输中) 合计(bbcc 全盘可支撑)
            bc_total[sku]["transit"] += tty

        if wt == "platform_b":
            if sku not in b_stock:
                b_stock[sku] = {"available": 0, "product_name": pname}
            b_stock[sku]["available"] += qty

        elif wt == "platform":
            key = (sku, i.get("warehouse", ""))
            if key not in c_stock:
                c_stock[key] = {"available": 0, "safety": 0, "transit": 0, "warehouse": i.get("warehouse", ""), "product_name": pname}
            c_stock[key]["available"] += qty
            c_stock[key]["safety"] += safety
            c_stock[key]["transit"] += tty

            if sku not in c_total:
                c_total[sku] = {"available": 0, "transit": 0}
            c_total[sku]["available"] += qty
            c_total[sku]["transit"] += tty

    result = []

    # ── B 仓维度（BBCC）──
    for sku, st in b_stock.items():
        b_avail = st["available"]
        if b_avail <= 0: continue
        _ck = f"{sku}|{sku_barcode_map.get(sku,'')}" if sku_barcode_map and sku_barcode_map.get(sku) else sku
        ds = fused_c.get(_ck, 0) or fused_c.get(sku, 0)  # 全国C仓(+空归属)日销——own/B仓销量不计入BBCC需求, 严格口径
        if ds <= 0: continue
        c_avail = c_total.get(sku, {}).get("available", 0)
        c_transit = c_total.get(sku, {}).get("transit", 0)
        c_gap = max(round(ds * bbcc_lead - c_avail - c_transit), 0)
        if c_gap <= 0: continue
        daily_need = c_gap / bbcc_lead
        days_left = round(b_avail / daily_need, 1) if daily_need > 0 else 999
        result.append({
            "sku": sku,
            "barcode": products.get(sku, {}).get("barcode", ""),
            "product_name": products.get(sku, {}).get("product_name", st["product_name"]),
            "warehouse": "B仓", "type": "B",
            "available_qty": b_avail,
            "daily_sales": round(ds, 1),
            "days_to_empty": days_left,
            "c_gap": c_gap, "c_avail": c_avail, "c_transit": c_transit,
        })

    # ── C 仓维度（传统多仓: 逐仓日销）──
    for (sku, wh), st in c_stock.items():
        avail = st["available"]
        safety = st["safety"]
        if avail <= 0 or avail >= safety: continue
        # 传统多仓: 用该 SKU×仓 的逐仓日销(曾用全渠道合计日销, 会因他仓销量高估可撑天数)
        _bkey = sku_barcode_map.get(sku, '')
        ds = fused_wh.get(f"{sku}|{_bkey}|{wh}", 0) or fused_wh.get(f"{sku}|{wh}", 0) or fused.get(sku, 0)
        if ds <= 0: continue
        days_left = round(avail / ds, 1)
        result.append({
            "sku": sku,
            "barcode": products.get(sku, {}).get("barcode", ""),
            "product_name": products.get(sku, {}).get("product_name", st["product_name"]),
            "warehouse": wh, "type": "C",
            "available_qty": avail,
            "daily_sales": round(ds, 1),
            "days_to_empty": days_left,
        })

    # ── BC 合计维度（bbcc 全盘, 对齐库存卡 bc tab 口径）──
    # 同一 SKU 在 B+C 可用合计判断: 合计可用 < 安全线 或 =0 且 有日销 → 濒临断货风险
    bc_items = []
    for sku, st in bc_total.items():
        avail = st["available"]
        safety = st["safety"]
        _ck = f"{sku}|{sku_barcode_map.get(sku,'')}" if sku_barcode_map and sku_barcode_map.get(sku) else sku
        ds = fused_c.get(_ck, 0) or fused_c.get(sku, 0)  # 全国C仓(+空归属)日销——own/B仓销量不计入BBCC需求, 严格口径
        if ds <= 0:
            continue
        if avail <= 0 or avail < safety:
            days_left = round(avail / ds, 1) if ds > 0 else 999
            bc_items.append({
                "sku": sku,
                "barcode": products.get(sku, {}).get("barcode", ""),
                "product_name": products.get(sku, {}).get("product_name", st["product_name"]),
                "warehouse": "BC", "type": "BC",
                "available_qty": avail,
                "daily_sales": round(ds, 1),
                "days_to_empty": days_left,
                "b_avail": b_stock.get(sku, {}).get("available", 0),
                "c_avail": c_total.get(sku, {}).get("available", 0),
                "transit": st["transit"],
            })
    bc_items.sort(key=lambda x: x["days_to_empty"])

    # ── 自有/三方仓维度（传统模式: 集货仓/三方仓 缺货风险, 由采购维度管控）──
    # own 仓服务于整体销售(采购按系统总库存补), 可撑天数 = own可用 / 全渠道日销
    own_items = []
    for sku, st in own_stock.items():
        avail = st["available"]
        safety = st["safety"]
        ds = fused.get(sku, 0)
        if avail <= 0 or (safety > 0 and avail < safety):
            days_left = round(avail / ds, 1) if ds > 0 else 999
            own_items.append({
                "sku": sku,
                "barcode": products.get(sku, {}).get("barcode", ""),
                "product_name": products.get(sku, {}).get("product_name", st["product_name"]),
                "warehouse": st.get("warehouse", ""), "type": "OWN",
                "available_qty": avail,
                "daily_sales": round(ds, 1),
                "days_to_empty": days_left,
            })
    own_items.sort(key=lambda x: x["days_to_empty"])

    result.sort(key=lambda x: x["days_to_empty"])
    # 全量计数(完整性): 列表截断 TOP10, 但 total/紧急/警告 必须是全量——卡上大数字不得读截断数
    # C 仓独立维度(传统多仓): result 混排 B(BBCC) + C(传统) 项, individual 传统模式只应显示 C 仓
    _c_items = [x for x in result if x.get("type") == "C"]
    _total = len(result)
    _crit = sum(1 for x in result if x.get("days_to_empty", 999) < 3)
    _warn = sum(1 for x in result if 3 <= x.get("days_to_empty", 999) < 7)
    _ct_total = len(_c_items)
    _ct_crit = sum(1 for x in _c_items if x.get("days_to_empty", 999) < 3)
    _ct_warn = sum(1 for x in _c_items if 3 <= x.get("days_to_empty", 999) < 7)
    _bc_total = len(bc_items)
    _bc_crit = sum(1 for x in bc_items if x.get("days_to_empty", 999) < 3)
    _bc_warn = sum(1 for x in bc_items if 3 <= x.get("days_to_empty", 999) < 7)
    _own_total = len(own_items)
    _own_crit = sum(1 for x in own_items if x.get("days_to_empty", 999) < 3)
    _own_warn = sum(1 for x in own_items if 3 <= x.get("days_to_empty", 999) < 7)
    # 缓存保存全量 result/bcAll/cAll/ownAll(弹窗 full=1 用), 常规返回 top10
    _payload = {"items": result[:10], "total": _total, "critical": _crit, "warning": _warn, "_all": result,
                "bcItems": bc_items[:10], "bcTotal": _bc_total, "bcCritical": _bc_crit, "bcWarning": _bc_warn, "bcAll": bc_items,
                "cItems": _c_items[:10], "cTotal": _ct_total, "cCritical": _ct_crit, "cWarning": _ct_warn, "cAll": _c_items,
                "ownItems": own_items[:10], "ownTotal": _own_total, "ownCritical": _own_crit, "ownWarning": _own_warn, "ownAll": own_items}
    _stock_risk_cache[channel] = {'data': _payload, 'ts': time.time(), 'ver': db_ver}
    _ret = dict(_payload); _ret.pop('_all', None); _ret.pop('bcAll', None); _ret.pop('cAll', None); _ret.pop('ownAll', None)
    return ok(_ret)
"""dashboard 濒临断货(stock-risk)计算模块(2026-10-11 拆分——dashboard.py 减负)
函数: _rule_params(规则参数) / _grade_risk(三级分级纯函数) / _stock_risk(主计算)
依赖: _hourly_accel 仍在 dashboard(函数内延迟 import——防模块级循环)
"""
from datetime import datetime, timedelta, timezone

from db import query, one, execute
from routes.common import try_err




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
    except Exception as _e:
            try_err('dashboard', '静默降级', _e)
    return {}

def _grade_risk(adj_dos, buffer, lit, orange_slack=1.0, buffer_orange=1.2, buffer_yellow=1.0):
    """濒临断货三级分级(纯函数, P2 单测面; 供应链时间线优先):
    red=可售天数击穿补货周期 / orange=逼近周期且缓冲破位 / yellow=缓冲破位但时间尚够"""
    if adj_dos <= lit:
        return True, "red"
    if adj_dos <= lit + orange_slack and buffer <= buffer_orange:
        return True, "orange"
    if buffer <= buffer_yellow:
        return True, "yellow"
    return False, None
def _stock_risk(channel, full: int = 0):
    from routes.dashboard import _hourly_accel  # 延迟 import(防模块级循环)
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
        except Exception as _e:
                try_err('dashboard', '静默降级', _e)
    # P1: 需求调整因子 —— 活动系数(season_config, 补货/采购已用) + 当天小时流速加速(加速判定)
    try:
        from routes.replenishment import _season_factor, safety_line
        factor_trad = _season_factor(channel, "traditional")
        factor_bbcc = _season_factor(channel, "bbcc")
    except Exception as _e:
        try_err("dash", "season_factor 加载降级", _e)
        factor_trad = factor_bbcc = 1.0
    try:
        from datetime import datetime as _dt, timezone as _tz
        accel = _hourly_accel(channel, _dt.now(_tz.utc), ratio=accel_ratio, min_qty=accel_min_qty)
    except Exception as _e:
        try_err("dash", "hourly_accel 计算降级", _e)
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
        return _grade_risk(adj_dos, buffer, lit, orange_slack, buffer_orange, buffer_yellow)

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
        buffer = avail / max(max(safety_line(ds, lit_trad), _ss_dyn(sku, lit_trad)), 1)
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
        buffer = avail / max(max(safety_line(ds, lit_bbcc), _ss_dyn(sku, lit_bbcc)), 1)
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
        buffer = avail / max(max(safety_line(ds, lit_trad), _ss_dyn(sku, lit_trad)), 1)
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

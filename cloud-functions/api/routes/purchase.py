"""原生采购/处置路由(方案 B): purchase-orders CRUD + insights/purchase + 滞销处置建议"""

import json
import time as _time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi import Request

from db import query, one, execute, executemany
from routes.common import ok, fail, traced
from biz.sales import load_daily_sales_grouped, load_daily_sales, calc_sales_multi, rolling_predict

router = APIRouter(tags=["purchase"])

from routes.analysis_cache import register as _register_cache
_register_cache(lambda: _purchase_cache.clear())


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
    from routes.analysis_cache import invalidate_all
    invalidate_all()
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
    from routes.analysis_cache import invalidate_all
    invalidate_all()
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
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok({})


# ── 采购建议(insights/purchase) ─────────────────────────────────────────
_purchase_cache = {}
_PURCHASE_TTL = 300


@router.get("/insights/purchase")
@traced
def purchase_suggestions(days: int = 28, mode: str = "bbcc", channel: str = "jd",
                         search: str = ""):
    """采购建议(60s TTL 缓存, 搜索在缓存后过滤——降 RU): 系统总库存+供应商级参数+目标周转+采购告警"""
    _key = "%s|%s" % (channel, mode)
    _c = _purchase_cache.get(_key)
    if _c and _time.time() - _c[0] < _PURCHASE_TTL:
        _all = _c[1]
        if search:
            _sq = search.lower()
            _all = [r for r in _all if _sq in str(r.get("sku", "")).lower()
                    or _sq in str(r.get("product_name", "")).lower()
                    or _sq in str(r.get("barcode", "")).lower()]
        return ok({"suggestions": _all})
    from biz.sales import load_daily_sales, calc_sales_multi
    now = datetime.now(timezone.utc)

    raw = {}
    for r in query("SELECT `key`, value FROM replenishment_config WHERE channel=%s OR channel=''",
                   [channel]):
        raw[r.get("key") or ""] = r.get("value") or ""
    purchase_lead_time = int(float(raw.get("purchase_lead_days") or 0))
    moq_default = int(float(raw.get("moq") or 0))
    purchase_safety_days = float(raw.get("purchase_safety_days") or 0)
    target_turn = int(float(raw.get("max_turnover_days") or 0))
    # 活动系数
    active_factor = 1.0
    try:
        sv = json.loads(raw.get("season_config_%s" % mode) or "[]")
        for s in (sv or []):
            if isinstance(s, dict) and s.get("enabled") and float(s.get("factor", 1.0)) > active_factor:
                active_factor = float(s["factor"])
    except Exception:
        pass

    products = {}
    for p in query("SELECT sku, product_name, barcode, brand, store, category, box_qty, price, "
                   "supplier_code FROM products WHERE (deleted_at IS NULL OR deleted_at='') "
                   "AND channel=%s", [channel]):
        products[p.get("sku")] = p
    if not products:
        return ok({"suggestions": []})

    # 日销 14+28 双窗口 → fused(趋势加权)
    daily = load_daily_sales(days, channel, skus=set(products.keys()))
    multi = calc_sales_multi(daily, windows=[14, 28])
    s14m, s28m = multi[14], multi[28]
    fused = {}
    for sku in set(list(s14m) + list(s28m)):
        a, b = s14m.get(sku, 0), s28m.get(sku, 0)
        if a > b * 1.15:
            w14, w28 = 0.55, 0.45
        elif a < b * 0.85:
            w14, w28 = 0.35, 0.65
        else:
            w14, w28 = 0.20, 0.80
        fused[sku] = round(a * w14 + b * w28, 1)

    # 系统总库存(own/plat/B 口径: B 仓仅 jd+bbcc 参与链路)
    inv = {}
    for i in query("SELECT sku, warehouse_type, warehouse, available_qty, in_transit_qty, "
                   "safety_qty, safety_days FROM inventory WHERE channel=%s", [channel]):
        s = i.get("sku")
        wt = i.get("warehouse_type") or "platform"
        if wt == "platform_b" and (channel != "jd" or mode != "bbcc"):
            continue
        st = inv.setdefault(s, {"available": 0, "transit": 0, "safety": 0, "safety_days": 0,
                                "own_avail": 0, "own_transit": 0, "plat_avail": 0,
                                "plat_transit": 0, "b_transit": 0, "b_avail": 0,
                                "own_warehouse": ""})
        qty = int(i.get("available_qty") or 0)
        tty = int(i.get("in_transit_qty") or 0)
        st["available"] += qty
        st["transit"] += tty
        st["safety"] += int(i.get("safety_qty") or 0)
        sd = float(i.get("safety_days") or 0)
        if sd > st["safety_days"]:
            st["safety_days"] = sd
        if wt == "platform_b":
            st["b_avail"] += qty
            st["b_transit"] += tty
        elif wt == "own":
            st["own_avail"] += qty
            st["own_transit"] += tty
            if not st["own_warehouse"]:
                st["own_warehouse"] = i.get("warehouse", "")
        else:
            st["plat_avail"] += qty
            st["plat_transit"] += tty

    # 供应商特定参数(前置期/安全天数/MOQ 按供应商独立, 回退全局)
    _sup_params = {}

    def _sup_param(sup_code, key, fallback):
        if not sup_code:
            return fallback
        cache = _sup_params.setdefault(sup_code, {})
        if key not in cache:
            try:
                cache[key] = int(float(raw.get("%s_%s" % (key, sup_code), str(fallback))))
            except Exception:
                cache[key] = fallback
        return cache[key]

    result = []
    for sku, st in inv.items():
        ds = round(fused.get(sku, 0) * active_factor, 1)
        if ds <= 0:
            continue
        sys_total = st["available"] + st["transit"]
        prod = products.get(sku, {})
        _sup = prod.get("supplier_code") or ""
        _lead = _sup_param(_sup, "purchase_lead_days", purchase_lead_time)
        _safe_days = st["safety_days"] if st["safety_days"] > 0 else _sup_param(
            _sup, "purchase_safety_days", purchase_safety_days)
        eff_safety = round(ds * _safe_days)
        purchase_qty = max(round(ds * _lead) + eff_safety - sys_total, 0)
        box_qty = int(prod.get("box_qty") or 1)
        actual_purchase = (purchase_qty + box_qty - 1) // box_qty * box_qty if purchase_qty > 0 else 0
        days_to_empty = round(st["available"] / ds, 1) if ds > 0 else 999
        after_stock = st["own_avail"] + st["own_transit"] + actual_purchase
        after_turnover = round(after_stock / ds, 1) if ds > 0 else 999
        c_consume = round(ds * _lead)
        note = ""
        if purchase_qty > 0:
            note = "消耗%d+安全%d -库存%d =%d" % (c_consume, eff_safety, int(sys_total), purchase_qty)
            if box_qty > 1:
                note += " · 箱规%d件, 实购%d件(%d箱)" % (box_qty, actual_purchase, actual_purchase // box_qty)
            if target_turn > 0:
                note += " · 补后周转%d天%s" % (after_turnover,
                                            " > 目标%d天" % target_turn if after_turnover > target_turn
                                            else " < 目标%d天" % target_turn)
        result.append({
            "sku": sku, "barcode": prod.get("barcode", ""),
            "product_name": prod.get("product_name") or sku, "brand": prod.get("brand", ""),
            "store": prod.get("store", ""), "warehouse": st["own_warehouse"],
            "category": prod.get("category", ""),
            "sys_available": st["available"], "sys_transit": st["transit"], "sys_total": sys_total,
            "own_available": st["own_avail"], "own_transit": st["own_transit"],
            "b_transit": st["b_transit"], "plat_available": st["plat_avail"],
            "plat_transit": st["plat_transit"], "b_available": st["b_avail"],
            "safety_qty": st["safety"], "daily_sales": ds,
            "daily_sales_14": round(s14m.get(sku, 0), 1), "daily_sales_28": round(s28m.get(sku, 0), 1),
            "daily_sales_60": round(fused.get(sku, 0), 1),
            "supplier_code": _sup,
            "purchase_qty": purchase_qty, "box_qty": box_qty, "actual_purchase": actual_purchase,
            "after_stock": after_stock, "after_turnover": after_turnover,
            "target_turnover": target_turn, "days_to_empty": days_to_empty,
            "note": note if note else "库存充足",
        })

    # 供应商 MOQ 聚合: 同供应商采购量合计 < MOQ 时按占比放大到起订量
    _sup_groups = {}
    for _r in result:
        _sup = _r.get("supplier_code") or ""
        if not _sup:
            continue
        g = _sup_groups.setdefault(_sup, {"moq": _sup_param(_sup, "moq", moq_default),
                                          "total_raw": 0, "skus": []})
        g["total_raw"] += _r["purchase_qty"]
        g["skus"].append(_r)
    for _sup, g in _sup_groups.items():
        if g["total_raw"] > 0 and g["total_raw"] < g["moq"]:
            _ratio = g["moq"] / g["total_raw"]
            for _r in g["skus"]:
                _old = _r["purchase_qty"]
                _r["purchase_qty"] = max(round(_r["purchase_qty"] * _ratio), 0)
                if _r["purchase_qty"] > 0:
                    _box = _r.get("box_qty") or 1
                    _r["actual_purchase"] = (_r["purchase_qty"] + _box - 1) // _box * _box
                _r["note"] = "%s · 供应商%s起订%d件, 该供应商合计%d件不足, 按占比%d/%d提升至%d件" % (
                    _r.get("note", ""), _sup, g["moq"], g["total_raw"], _old,
                    g["total_raw"], _r["purchase_qty"])

    # 采购告警 purchase_need(批量: 需采购且可撑<14天 → active; 无需 → closed)
    try:
        existing = set()
        for r in query("SELECT related_sku FROM alerts WHERE alert_type='purchase_need' "
                       "AND status='active' AND channel=%s", [channel]):
            existing.add(r.get("related_sku"))
        ins, upd = [], []
        for _r in result:
            sku = _r.get("sku")
            if _r.get("purchase_qty", 0) > 0 and _r.get("days_to_empty", 999) < 14 and sku not in existing:
                ins.append(("需采购: %s" % _r.get("product_name"),
                            "可用%d件, 建议采购%d件, 可撑%d天" % (_r.get("sys_available") or 0,
                                                            _r.get("actual_purchase") or 0,
                                                            _r.get("days_to_empty") or 0),
                            sku, channel))
            elif _r.get("purchase_qty", 0) == 0 and sku in existing:
                upd.append(sku)
        for i in range(0, len(ins), 100):
            executemany("INSERT INTO alerts(alert_type, title, description, severity, source, "
                        "related_sku, status, channel) VALUES('purchase_need',%s,%s,'warning',"
                        "'purchase_engine',%s,'active',%s)", ins[i:i + 100])
        if upd:
            ph = ",".join(["%s"] * len(upd))
            execute("UPDATE alerts SET status='closed' WHERE alert_type='purchase_need' "
                    "AND related_sku IN (%s) AND status='active' AND channel=%s"
                    % (ph, channel), upd + [channel])
    except Exception:
        pass

    result.sort(key=lambda x: x["days_to_empty"])
    _purchase_cache[_key] = (_time.time(), result)
    if search:
        sq = search.lower()
        result = [r for r in result if sq in str(r.get("sku", "")).lower()
                  or sq in str(r.get("product_name", "")).lower()
                  or sq in str(r.get("barcode", "")).lower()]
    return ok({"suggestions": result})


# ── 滞销处置建议(disposal-suggestions) ──────────────────────────────────
@router.get("/insights/disposal-suggestions")
@traced
def disposal_suggestions(channel: str = "jd", page: int = 0, page_size: int = 0,
                         search: str = ""):
    """SKU×仓库粒度滞销处置建议(多因素)
    ① 临期(best_before ≤ 品类临期线) → black 紧急
    ② B仓超免费期(purchase_orders.arrival_date 起算) → 成本压力
    ③ 滞销主判据(品类滞销线 slow-cats / 观察线) → yellow / observe
    ④ 升级: yellow + (B仓超期 或 资金占用≥阈值) → red
    """
    from biz.sales import load_daily_sales, calc_sales_multi
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    def _cfg_number(key, default):
        row = one("SELECT value FROM replenishment_config WHERE `key`=%s AND channel=%s",
                  [key, channel])
        try:
            return int((row or {}).get("value") or default)
        except Exception:
            return default

    def _cfg_list(key, default):
        row = one("SELECT value FROM replenishment_config WHERE `key`=%s AND channel=%s",
                  [key, channel])
        if row and row.get("value"):
            try:
                d = json.loads(row["value"])
                # 空数组视为"未自定义"→ 返回内置默认(避免丢个护家清等品类配置, 全按食品线误判)
                if isinstance(d, list) and d:
                    return d
            except Exception:
                pass
        return default

    cat_cfg = _cfg_list("slow_cats", [
        {"name": "食品", "slow_days": 30, "shelf_months": 3,
         "cats": "酱油,酱料,调味汁,食用油,醋,料酒,蚝油,芝麻油,辣椒酱,拌面酱,老抽,生抽,"
                 "陈醋,香醋,白醋,米醋,花椒油,藤椒油,辣椒油,芥末油,番茄酱,甜辣酱,沙拉酱,"
                 "芝麻酱,花生酱,豆瓣酱,豆豉,腐乳,糟卤,鱼露,咖喱块,咖喱粉,五香粉,孜然粉,"
                 "花椒粉,辣椒粉,胡椒粉,十三香,卤料包,炖肉料,鸡精,味精,白糖,冰糖,红糖,"
                 "麦芽糖,蜂蜜,黄酒,米酒,薯片,虾条,爆米花,坚果,瓜子,花生,饼干,威化,"
                 "巧克力,糖果", "enabled": True},
        {"name": "个护家清", "slow_days": 60, "shelf_months": 6,
         "cats": "洗衣液,洗洁精,洗手液,消毒液,纸巾,湿巾,垃圾袋,保鲜膜,保鲜袋,收纳盒",
         "enabled": True}])
    b_free = _cfg_number("b_free_days", 15)
    fund_threshold = _cfg_number("slow_fund_threshold", 10000)

    products = {}
    for r in query("SELECT sku, product_name, price, volume, category, best_before, brand "
                   "FROM products WHERE (deleted_at IS NULL OR deleted_at='') AND channel=%s",
                   [channel]):
        products[r.get("sku")] = r
    # 最后销售日(快照 + 近90天订单)
    last_order = {}
    for r in query("SELECT sku, MAX(date) AS m FROM daily_sales_snapshot "
                   "WHERE channel=%s GROUP BY sku", [channel]):
        last_order[str(r.get("sku"))] = str(r.get("m") or "")[:10]
    cutoff90 = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    for r in query("SELECT sku, MAX(ordered_at) AS m FROM orders "
                   "WHERE channel=%s AND ordered_at>=%s AND (deleted_at IS NULL OR deleted_at='') "
                   "GROUP BY sku", [channel, cutoff90 + " 00:00:00"]):
        mx = str(r.get("m") or "")[:10]
        if mx and mx > last_order.get(str(r.get("sku")), ""):
            last_order[str(r.get("sku"))] = mx
    # 28 天日销
    daily_map = {}
    if products:
        multi = calc_sales_multi(load_daily_sales(28, channel, skus=set(products.keys())),
                                 windows=[28])
        daily_map = multi[28]
    # B 仓入库批次(到仓日期)
    b_arrival = {}
    for r in query("SELECT sku, arrival_date FROM purchase_orders "
                   "WHERE channel=%s AND arrival_date IS NOT NULL AND arrival_date!=''", [channel]):
        try:
            b_arrival[str(r.get("sku"))] = datetime.strptime(str(r.get("arrival_date"))[:10], "%Y-%m-%d")
        except Exception:
            pass
    # 已处置(30 天内)
    disposed = {}
    cutoff30 = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    for r in query("SELECT sku, warehouse, action FROM disposal_records "
                   "WHERE channel=%s AND created_at>=%s", [channel, cutoff30]):
        disposed[(str(r.get("sku")), str(r.get("warehouse") or ""))] = str(r.get("action") or "")
    # 伪滞销线索(近14天补货建议)
    pseudo = set()
    c14 = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    for r in query("SELECT DISTINCT related_sku FROM alerts WHERE channel=%s "
                   "AND alert_type IN ('replenish','rewrite') AND created_at>=%s "
                   "AND related_sku IS NOT NULL AND related_sku!=''", [channel, c14]):
        pseudo.add(str(r.get("related_sku")))

    sug = {"black": "临期紧急处理(促销/退供)", "red": "退货供应商/清仓甩卖",
           "yellow": "补货降量/持续跟踪", "observe": "继续观察/补货降量"}
    items = []
    for r in query("SELECT sku, warehouse, warehouse_type, available_qty FROM inventory "
                   "WHERE channel=%s", [channel]):
        sku = str(r.get("sku") or "")
        wh = str(r.get("warehouse") or "")
        wht = str(r.get("warehouse_type") or "")
        avail = int(r.get("available_qty") or 0)
        if avail <= 0:
            continue
        p = products.get(sku)
        if not p:
            continue
        cat = p.get("category") or ""
        match = None
        for c in cat_cfg:
            if c.get("enabled") is False:
                continue
            if any(w.strip() and w.strip() in cat for w in str(c.get("cats") or "").split(",")):
                match = c
                break
        slow_days = int(match.get("slow_days", 30)) if match else 30
        shelf_m = int(match.get("shelf_months", 3)) if match else 3
        cat_name = (match.get("name") or "未归类") if match else "未归类(默认食品线)"
        ld = last_order.get(sku, "")
        days_zero = 999 if not ld else max((now - datetime.strptime(ld, "%Y-%m-%d")).days, 0)
        daily = float(daily_map.get(sku, 0) or 0)
        turnover = round(avail / daily, 1) if daily > 0 else 999.0
        fund = round(avail * float(p.get("price") or 0), 0)
        reason, level, b_storage = [], None, None
        # ① 临期风险(black 紧急)
        bb = str(p.get("best_before") or "")[:10]
        shelf_days = shelf_m * 30
        if bb:
            try:
                dd = (datetime.strptime(bb, "%Y-%m-%d") - now).days
                if dd <= shelf_days:
                    level = "black"
                    reason.append("距保质期%d天(<%d月临期线)" % (dd, shelf_m) if dd >= 0
                                  else "已过期%d天" % (-dd))
            except Exception:
                pass
        # ② B仓超免费期(仓储费成本)
        if wht == "platform_b" and channel == "jd":
            days_stored = max((now - b_arrival[sku]).days, 0) if sku in b_arrival else 0
            if days_stored > b_free:
                over = days_stored - b_free
                months = max((over + 29) // 30, 1)
                vol_m3 = round(avail * float(p.get("volume") or 0), 3)
                b_storage = {"days_stored": days_stored, "free_days": b_free,
                             "volume_m3": vol_m3, "over_days": over, "billed_months": months}
                reason.append("B仓在库%d天超免费期%d天(约%d计费月, 费率待定)"
                              % (days_stored, over, months))
        # ③ 滞销主判据(观察线 = 滞销线一半, 下限 15)
        try:
            if match and match.get("observe_days") not in (None, "", 0):
                observe_days = max(int(str(match.get("observe_days")).strip()), 1)
            else:
                observe_days = max(slow_days // 2, 15)
        except Exception:
            observe_days = max(slow_days // 2, 15)
        if days_zero >= slow_days:
            if level is None:
                level = "yellow"
            reason.append("%s: %d天未销售(超%d天线)" % (cat_name, days_zero, slow_days))
        elif days_zero >= observe_days:
            if level is None:
                level = "observe"
            reason.append("%s: %d天未销售(接近%d天线, 建议观察)" % (cat_name, days_zero, slow_days))
        else:
            if level is None and b_storage:
                level = "yellow"
            if level is None:
                continue
        # ④ 升级: 滞销 + B仓超期 或 高资金占用 → red
        if level == "yellow" and (b_storage or fund >= fund_threshold):
            level = "red"
            reason.append("有成本压力(仓储费或占用¥%d), 建议尽快处置" % fund)
        if sku in pseudo and level == "red":
            reason.append("近14天有补货建议, 疑似缺货伪滞销, 建议先核实库存")
        items.append({
            "sku": sku, "product_name": p.get("product_name") or sku, "channel": channel,
            "warehouse": wh, "warehouse_type": wht, "category": cat,
            "stock": avail, "turnover_days": turnover, "fund_occupied": fund,
            "daily_sales": round(daily, 1), "days_zero": days_zero,
            "cat_line": cat_name, "slow_days": slow_days,
            "level": level, "reason": reason, "suggestion": sug.get(level, ""),
            "b_storage": b_storage, "best_before": bb, "brand": p.get("brand") or "",
            "disposed": (sku, wh) in disposed, "disposed_action": disposed.get((sku, wh), ""),
        })
    items.sort(key=lambda x: ({"black": 0, "red": 1, "yellow": 2, "observe": 3}.get(x["level"], 9),
                               -x["days_zero"]))
    if search:
        sq = search.lower()
        items = [x for x in items if sq in str(x.get("sku", "")).lower()
                 or sq in str(x.get("product_name", "")).lower()
                 or sq in str(x.get("barcode", "")).lower()]
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
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok({"updated": n})
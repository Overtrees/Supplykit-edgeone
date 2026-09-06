"""原生采购/处置路由(方案 B): purchase-orders CRUD + insights/purchase + 滞销处置建议"""

import json
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
                if isinstance(d, list):
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
    return ok({"updated": n})
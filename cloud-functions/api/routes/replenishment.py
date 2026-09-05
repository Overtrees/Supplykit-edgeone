"""原生补货路由(方案 B): BBCC 两步法 + 传统逐仓(契约与旧 backend 一致)"""
from fastapi import APIRouter

from db import query, one
from routes.common import ok, traced
from biz.sales import load_daily_sales_grouped, calc_sales_multi, rolling_predict

router = APIRouter(tags=["insights"])


@router.get("/insights/replenishment")
@traced
def get_replenishment_suggestions(days: int = 28, source: str = "", mode: str = "bbcc",
                                  channel: str = "jd", page: int = 0, page_size: int = 0,
                                  search: str = ""):
    """补货建议: mode=bbcc(全国一盘棋) / traditional(逐仓)"""
    now_s = "2026-09-05"  # 占位, 由下方实际计算
    cfg = _config(channel, mode)
    products = _products(channel)
    inv = _inventory(channel)
    lead = (int(cfg.get("b_to_c_days", "0")) + int(cfg.get("c_safety_days", "0"))) if mode == "bbcc" \
        else int(cfg.get("lead_time_days", "0"))
    season = _season_factor(channel, mode)

    # 日销: BBCC=全国 C 仓合计(平台仓名), 传统=逐仓
    by_sku, by_sku_wh = load_daily_sales_grouped(60, channel)
    if mode == "bbcc":
        c_whs = {r.get("warehouse") for r in query(
            "SELECT DISTINCT warehouse FROM inventory WHERE channel=%s AND warehouse_type='platform' AND warehouse!=''",
            [channel])}
        daily_c = {}
        for wk, wd in by_sku_wh.items():
            base, wh = wk.rsplit("|", 1)
            if wh in c_whs:
                m = daily_c.setdefault(base, {})
                for d, q in wd.items():
                    m[d] = m.get(d, 0) + q
        daily_28 = daily_c
    else:
        daily_28 = by_sku

    multi = calc_sales_multi(daily_28, windows=[7, 14, 28])
    s7, s14, s28 = multi[7], multi[14], multi[28]
    fused = {}
    for sku in set(list(s7) + list(s14) + list(s28)):
        fused[sku] = rolling_predict(s7.get(sku, 0), s14.get(sku, 0), s28.get(sku, 0))

    suggestions = []
    if mode == "bbcc":
        agg = {}
        b_stock = {}
        b_transit = {}
        for r in inv:
            sku = r.get("sku")
            wt = r.get("warehouse_type")
            qty = int(r.get("available_qty") or 0)
            tty = int(r.get("in_transit_qty") or 0)
            ctt = int(r.get("c_transit") or 0)
            saf = int(r.get("safety_qty") or 0)
            if wt == "platform_b":
                b_stock[sku] = b_stock.get(sku, 0) + qty
                b_transit[sku] = b_transit.get(sku, 0) + tty
            elif wt == "own":
                pass
            else:
                a = agg.setdefault(sku, {"avail": 0, "transit": 0, "c_transit": 0, "safety": 0})
                a["avail"] += qty
                a["transit"] += tty
                a["c_transit"] += ctt
                a["safety"] += saf
        for sku, st in agg.items():
            ds = fused.get(sku, 0) * season
            if ds <= 0:
                continue
            avail, c_transit, transit, safety = st["avail"], st["c_transit"], st["transit"], st["safety"]
            c_gap = max(round(ds * lead - avail - c_transit, 1), 0)
            b_cover = b_stock.get(sku, 0) + b_transit.get(sku, 0)
            b_gap = max(round(c_gap - b_cover, 1), 0)
            prod = products.get(sku, {})
            box = int(prod.get("box_qty") or 1)
            suggested = c_gap
            b_box = ((b_gap + box - 1) // box * box) if b_gap > 0 else 0
            days_to_empty = round(avail / ds, 1) if ds > 0 else 999
            note = "需补货" if suggested > 0 or b_box > 0 else "库存充足"
            suggestions.append({
                "sku": sku, "barcode": prod.get("barcode", ""),
                "product_name": prod.get("product_name", ""), "brand": prod.get("brand", ""),
                "store": prod.get("store", ""), "category": prod.get("category", ""),
                "available_qty": avail, "safety_qty": safety, "in_transit_qty": transit,
                "c_transit": c_transit, "b_transit": b_transit.get(sku, 0),
                "b_stock": b_stock.get(sku, 0), "c_stock": avail, "b_gap": b_gap,
                "daily_sales": round(ds, 1), "daily_sales_7": round(s7.get(sku, 0), 1),
                "daily_sales_14": round(s14.get(sku, 0), 1), "daily_sales_28": round(s28.get(sku, 0), 1),
                "daily_sales_60": round(fused.get(sku, 0), 1),
                "raw_suggested": c_gap, "suggested_qty": suggested,
                "b_suggested": b_box, "b_replenish_raw": b_gap,
                "days_to_empty": days_to_empty, "note": note,
            })
    else:
        # 传统: 逐仓
        wh_sales = {}
        for r in inv:
            if r.get("warehouse_type") != "platform":
                continue
            wh = r.get("warehouse")
            wk = "%s|%s" % (r.get("sku"), wh)
            wd = by_sku_wh.get(wk, {})
            wh_sales[wk] = wd
        _wm = calc_sales_multi({k: v for k, v in wh_sales.items() if v}, windows=[7, 14, 28])
        for r in inv:
            if r.get("warehouse_type") != "platform":
                continue
            sku = r.get("sku")
            wh = r.get("warehouse")
            wk = "%s|%s" % (sku, wh)
            w7 = _wm[7].get(wk, 0)
            w14 = _wm[14].get(wk, 0)
            w28 = _wm[28].get(wk, 0)
            ds = rolling_predict(w7, w14, w28) * season
            avail = int(r.get("available_qty") or 0)
            transit = int(r.get("in_transit_qty") or 0)
            safety = int(r.get("safety_qty") or 0)
            suggested = max(round(ds * lead + safety - avail - transit), 0) if ds > 0 else 0
            prod = products.get(sku, {})
            box = int(prod.get("box_qty") or 1)
            box_qty = ((suggested + box - 1) // box * box) if suggested > 0 else 0
            suggestions.append({
                "sku": sku, "barcode": prod.get("barcode", ""),
                "product_name": prod.get("product_name", ""), "brand": prod.get("brand", ""),
                "store": prod.get("store", ""), "warehouse": wh, "category": prod.get("category", ""),
                "available_qty": avail, "safety_qty": safety, "in_transit_qty": transit,
                "daily_sales": round(ds, 1), "daily_sales_7": round(w7, 1),
                "daily_sales_14": round(w14, 1), "daily_sales_28": round(w28, 1),
                "daily_sales_60": round(fused.get(sku, 0), 1),
                "suggested_qty": box_qty,
                "days_to_empty": round(avail / ds, 1) if ds > 0 else 999,
                "note": "需补货" if box_qty > 0 else "库存充足",
            })

    # 排序: 需补货优先, 缺口大优先
    suggestions.sort(key=lambda s: (-(1 if (s.get("suggested_qty") or 0) > 0 or (s.get("b_suggested") or 0) > 0 else 0),
                                     -(s.get("suggested_qty") or 0), -(s.get("daily_sales") or 0), s.get("sku", "")))
    if search:
        sq = search.lower()
        suggestions = [s for s in suggestions if sq in str(s.get("sku", "")).lower()
                       or sq in str(s.get("product_name", "")).lower()
                       or sq in str(s.get("barcode", "")).lower()]
    if page > 0 and page_size > 0:
        total = len(suggestions)
        return ok({"items": suggestions[(page - 1) * page_size: page * page_size],
                   "total": total, "page": page, "page_size": page_size})
    return ok(suggestions)


def _config(channel, mode):
    rows = query("SELECT `key`, value FROM replenishment_config WHERE channel=%s OR channel=''", [channel])
    cfg = {}
    prefix = "mode_%s_" % mode
    for r in rows:
        k = r.get("key") or ""
        v = r.get("value") or ""
        if k.startswith(prefix):
            cfg[k[len(prefix):]] = v
        elif not k.startswith("mode_"):
            cfg[k] = v
    return cfg


def _season_factor(channel, mode):
    row = one("SELECT value FROM replenishment_config WHERE `key`=%s AND channel=%s",
              ("season_config_%s" % mode, channel))
    if not row or not row.get("value"):
        return 1.0
    try:
        import json
        cfg = json.loads(row["value"])
    except Exception:
        return 1.0
    factor = 1.0
    for s in (cfg or []):
        if isinstance(s, dict) and s.get("enabled") and float(s.get("factor", 1.0)) > factor:
            factor = float(s["factor"])
    return factor


def _products(channel):
    rows = query("SELECT sku, barcode, product_name, brand, store, category, box_qty FROM products "
                 "WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='')", [channel])
    return {r.get("sku"): r for r in rows}


def _inventory(channel):
    return query("SELECT sku, warehouse, warehouse_type, available_qty, in_transit_qty, c_transit, safety_qty "
                 "FROM inventory WHERE channel=%s", [channel])

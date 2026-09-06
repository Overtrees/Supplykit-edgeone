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
            avail, c_transit, transit, safety = st["avail"], st["c_transit"], st["transit"], st["safety"]
            ds7 = round(s7.get(sku, 0), 1)
            ds14 = round(s14.get(sku, 0), 1)
            ds28 = round(s28.get(sku, 0), 1)
            ds = round(fused.get(sku, 0) * season, 1)
            # 安全天数(库存行 safety_days 优先, 回退配置)
            safety_days = float(cfg.get("safety_multiplier") or 0)
            effective_safety = round(ds * safety_days, 1) if ds > 0 else 0
            # C 缺口 = 日销×lead − C可用 − B→C调拨在途(保留1位小数)
            c_gap = max(round(ds * lead - avail - c_transit, 1), 0) if ds > 0 else 0
            b_available = b_stock.get(sku, 0)
            b_in_transit = b_transit.get(sku, 0)
            b_cover = b_available + b_in_transit
            b_gap = max(round(c_gap - b_cover, 1), 0) if c_gap > 0 else 0
            b_ship_days = int(cfg.get("ship_to_b_days") or 0)
            # B 建议补 = B缺口 + 调拨期消耗(日销×(自有→B + 安全天数)), 箱规取整
            b_replenish = round(b_gap + ds * b_ship_days + effective_safety, 1) if b_gap > 0 else 0
            prod = products.get(sku, {})
            box = int(prod.get("box_qty") or 1)
            suggested = c_gap
            b_box = (b_replenish + box - 1) // box * box if b_replenish > 0 else 0
            after_stock = avail + transit + suggested
            after_turnover = round(after_stock / ds, 1) if ds > 0 else 999
            days_to_empty = round(avail / ds, 1) if ds > 0 else 999
            combined_turnover_current = round((avail + transit + b_available) / ds, 1) if ds > 0 else None
            combined_turnover = round((avail + transit + suggested + b_available + b_box) / ds, 1) if ds > 0 else None
            c_turnover = round(avail / ds, 1) if ds > 0 else None
            transit_turnover = round(transit / ds, 1) if ds > 0 else None
            # note: 趋势 + 建议 + 仓储费/周转风险(与 PA 同构)
            t7 = "📈" if ds7 > ds14 * 1.15 else ("📉" if ds7 < ds14 * 0.85 else "➡️")
            t14 = "📈" if ds14 > ds28 * 1.15 else ("📉" if ds14 < ds28 * 0.85 else "➡️")
            trend_text = "近7%s 近14%s" % (t7, t14)
            if ds > 0 and ds < 5 and combined_turnover_current is not None and combined_turnover_current > 90:
                trend_text += " 销量极低，库存积压"
            elif ds7 == 0 and ds14 == 0 and ds28 > 0:
                trend_text += " 持续下行（近14天无销量）"
            elif ds7 > ds14 * 1.15 and ds14 > ds28 * 1.1:
                trend_text += " 持续上行"
            elif ds7 < ds14 * 0.85 and ds14 < ds28 * 0.9:
                trend_text += " 持续下行"
            elif ds7 > ds14 * 1.15:
                trend_text += " 7天抬头"
            elif ds7 < ds14 * 0.85:
                trend_text += " 7天走弱"
            else:
                trend_text += " 平稳"
            parts = []
            if ds > 0:
                parts.append(trend_text)
            if c_gap > 0:
                if b_gap <= 0:
                    parts.append("C建议补%s件(缺口%s,B仓可覆盖)" % (suggested, c_gap))
                else:
                    parts.append("C建议补%s件 · B建议补%s件(缺口%s,调拨消耗%s,箱规%s)"
                                 % (suggested, b_box, b_gap, round(ds * b_ship_days + effective_safety, 1), box))
                if b_in_transit > 0 and b_gap > 0:
                    parts.append("B仓仅%s件需从自有仓调(供应商到B在途%s)" % (b_available, b_in_transit))
                elif b_gap > 0:
                    parts.append("B仓仅%s件需从自有仓调" % b_available)
            if b_gap > 0:
                c_cover = round((avail + transit) / ds, 1) if ds > 0 else 0
                b_idle = max(round(c_cover - b_ship_days, 1), 0)
            else:
                b_idle = 0
            b_free = int(cfg.get("b_free_days") or 15)
            if b_idle > b_free:
                parts.append("🔴 超%s天免费期有仓储费" % b_free)
            elif b_idle > b_free - 5:
                parts.append("⚠️ 接近%s天免费期" % b_free)
            tw90 = int(cfg.get("turnover_warning_90") or 90)
            has_replen = (suggested > 0 or b_box > 0)
            turn_check = combined_turnover if has_replen and combined_turnover is not None else combined_turnover_current
            if turn_check is not None and turn_check > tw90:
                parts.append("🔴 %s%s天超%s天" % ("补后综转" if has_replen else "当前综转", turn_check, tw90))
            elif turn_check is not None and turn_check > tw90 - 15:
                parts.append("⚠️ %s%s天接近%s天" % ("补后综转" if has_replen else "当前综转", turn_check, tw90))
            if ds <= 0:
                if b_available > 0:
                    parts.append("🔴 近30天无销量，B仓库存积压")
                elif avail > 0:
                    parts.append("🔴 近30天无销量，C仓库存积压")
                else:
                    parts.append("⚪ 近30天无销量")
            if not parts:
                parts.append("库存充足")
            note = " · ".join(parts)
            suggestions.append({
                "sku": sku, "barcode": prod.get("barcode", ""),
                "product_name": prod.get("product_name", ""), "brand": prod.get("brand", ""),
                "store": prod.get("store", ""), "category": prod.get("category", ""),
                "available_qty": avail, "safety_qty": safety, "in_transit_qty": transit,
                "c_transit": c_transit, "b_transit": b_in_transit,
                "b_stock": b_available, "c_stock": avail, "b_gap": b_gap,
                "daily_sales": ds, "daily_sales_7": ds7,
                "daily_sales_14": ds14, "daily_sales_28": ds28,
                "daily_sales_60": round(fused.get(sku, 0), 1),
                "raw_suggested": c_gap, "suggested_qty": suggested,
                "b_suggested": b_box, "b_replenish_raw": b_replenish,
                "days_to_empty": days_to_empty, "after_turnover": after_turnover,
                "c_turnover": c_turnover, "transit_turnover": transit_turnover,
                "combined_turnover_current": combined_turnover_current,
                "combined_turnover": combined_turnover,
                "note": note,
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
            after_turnover = round((avail + transit + box_qty) / ds, 1) if ds > 0 else 999
            suggestions.append({
                "sku": sku, "barcode": prod.get("barcode", ""),
                "product_name": prod.get("product_name", ""), "brand": prod.get("brand", ""),
                "store": prod.get("store", ""), "warehouse": wh, "category": prod.get("category", ""),
                "available_qty": avail, "safety_qty": safety, "in_transit_qty": transit,
                "daily_sales": round(ds, 1), "daily_sales_7": round(w7, 1),
                "daily_sales_14": round(w14, 1), "daily_sales_28": round(w28, 1),
                "daily_sales_60": round(fused.get(sku, 0), 1),
                "suggested_qty": box_qty, "after_turnover": after_turnover,
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

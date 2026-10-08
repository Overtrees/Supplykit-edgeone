"""原生补货路由(方案 B): BBCC 两步法 + 传统逐仓(契约与旧 backend 一致)"""
import time as _time
from fastapi import APIRouter, Request

from db import query, one
from routes.common import ok, fail, traced, try_err
from biz.sales import load_daily_sales_grouped, calc_sales_multi, rolling_predict, smooth_promo_spikes

router = APIRouter(tags=["insights"])

from routes.analysis_cache import register as _register_cache, cache_get as _cache_get
_register_cache(lambda: _repl_cache.clear())


_repl_cache = {}
_REPL_TTL = 300
# 需求门控阈值: 日销低于此值(近28天不足 2.8 件)视为无实际需求——无需求时库存不会被消耗,
# 安全线缺口不触发补货(安全线是需求波动的缓冲; 参考 bbcc 无需求不补的驱动语义)
_MIN_DS = 0.1


@router.get("/insights/replenishment")
@traced
def get_replenishment_suggestions(days: int = 28, source: str = "", mode: str = "bbcc",
                                  channel: str = "jd", page: int = 0, page_size: int = 0,
                                  search: str = "", need_only: int = 0):
    """补货建议(300s 共享表缓存全量——TiDB 表跨实例一致, 分页/搜索/need_only 在缓存后处理——降 RU): mode=bbcc/traditional"""
    _key = "repl|%s|%s|%s|%s" % (channel, mode, days, source)
    _all = _cache_get(_key, _REPL_TTL, lambda: _build_repl(channel, mode))
    if need_only:
        _all = [s for s in _all if (s.get("suggested_qty") or 0) > 0 or (s.get("b_suggested") or 0) > 0]
    if search:
        _sq = search.lower()
        _all = [s for s in _all if _sq in str(s.get("sku", "")).lower()
                or _sq in str(s.get("product_name", "")).lower()
                or _sq in str(s.get("barcode", "")).lower()]
    if page > 0 and page_size > 0:
        return ok({"items": _all[(page - 1) * page_size: page * page_size],
                   "total": len(_all), "page": page, "page_size": page_size})
    return ok(_all)


def safety_line(ds, lead):
    """动态安全线(可售天数 ≥ 补货周期): ds × lead

    引用补货参数按模式区分: bbcc lead=b_to_c_days+c_safety_days / 传统 lead=lead_time_days;
    无日销(ds<=0) → 0(调用方回退静态 safety_qty 兜底) —— 替代 seed 随机/导入的静态安全线
    """
    if ds <= 0:
        return 0.0
    return round(ds * lead, 1)


def _trad_suggested(ds, lead, safety_days, avail, transit):
    """传统逐仓补货量(纯函数, 可单测): 安全天数×日销 与 bbcc 同构

    suggested = ds×(lead + safety_days) − avail − transit
    需求门控: 日销 < _MIN_DS 视为无实际需求 → 0;
    取整防放大: 缺口 < 1 件 → 0(0.56 被 round 成 1 再凑整箱 24 件荒谬)
    """
    if ds < _MIN_DS:
        return 0
    raw = ds * (lead + safety_days) - avail - transit
    if raw < 1:
        return 0
    return round(raw)


def _trad_note(ds, w7, w14, w28, avail, safety, box_qty, suggested, after_turnover=None, tw90=90,
               uneconomical=False, safety_hint=None):
    """传统逐仓备注(纯函数): 对齐 bbcc 语境——无销量积压/低需求说明/周转红线/补货前置濒临/动态安全线预警

    ds<=0: 无销量语境(积压/⚪); ds>0 补货: 趋势+需补量; ds>0 不补但低于安全线: 说明暂不补原因;
    周转红线: 补货时看补后综转(after_turnover 含 box_qty), 不补时看当前综转——>tw90 超/接近;
    uneconomical: 缺口<1件凑整箱不经济 → 提示暂不补; safety_hint: 动态安全线预警文本(调用方算好)
    """
    parts = []
    if ds > 0:
        t7 = "📈" if w7 > w14 * 1.15 else ("📉" if w7 < w14 * 0.85 else "➡️")
        t14 = "📈" if w14 > w28 * 1.15 else ("📉" if w14 < w28 * 0.85 else "➡️")
        parts.append("近7%s 近14%s" % (t7, t14))
    if ds <= 0:
        if avail > 0:
            parts.append("🔴 近30天无销量，库存积压")
        else:
            parts.append("⚪ 近30天无销量")
    elif box_qty > 0:
        parts.append("需补%s件" % box_qty)
    elif uneconomical:
        parts.append("⚠️ 缺口不足1件，凑整箱不经济，暂不补")
    # 动态安全线预警(逐仓: 该仓可售 vs 传统周期, 只提示不参与计算) —— 替代旧"低于安全线暂不补"话术
    if safety_hint:
        parts.append(safety_hint)
    # 周转红线(对齐 bbcc: 补货时=补后综转, 不补时=当前综转)
    if after_turnover is not None and ds > 0:
        if after_turnover > tw90:
            parts.append("🔴 %s%s天超%s天" % ("补后综转" if box_qty > 0 else "当前综转",
                                               after_turnover, tw90))
        elif after_turnover > tw90 - 15:
            parts.append("⚠️ %s%s天接近%s天" % ("补后综转" if box_qty > 0 else "当前综转",
                                                 after_turnover, tw90))
    if box_qty > 0:
        parts.insert(0, "🔴 已濒临")
    if not parts:
        parts.append("库存充足")
    return " · ".join(parts)


def _build_repl(channel, mode):
    """补货建议计算体(共享缓存 builder, 返回全量列表)"""
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

    # 促销尖峰削峰: 近窗口单日峰值 > 5×历史基线(28-60天) → 截断(促销不推高趋势加权/活性门控)
    daily_28 = {s: smooth_promo_spikes(d) for s, d in daily_28.items()}
    multi = calc_sales_multi(daily_28, windows=[7, 14, 28], sparse="shrink")
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
            # 动态安全线周期(一盘货): C周期=b_to_c_days+c_safety_days, B周期=ship_to_b_days+safety_multiplier, 全周期=C+B
            _c_period = int(cfg.get("b_to_c_days", "0")) + int(cfg.get("c_safety_days", "0"))
            _b_period = int(cfg.get("ship_to_b_days", "0")) + int(float(cfg.get("safety_multiplier") or 0))
            _full_period = _c_period + _b_period
            ds7 = round(s7.get(sku, 0), 1)
            ds14 = round(s14.get(sku, 0), 1)
            ds28 = round(s28.get(sku, 0), 1)
            # 活性门控: 近 7/14 天有销售=近期活跃(×1); 仅 28 天历史销售=弱化(×0.5); 全零=0
            _act = 1.0 if (s7.get(sku, 0) > 0 or s14.get(sku, 0) > 0) else (0.5 if s28.get(sku, 0) > 0 else 0.0)
            ds = round(fused.get(sku, 0) * season * _act, 1)
            # 需求门控(与 traditional 对齐): 日销 < 门槛 视为无实际需求, 缺口不触发补货
            has_demand = ds >= _MIN_DS
            # 安全天数(库存行 safety_days 优先, 回退配置)
            safety_days = float(cfg.get("safety_multiplier") or 0)
            effective_safety = round(ds * safety_days, 1) if has_demand else 0
            # C 缺口 = 日销×lead − C可用 − B→C调拨在途(保留1位小数)
            c_gap = max(round(ds * lead - avail - c_transit, 1), 0) if has_demand else 0
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
            b_box = (b_replenish + box - 1) // box * box if b_replenish >= 1 else 0
            # 取整防放大: C/B 缺口 <1 件时凑整箱不经济(0.56→1件→整箱荒谬), 不补+提示
            uneconomical = (0 < suggested < 1) or (0 < b_replenish < 1)
            if uneconomical:
                suggested = 0
                b_box = 0
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
            if c_gap > 0 and not uneconomical:
                if b_gap <= 0:
                    parts.append("C建议补%s件" % suggested)
                else:
                    # 缺口标注准确: C缺口(c_gap) 被 B 仓覆盖后剩 B缺口(b_gap)——决定 B 补货量
                    parts.append("B建议补%s件(C缺口%s→B缺口%s,调拨消耗%s,箱规%s)"
                                 % (b_box, c_gap, b_gap, round(ds * b_ship_days + effective_safety, 1), box))
                if b_gap > 0 and b_available <= 0:
                    parts.append("B仓已空")
                elif b_gap > 0:
                    parts.append("B仓仅%s件需从自有仓调" % b_available)
            if uneconomical:
                parts.append("⚠️ 缺口不足1件，凑整箱不划算，暂不补")
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
                    parts.append("近30天无销量，B仓库存积压")
                elif avail > 0:
                    parts.append("近30天无销量，C仓库存积压")
                else:
                    parts.append("⚪ 近30天无销量")
            # 动态安全线预警(一盘货 BC 合计 vs 全周期; 只提示不参与计算)
            elif _full_period > 0:
                _bc_total = avail + c_transit + b_available + b_in_transit
                _days_left = _bc_total / ds if ds > 0 else 999
                if _days_left <= _full_period:
                    parts.append("现BC合计低于动态安全线：可撑%s天<周期%s天，需尽快补到B仓"
                                 % (round(_days_left), _full_period))
                elif _days_left <= _full_period + 2:
                    parts.append("⚠️ 接近动态安全线：约剩%s天（周期%s天）" % (round(_days_left), _full_period))
            if not parts:
                parts.append("库存充足")
            # P1: 濒临断货反哺 —— 建议补>0 即 Adj-DOS≤补货周期(缺口), note 前置 🔴 已濒临
            if suggested > 0 or b_box > 0:
                parts.insert(0, "🔴 已濒临")
            note = " · ".join(parts)
            suggestions.append({
                "sku": sku, "barcode": prod.get("barcode", ""),
                "product_name": prod.get("product_name", ""), "brand": prod.get("brand", ""),
                "store": prod.get("store", ""), "category": prod.get("category", ""),
                "available_qty": avail, "safety_qty": effective_safety, "in_transit_qty": transit,
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
        _wh_sm = {k: smooth_promo_spikes(v) for k, v in wh_sales.items() if v}
        _wm = calc_sales_multi(_wh_sm, windows=[7, 14, 28],
                               sparse="shrink")
        tw90 = int(cfg.get("turnover_warning_90") or 90)
        # 安全天数口径(与 bbcc 同构): mode_traditional_safety_multiplier → cfg["safety_multiplier"]
        # 静态 inventory.safety_qty 不再参与补货量(早期遗留, 仅看板断货卡/低库存规则作兜底)
        safety_days = float(cfg.get("safety_multiplier") or 0)
        for r in inv:
            if r.get("warehouse_type") != "platform":
                continue
            sku = r.get("sku")
            wh = r.get("warehouse")
            wk = "%s|%s" % (sku, wh)
            w7 = _wm[7].get(wk, 0)
            w14 = _wm[14].get(wk, 0)
            w28 = _wm[28].get(wk, 0)
            # 活性门控(逐仓): 近 7/14 天该仓有销售=活跃(×1); 仅 28 天历史=弱化(×0.5)
            _act = 1.0 if (w7 > 0 or w14 > 0) else (0.5 if w28 > 0 else 0.0)
            ds = rolling_predict(w7, w14, w28) * season * _act
            avail = int(r.get("available_qty") or 0)
            transit = int(r.get("in_transit_qty") or 0)
            effective_safety = round(ds * safety_days, 1) if ds > 0 else 0
            raw = ds * (lead + safety_days) - avail - transit
            suggested = _trad_suggested(ds, lead, safety_days, avail, transit)
            uneconomical = ds >= _MIN_DS and 0 < raw < 1
            _trad_period = lead + int(safety_days)
            # 动态安全线预警(逐仓: 该仓可售 avail+transit vs 传统周期, 只提示不参与计算)
            _hint = None
            if ds > 0 and _trad_period > 0:
                _dl = (avail + transit) / ds
                if _dl <= _trad_period:
                    _hint = "🔴 库存告急：只够卖%s天（补货需%s天），将断货，尽快补货" % (round(_dl), _trad_period)
                elif _dl <= _trad_period + 2:
                    _hint = "⚠️ 约剩%s天（补货周期%s天），建议备货" % (round(_dl), _trad_period)
            prod = products.get(sku, {})
            box = int(prod.get("box_qty") or 1)
            box_qty = ((suggested + box - 1) // box * box) if suggested > 0 else 0
            after_turnover = round((avail + transit + box_qty) / ds, 1) if ds > 0 else 999
            suggestions.append({
                "sku": sku, "barcode": prod.get("barcode", ""),
                "product_name": prod.get("product_name", ""), "brand": prod.get("brand", ""),
                "store": prod.get("store", ""), "warehouse": wh, "category": prod.get("category", ""),
                "available_qty": avail, "safety_qty": effective_safety, "in_transit_qty": transit,
                "effective_safety": effective_safety,
                "daily_sales": round(ds, 1), "daily_sales_7": round(w7, 1),
                "daily_sales_14": round(w14, 1), "daily_sales_28": round(w28, 1),
                "daily_sales_60": round(fused.get(sku, 0), 1),
                "suggested_qty": box_qty, "after_turnover": after_turnover,
                "days_to_empty": round(avail / ds, 1) if ds > 0 else 999,
                "note": _trad_note(ds, w7, w14, w28, avail, effective_safety, box_qty, suggested,
                                   after_turnover, tw90, uneconomical, _hint),
            })

    # 排序: 需补货优先, 缺口大优先
    suggestions.sort(key=lambda s: (-(1 if (s.get("suggested_qty") or 0) > 0 or (s.get("b_suggested") or 0) > 0 else 0),
                                     -(s.get("suggested_qty") or 0), -(s.get("daily_sales") or 0), s.get("sku", "")))
    return suggestions


def _config(channel, mode):
    """补货参数加载: mode 前缀键优先, 通用旧键兜底(按模式隔离, 防跨模式污染)

    先收 mode_{mode}_ 前缀键, 再读通用键(跳过所有 mode_ 前缀), 最后 mode 键覆盖——
    mode_traditional_safety_multiplier 不会被通用旧键 safety_multiplier 覆盖(SELECT 无序)
    """
    rows = query("SELECT `key`, value FROM replenishment_config WHERE channel=%s OR channel=''", [channel])
    cfg = {}
    pref = {}
    prefix = "mode_%s_" % mode
    for r in rows:
        k = r.get("key") or ""
        v = r.get("value") or ""
        if k.startswith(prefix):
            pref[k[len(prefix):]] = v
        elif not k.startswith("mode_"):
            cfg[k] = v
    cfg.update(pref)  # mode 前缀键优先于通用旧键
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

"""dashboard 濒临断货(stock-risk)计算模块(2026-10-11 拆分——dashboard.py 减负)
函数: _rule_params(规则参数) / _grade_risk(三级分级纯函数) / _stock_risk(主计算)
依赖: _hourly_accel 仍在 dashboard(函数内延迟 import——防模块级循环)
"""
from datetime import datetime, timedelta, timezone

from db import query, one, execute
from routes.common import try_err, INVENTORY_COLS as _INV_COLS
from biz.sales import pick_ds   # 日销取值单一真源(2026-11-01 收敛; 顶层 import 无环)
from core.clock import now_cn




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
_RISK_LV = {"red": 0, "orange": 1, "yellow": 2}


def risk_sort_key(x):
    """断货行排序**唯一真源** —— 现算(Python)与物化(读回后)必须同序

    2026-11-07 P0: 原来排序 key 只有 (level, days_to_empty), 同值行顺序取决于 dict 插入序
    (inventory 的物理返回序, TiDB 不保证稳定) —— 同一份数据两次请求顺序可能不同;
    物化后读侧若是 SQL 排序, 又与 Python 稳定排序对不上, 双路对账会报"每行都不同"
    (实测 jd full=1 报 17616 条, 全是错位不是数值差)。补 (sku, barcode, warehouse) 成全序。

    为什么不交给 SQL 排: MySQL 的 collation('-','_' 视为等价/忽略重音) 与 Python 的码位序
    不一致, 同一批 SKU 只要格式不统一就会错位。排序放在 Python, 两路共用这一个函数。
    """
    return (_RISK_LV.get(x.get("level"), 9), x.get("days_to_empty") or 0,
            x.get("sku") or "", x.get("barcode") or "", x.get("warehouse") or "")


def _risk_compare(channel, full: int = 0):
    """双写期对账：现算(legacy) vs 物化，逐字段比 —— 切换开关前的 0 差异证明

    不落缓存、不进常态路径：只在人工验证/排障时 `?compare=1` 打一次。
    """
    _now = _stock_risk(channel, full=full, legacy=True)
    try:
        from core import unit_store as _US
        _mat = _US.risk_of(channel, full=full)
    except Exception as _e:
        try_err("dash_stock", "risk 对账读物化失败", _e)
        _mat = None
    if _mat is None:
        return {"ok": False, "materialized": False,
                "note": "物化不可用（未构建 / risk_enabled=0 / fp 不一致 / 查询失败）"}
    diffs = []
    _fields = ("sku", "barcode", "warehouse", "level", "available_qty",
               "days_to_empty", "daily_sales", "product_name")
    for _d, _k in (("bc", "bcItems"), ("c", "cItems"), ("own", "ownItems")):
        a, b = (_now.get(_k) or []), (_mat.get(_k) or [])
        if len(a) != len(b):
            diffs.append("%s 条数 %d != %d" % (_d, len(a), len(b)))
        for i in range(min(len(a), len(b))):
            for f in _fields:
                if (a[i] or {}).get(f) != (b[i] or {}).get(f):
                    diffs.append("%s[%d].%s %r != %r" % (_d, i, f, (a[i] or {}).get(f), (b[i] or {}).get(f)))
        for f in ("Total", "Critical", "Warning"):
            if _now.get(_d + f) != _mat.get(_d + f):
                diffs.append("%s%s %r != %r" % (_d, f, _now.get(_d + f), _mat.get(_d + f)))
    return {"ok": not diffs, "materialized": True, "diff_count": len(diffs),
            "diffs": diffs[:40],
            "counts": {k: _now.get(k) for k in ("bcTotal", "cTotal", "ownTotal")}}


def _stock_risk(channel, full: int = 0, legacy: bool = False, limit: int = 0, offset: int = 0,
                level: str = "", inv_rows=None):
    """濒临断货 TOP: B(BBCC)/C(传统)/BC/own 维度

    ── 2026-11-07 P0(最后一块) 读路径物化 ─────────────────────────────────────
    线上实测冷算 4.4~5.17s, 而返回体只有 3KB —— 慢的不是 SQL 也不是判定,
    是**为了 10 条明细把 inventory 9 万行 + sku_sales_stats 12.6 万行搬进 Python**。

    现在: 本函数的完整结果(三个维度的全部命中行)由重建线程算一次写进 `risk_snapshot`,
    请求路径只读几百行(见 core/unit_store.risk_of)。`legacy=True` 强制走下面这段现算,
    用于 ① 重建时产出快照(自己不能读自己) ② 双写期两路逐字段比对。

    ⚠️ 为什么不复用 unit_alert: 那是另一套口径(state 用 period_of 安全线, 不含活动/加速/OTIF;
       own 维日销取 __C__ 下游需求)。断货卡是自己的一套公式(见 _grade_risk), 混表必串味。
    """
    if not legacy:
        try:
            from core import unit_store as _US
            # 2026-11-07 §15.162 P0 SWR: fp 失配但上一版完整快照可用时, 返回上一版
            #   (不退 4.4s 现算), __swr 随 payload 带回、由路由层 pop 到响应顶层;
            #   从未构建/不可用才落到底部现算(保证完整数据)。legacy=True 对账路径不 SWR。
            _mat, _swr = _US.serve_risk(channel, full=full, limit=limit, offset=offset,
                                        level=level)
            if _mat is not None:
                if _swr and isinstance(_mat, dict):
                    _mat["__swr"] = _swr
                return _mat
        except Exception as _e:
            try_err("dash_stock", "risk 物化读取降级(走现算)", _e)
    from routes.dashboard import _hourly_accel  # 延迟 import(防模块级循环)
    """濒临断货 TOP: B(BBCC)/C(传统)/BC/own 维度"""
    from biz.sales import load_daily_sales_grouped, calc_sales_multi, rolling_predict
    from db import query as _q
    cfg_rows = _q("SELECT `key`, value FROM replenishment_config WHERE channel=%s", [channel])
    cfg = {r.get("key"): r.get("value") for r in cfg_rows}
    # 有效补货周期 L/T_eff(模式双线, 复用补货配置): bc 维度= B→C 调拨+安全缓冲; 传统维度=采购到货
    # L/T 与补货建议同源 —— 2026-11-06 S3: 公式收敛到 core.metrics.periods(唯一真源),
    #   这里只做取值。原来 5 个文件各写一遍同样五行, mode 前缀兜底还互不相同。
    from core import metrics as _M
    _p = _M.periods(cfg)
    lit_bbcc = float(_p["b2c"])
    lit_trad = float(_p["lead"])

    # 2026-10-29 修复(按码真根因): 原 SELECT 漏 barcode → 下方 r.get("barcode") 恒 '' →
    #   bc_total/c_items 按 (sku,'') 合并, 断货卡按码从未生效
    # 2026-11-07 P2: 重建线程会把刚读过的 inventory 全量传进来(同一份内存数据),
    #   省掉重读 10 万行 —— 这是重建 16.2s 里 risk 段 4.6s 的主要部分。
    #   ⚠️ 只复用**输入**, 公式仍在本函数内唯一一份(红线 D: 不复制算法)。
    inv = inv_rows if inv_rows is not None else _q(
        "SELECT " + _INV_COLS + " FROM inventory WHERE channel=%s", [channel])
    try:
        from routes.common import check_cols as _cc
        _cc(inv, ("barcode", "warehouse"), "risk", "断货物料")
    except Exception:
        pass
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
        accel = _hourly_accel(channel, now_cn(), ratio=accel_ratio, min_qty=accel_min_qty)
    except Exception as _e:
        try_err("dash", "hourly_accel 计算降级", _e)
        accel = {}

    # ── 2026-10-17 物化短路(根治 3.7-5.5s): 物化表有数据 → 直接读 WH_ALL/WH_C/逐仓行现场 rolling_predict,
    #    跳过 load_daily_sales_grouped + calc_sales_multi ×3 直算(曾直算抢跑——5 万 SKU 白算 3 次);
    #    物化空/失败 → 降级原直算(数学等价, 零行为变化)。物化行只存三窗口底料, 现场 rolling_predict 与直算逐 SKU 等价
    fused, fused_c, wh_fused, sigma, _has_materialized = {}, {}, {}, {}, False
    wh_fused_bc = {}   # 2026-10-30 逐仓按码: sku|barcode|warehouse → fused
    fused_bc = {}  # 2026-10-29 按 69码 日销
    try:
        from biz.sales import get_sku_sales_stats, WH_ALL, WH_C, rolling_predict as _rp
        # 2026-11-07 P0(去重叠加): 原来这里连续读了 **5 次**物化表, 其中两次参数完全相同
        #   (`wh=WH_C` 合计行读两遍) —— 线上实测 stock-risk 7.7s, 大头是这 5 次全量传输。
        #   改为**一次** `barcode="all"` 取回该渠道全部维度行(合计 + 按码 + __C__ + 逐仓),
        #   在内存里按 (barcode 是否为空 × warehouse 是否聚合维) 分流成原来那 5 个映射。
        #   语义逐项等价: 原 5 次各自筛的就是这些行的不同子集, 只是多绕了 4 趟往返。
        #   fields 裁剪: 只取 fused/sigma 需要的 4 列(原为 11 列, 白传 7 列 × 12 万行)。
        _rws_all = get_sku_sales_stats(channel, days=28, barcode="all",
                                       fields=("ds7", "ds14", "ds28", "sigma"))
        if _rws_all:
            for _r in _rws_all.values():
                _sku = str(_r.get("sku") or "")
                _bc = str(_r.get("barcode") or "")
                _wh = str(_r.get("warehouse") or "")
                _v = _rp(float(_r.get("ds7") or 0), float(_r.get("ds14") or 0),
                         float(_r.get("ds28") or 0))
                if _bc:
                    if _wh == WH_C:
                        # BC 判定的日销读 __C__(全国 C 仓) —— B 仓不产生销售
                        fused_bc[(_sku, _bc)] = _v
                    elif _wh and _wh not in (WH_ALL, WH_C, "__BC__"):
                        # 逐仓按码(该仓该码) —— 逐仓判定降级只降码不降维
                        wh_fused_bc[(_sku, _bc, _wh)] = _v
                else:
                    if _wh == WH_C:
                        fused[_sku] = _v
                        fused_c[(_sku, WH_C)] = _v
                        if abs(float(_r.get("sigma") or 0)) > 0:
                            sigma[_sku] = float(_r.get("sigma") or 0)
                    elif _wh and _wh not in (WH_ALL, WH_C, "__BC__"):
                        wh_fused[(_sku, _wh)] = _v
            _has_materialized = True
    except Exception as _e:
        from routes.common import try_err as _terr2
        _terr2('dash_stock', 'stock_risk 物化读取降级(走直算)', _e)
    # ── 降级直算(仅物化表空/失败——与旧口径完全一致) ──
    if not _has_materialized:
        by_sku, by_sku_wh, by_sku_bc = load_daily_sales_grouped(28, channel, skus=skus)
        multi = calc_sales_multi(by_sku, windows=[7, 14, 28])
        fused = {s: rolling_predict(multi[7].get(s, 0), multi[14].get(s, 0), multi[28].get(s, 0)) for s in skus}
        c_whs = {r.get("warehouse") for r in _q("SELECT DISTINCT warehouse FROM inventory WHERE channel=%s AND warehouse_type='platform' AND warehouse!=''", [channel])}
        daily_c = {}
        for wk, wd in by_sku_wh.items():
            base, wh = wk.rsplit("|", 1)
            if wh in c_whs:
                m = daily_c.setdefault(base, {})
                for d, q in wd.items():
                    m[d] = m.get(d, 0) + q
        cmulti = calc_sales_multi(daily_c, windows=[7, 14, 28]) if daily_c else {7: {}, 14: {}, 28: {}}
        fused_c = {(s, WH_C): rolling_predict(cmulti[7].get(s, 0), cmulti[14].get(s, 0), cmulti[28].get(s, 0)) for s in daily_c}
        # 2026-10-29 按码直算
        if by_sku_bc:
            _bcm = calc_sales_multi(by_sku_bc, windows=[7, 14, 28])
            for _k in by_sku_bc:
                fused_bc[tuple(str(_k).rsplit("|", 1))] = rolling_predict(_bcm[7].get(_k, 0), _bcm[14].get(_k, 0),
                                               _bcm[28].get(_k, 0))
        wh_multi = calc_sales_multi(by_sku_wh, windows=[7, 14, 28])
        wh_fused = {tuple(str(k).rsplit("|", 1)): rolling_predict(wh_multi[7].get(k, 0), wh_multi[14].get(k, 0),
                                       wh_multi[28].get(k, 0)) for k in by_sku_wh}
        # P2: 动态安全库存 SS_dyn = Z(1.65) × 日销σ × √L/T —— 波动越大动态安全线越高, 更早濒临
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
        _bc = str(r.get("barcode") or "").strip()   # 2026-10-29 按 69码(空码降级)
        if wt in ("platform", "platform_b"):
            st = bc_total.setdefault((sku, _bc), {"available": 0, "safety": 0, "transit": 0, "ct": 0})
            st["available"] += qty
            st["safety"] += safety
            # 链路: 供应商统一发 B 仓 → B→C 调拨补 C 缺口 → BC 供应的在途仅 B 仓行(供应商→B)
            # + C 仓行 c_transit(B→C 调拨); C 仓行 in_transit(直发)在 BBCC 链路无业务含义
            if wt == "platform_b":
                st["transit"] += tty
            if wt == "platform":
                st["ct"] += ctt

    # 2026-11-01 删除本文件内的 _ds_of: 它把"降级"写成 `or`(0.0 也会被合计顶上),
    #   且从未被调用(grep 零调用点)。取值规则现统一走 biz.sales.pick_ds。

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
        _bc = str(r.get("barcode") or "").strip()   # 2026-10-29 按 69码
        avail = int(r.get("available_qty") or 0)
        safety = int(r.get("safety_qty") or 0)
        tty = int(r.get("in_transit_qty") or 0)
        ctt = int(r.get("c_transit") or 0)
        # 逐仓口径: 该仓该码 → 该仓合计 → 0(不跨维) —— 规则见 biz/sales.pick_ds
        ds = pick_ds(wh_fused_bc, wh_fused, sku, _bc, wh)
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
            c_items.append({"sku": sku, "barcode": _bc or (pmap.get(sku) or {}).get("barcode", ""),
                            "product_name": (pmap.get(sku) or {}).get("product_name", sku),
                            "warehouse": wh or "C仓", "type": "C", "available_qty": avail,
                            "daily_sales": round(ds, 1), "days_to_empty": round(adj_dos, 1),
                            "level": lv, "accel": (a_rate > 1.1) or None})
    # BC 合计(bbcc 专属: B仓+全国C仓按 SKU 合计, 一盘棋视图 → 标签 BC; 用 bbcc 补货周期)
    bc_items = []
    for (sku, _bc), st in bc_total.items():
        avail = st["available"]
        safety = st["safety"]
        # C 维日销: 该码(降级 SKU 合计) —— 规则见 biz/sales.pick_ds
        ds = pick_ds(fused_bc, fused_c, sku, _bc, WH_C)
        if not ds:
            # 直算降级分支的 fused 是全仓合计(与物化分支的 __C__ 语义不同), 仅作最后兜底
            ds = fused.get(sku, 0)
        if ds <= 0:
            continue
        if avail <= 0 and not include_avail_zero:
            continue
        adj_dos = (avail + st["transit"] * _otif(sku) + st["ct"]) / (ds * factor_bbcc * accel.get(sku, 1.0))
        buffer = avail / max(max(safety_line(ds, lit_bbcc), _ss_dyn(sku, lit_bbcc)), 1)
        inc, lv = _grade(adj_dos, buffer, lit_bbcc)
        if inc:
            bc_items.append({"sku": sku, "barcode": _bc or (pmap.get(sku) or {}).get("barcode", ""),
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
        _bc = str(r.get("barcode") or "").strip()   # 2026-10-29 按 69码
        avail = int(r.get("available_qty") or 0)
        safety = int(r.get("safety_qty") or 0)
        tty = int(r.get("in_transit_qty") or 0)
        ds = pick_ds(wh_fused_bc, wh_fused, sku, _bc, wh)
        if ds <= 0:
            continue
        if avail <= 0 and not include_avail_zero:
            continue
        adj_dos = (avail + tty * _otif(sku)) / (ds * factor_trad * accel.get(sku, 1.0))
        buffer = avail / max(max(safety_line(ds, lit_trad), _ss_dyn(sku, lit_trad)), 1)
        inc, lv = _grade(adj_dos, buffer, lit_trad)
        if inc:
            own_items.append({"sku": sku, "barcode": _bc or (pmap.get(sku) or {}).get("barcode", ""),
                              "product_name": (pmap.get(sku) or {}).get("product_name", sku),
                              "warehouse": wh or "自有", "type": "OWN", "available_qty": avail,
                              "daily_sales": round(ds, 1), "days_to_empty": round(adj_dos, 1),
                              "level": lv, "accel": (accel.get(sku, 1.0) > 1.1) or None})

    def _stats(items):
        crit = sum(1 for i in items if i.get("level") == "red")
        warn = sum(1 for i in items if i.get("level") == "orange")
        return len(items), crit, warn

    # 2026-11-07 P0: 排序统一走 risk_sort_key(现算/物化两路唯一真源)
    bc_items.sort(key=risk_sort_key)
    c_items.sort(key=risk_sort_key)
    own_items.sort(key=risk_sort_key)
    bt, bc, bw = _stats(bc_items)
    ct, cc, cw = _stats(c_items)
    ot, oc, ow = _stats(own_items)
    # 2026-11-07 P2: 现算分支也要能真分页(legacy=1 只用于对账/兜底, 但契约必须一致 ——
    #   两路返回结构不同会让前端"有时全量有时一页", 而 compare 对账时逐字段比较会误报)
    def _slice(items):
        if int(limit or 0) > 0:
            _o = max(0, int(offset or 0))
            return items[_o:_o + int(limit)]
        return items if full else items[:10]

    payload = {"items": [], "total": 0, "critical": 0, "warning": 0,
               "bcItems": _slice(bc_items), "bcTotal": bt, "bcCritical": bc, "bcWarning": bw,
               "cItems": _slice(c_items), "cTotal": ct, "cCritical": cc, "cWarning": cw,
               "ownItems": _slice(own_items), "ownTotal": ot, "ownCritical": oc, "ownWarning": ow}
    return payload

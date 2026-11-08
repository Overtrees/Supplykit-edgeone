"""原生补货路由(方案 B): BBCC 两步法 + 传统逐仓(契约与旧 backend 一致)"""
import os
import time as _time
from fastapi import APIRouter, Request

from db import query, one
from routes.common import ok, fail, traced, try_err, INVENTORY_COLS as _INV_COLS
from biz.sales import rolling_predict  # 物化 fused 现场计算(降级路径亦用)
from core.note_segs import zh_note  # 2026-11-04 结构化备注: segs 单一真源, note=zh_note(segs)

router = APIRouter(tags=["insights"])

# 2026-11-07 P0: 补货建议改走 `core/list_store` 域指纹物化 —— 旧的 300s 表缓存
#   （页级 → 全量分块 → builder）已整体删除, 不再在请求路径同步全量现算（红线 2 / 6）。
#   线上基线: 热 min 0.666s, 但 TTL 到点/写操作后的第一个请求 8.815s。
# 需求门控阈值: 日销低于此值(近28天不足 2.8 件)视为无实际需求——无需求时库存不会被消耗,
# 安全线缺口不触发补货(安全线是需求波动的缓冲; 参考 bbcc 无需求不补的驱动语义)
_MIN_DS = 0.1


def _slim_on():
    """§15.172 取数裁剪开关 —— 单一真源在 `biz.sales.slim_on()`, 这里只做委托,
    避免两个消费方各写一份环境变量判定(红线 2)。`SALES_SLIM=0` 一刀回退。"""
    from biz.sales import slim_on
    return slim_on()


@router.get("/insights/replenishment")
@traced
def get_replenishment_suggestions(days: int = 28, source: str = "", mode: str = "bbcc",
                                  channel: str = "jd", page: int = 0, page_size: int = 0,
                                  search: str = "", need_only: int = 0,
                                  legacy: int = 0, state: int = 0, force: int = 0):
    """补货建议(域指纹物化 + 后台重建, 见 core/list_store.py): mode=bbcc/traditional

    2026-11-07 P0 根治「读时全量现算 + TTL 300s 表缓存」:
      旧路径每 300s 到点(哪怕没有写操作)就由下一个请求在**请求路径**同步跑
      `_build_repl`(全量读 inventory + 算 5 万行) → 线上首次 8.815s。
      缓存失效打墓碑时同样刺穿, 所以「改一次配置、下一个用户等 8 秒」。
    现在: 请求路径只读 list_snapshot 按 seq 的一页 + head 计数; 重建在后台线程,
      由 data_version_dom 域计数 fp 驱动(**无 TTL**), 算完核对行数/need 数才切 head。
      fp 不命中 → 退回现算(慢但正确, 不返回旧值) + 起后台重建。

    形状契约**不变**: 无分页裸调 data 是列表(截断 2000, 导出/内部调用方按列表读);
    分页返回 {items,total,page,page_size}。`_build_repl` 算法一个字没动。
    `legacy=1` 强制现算(线上逐字段对账用), `state=1` 返回物化四维诊断,
    `force=1` 同步重建并回四维状态(部署后预热/对账用, 属诊断口非常态读路径)。
    """
    from core import list_store as _LS
    _base = "repl|%s|%s|%s|%s" % (channel, mode, days, source)
    if state or force:
        if force:
            _LS.register_scope("repl", _base, channel, mode, days, source)
            _rb = _LS.build("repl", _base, force=True)
            return ok({"rebuild": _rb, "state": _LS.status("repl", _base)})
        return ok(_LS.status("repl", _base))
    _d = _LS.serve("repl", _base, channel, mode, days, source,
                   lambda: _build_repl(channel, mode),
                   need_only=need_only, search=search,
                   page=page, page_size=page_size, legacy=legacy)
    # §15.161: __swr 从 data 里摘出来放响应顶层, 数组/对象两种形状都不被污染。
    _hd = _LS.pop_swr(_d)
    if page <= 0 or page_size <= 0:
        # 无分页裸调: 形状是契约(见 §15.153 打红教训), 截断 2000 防 EdgeOne 网关 413
        _items = (_d.get("items") if isinstance(_d, dict) else None) or []
        return ok(_items[:2000], **_hd)
    return ok(_d, **_hd)


def safety_line(ds, lead):
    """动态安全线(可售天数 ≥ 补货周期): ds × lead

    引用补货参数按模式区分: bbcc lead=b_to_c_days+c_safety_days / 传统 lead=lead_time_days;
    无日销(ds<=0) → 0(静态 safety_qty 已退役, 全链路动态口径)
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
               uneconomical=False, safety_seg=None):
    """传统逐仓备注(纯函数): 对齐 bbcc 语境——无销量积压/低需求说明/周转红线/补货前置濒临/动态安全线预警

    2026-11-04 结构化: 返回 (note, segs) 二元组 —— segs 是唯一真源, note=zh_note(segs);
    safety_seg: 动态安全线预警的 seg dict(调用方算好), 原 safety_hint 中文字符串退役
    """
    segs = []
    if ds > 0:
        t7 = "up" if w7 > w14 * 1.15 else ("down" if w7 < w14 * 0.85 else "flat")
        t14 = "up" if w14 > w28 * 1.15 else ("down" if w14 < w28 * 0.85 else "flat")
        segs.append({"k": "trend", "t7": t7, "t14": t14})
    if ds <= 0:
        if avail > 0:
            segs.append({"k": "no_sales_stock"})
        else:
            segs.append({"k": "no_sales"})
    elif box_qty > 0:
        segs.append({"k": "need", "n": box_qty})
    elif uneconomical:
        segs.append({"k": "unecon_t"})
    # 动态安全线预警(逐仓: 该仓可售 vs 传统周期, 只提示不参与计算) —— 替代旧"低于安全线暂不补"话术
    if safety_seg:
        segs.append(safety_seg)
    # 周转红线(对齐 bbcc: 补货时=补后综转, 不补时=当前综转)
    if after_turnover is not None and ds > 0:
        if after_turnover > tw90:
            segs.append({"k": "turn_over", "w": "after" if box_qty > 0 else "cur",
                         "v": after_turnover, "t": tw90})
        elif after_turnover > tw90 - 15:
            segs.append({"k": "turn_near", "w": "after" if box_qty > 0 else "cur",
                         "v": after_turnover, "t": tw90})
    if box_qty > 0:
        segs.insert(0, {"k": "critical"})
    if not segs:
        segs.append({"k": "ok"})
    return zh_note(segs), segs


def _build_repl(channel, mode):
    """补货建议计算体(共享缓存 builder, 返回全量列表)
    2026-10-17 物化重构: 日销三窗口从"每次请求全量重算(22s)"改为读物化表 sku_sales_stats——
    物化表列: __C__=全国C仓(bbcc)/__ALL__=全国(展示)/逐仓(traditional); cut 列=削峰+shrink(补货口径)
    表空/重建失败 → 降级直算(与旧逻辑完全一致); 库存/配置仍每次直查(实时性不变)
    """
    _stats = {}
    try:
        from biz.sales import get_sku_sales_stats
        if _slim_on():
            # §15.172 取数裁剪(纯读侧, 算法一行未动): 线上分段证据(list_head.timing,
            #   2026-11-08 13:41 repl gen=25): calc=6.1s 里 sql=5.3s/8 条, 87% 在 SQL。
            #   而 sku_sales_stats 该渠道 254268 行里, 本接口**只用** `__C__` 维度
            #   (20093 行; `_rd` 只查 sku|bc|__C__), 且只读 ds7_cut/ds14_cut/ds28_cut 三列
            #   (全表 11 列)。原实现「合计行 + 全部按码行」= 2 次往返 / 127413 行 / 11 列,
            #   仅取其中 1/6 的行、3/11 的列。
            #   `barcode="all"` 一次取「合计 + 按码」→ 1 次往返; wh 限定维度; fields 裁列。
            #   traditional 模式按**逐仓**取(`_rd(sku, wh)`), 故 wh 只能限 __C__, 不能限逐仓。
            _w = "__C__" if mode == "bbcc" else None
            _stats = get_sku_sales_stats(channel, days=90, barcode="all", wh=_w,
                                         fields=("ds7_cut", "ds14_cut", "ds28_cut")) or {}
        else:
            _stats = get_sku_sales_stats(channel, days=90) or {}
            # 2026-10-29 按 69码: 合并按码行(原只加载合计行 → _rd 的 "sku|bc|wh" 查询永远 miss,
            #   补货建议整条链路退化为 SKU 合计值 —— 多码 SKU 的两码会显示同一份日销/库存)
            try:
                _stats.update(get_sku_sales_stats(channel, days=90, barcode="*") or {})
            except Exception as _ebc:
                from routes.common import try_err as _te2
                _te2('repl', '补货按码行加载降级', _ebc)
    except Exception as _e:
        try_err('repl', '物化读取降级', _e)
        _stats = {}
    # 直算降级(仅物化表空/不可用时——与旧口径一致; 构造同构 _stats 使主循环单一)
    if not _stats:
        try:
            from biz.sales import load_daily_sales_grouped as _ldg, calc_sales_multi as _csm, \
                smooth_promo_spikes as _sps, rolling_predict as _rp
            _by, _by_wh, _by_bc = _ldg(90, channel)
            _c_whs = {r.get("warehouse") for r in query(
                "SELECT DISTINCT warehouse FROM inventory WHERE channel=%s AND warehouse_type='platform' AND warehouse!=''",
                [channel])}
            _by_c = {}
            for wk, wd in _by_wh.items():
                base, wh = wk.rsplit("|", 1) if "|" in wk else (wk, "")
                if wh in _c_whs:
                    m = _by_c.setdefault(base, {})
                    for d, q in wd.items():
                        m[d] = m.get(d, 0) + q
            if not _by_c:
                _by_c = _by
            _mc = _csm({k: _sps(v) for k, v in _by_c.items()}, windows=[7, 14, 28], sparse="shrink")
            for _k, _v in _mc.get(7, {}).items():
                _st = _stats.setdefault("%s|__C__" % _k, {})
                _st["ds7_cut"] = _v
            _mc14 = _csm({k: _sps(v) for k, v in _by_c.items()}, windows=[14], sparse="shrink")
            _mc28 = _csm({k: _sps(v) for k, v in _by_c.items()}, windows=[28], sparse="shrink")
            for _k in _mc.get(7, {}):
                _st = _stats.setdefault("%s|__C__" % _k, {})
                _st["ds14_cut"] = _mc14[14].get(_k, 0)
                _st["ds28_cut"] = _mc28[28].get(_k, 0)
            _ma = _csm({k: _sps(v) for k, v in _by.items()}, windows=[7, 14, 28], sparse="shrink")
            _ma14 = _csm({k: _sps(v) for k, v in _by.items()}, windows=[14], sparse="shrink")
            _ma28 = _csm({k: _sps(v) for k, v in _by.items()}, windows=[28], sparse="shrink")
            for _k in _ma.get(7, {}):
                _st = _stats.setdefault("%s|__C__" % _k, {})   # 2026-10-30 全仓→C仓
                _st["ds7_cut"] = _ma[7].get(_k, 0)
                _st["ds14_cut"] = _ma14[14].get(_k, 0)
                _st["ds28_cut"] = _ma28[28].get(_k, 0)
            _mw = _csm({k: _sps(v) for k, v in _by_wh.items()}, windows=[7, 14, 28], sparse="shrink")
            _mw14 = _csm({k: _sps(v) for k, v in _by_wh.items()}, windows=[14], sparse="shrink")
            _mw28 = _csm({k: _sps(v) for k, v in _by_wh.items()}, windows=[28], sparse="shrink")
            for _k in _mw.get(7, {}):
                _st = _stats.setdefault(_k, {})
                _st["ds7_cut"] = _mw[7].get(_k, 0)
                _st["ds14_cut"] = _mw14[14].get(_k, 0)
                _st["ds28_cut"] = _mw28[28].get(_k, 0)
            # 2026-10-29 按 69码 直算(与物化同构: key = "sku|barcode|WH_ALL")
            try:
                for _bk, _bd in (_by_bc or {}).items():
                    _sk, _bc = _bk.rsplit("|", 1) if "|" in _bk else (_bk, "")
                    if not _bc:
                        continue
                    _c7 = _csm({_bk: _sps(_bd)}, windows=[7], sparse="shrink")
                    _c14 = _csm({_bk: _sps(_bd)}, windows=[14], sparse="shrink")
                    _c28 = _csm({_bk: _sps(_bd)}, windows=[28], sparse="shrink")
                    _st = _stats.setdefault("%s|%s|__C__" % (_sk, _bc), {})
                    _st["ds7_cut"] = _c7[7].get(_bk, 0)
                    _st["ds14_cut"] = _c14[14].get(_bk, 0)
                    _st["ds28_cut"] = _c28[28].get(_bk, 0)
            except Exception:
                pass
        except Exception as _e2:
            from routes.common import try_err
            try_err('repl', '物化直算降级失败', _e2)

    def _rd(sku, wh, bc=""):
        """读物化行(2026-10-29 按码):
        · 有 69码 → 优先读该码行("sku|barcode|wh"), 无则**降级**读合计行(不含69码)
        · 无 69码 → 直接读合计行
        """
        if bc:
            # 2026-10-30 降级只降码, 不跨维(与 rule_eval._ds_of 一致): 该维该码 → 该维合计
            _hit = _stats.get("%s|%s|%s" % (sku, bc, wh))
            if _hit:
                return _hit
        # 2026-10-29 key 统一为 sku|barcode|warehouse(空码即 sku||wh) —— 原 sku|wh 已不存在
        return _stats.get("%s||%s" % (sku, wh)) or {} if wh else {}

    def _fused_cut(st, sku=None, wh=None):
        """现场 rolling_predict(cut)——不固化 fused 列, 消费方口径一致(补货=削峰+shrink 融合)"""
        if st:
            return rolling_predict(float(st.get("ds7_cut") or 0),
                                   float(st.get("ds14_cut") or 0),
                                   float(st.get("ds28_cut") or 0))
        return 0.0

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as _ex:  # 2026-10-11 线程收敛(5→3——连接数降, 快照重查询仍并行)
        _fc = _ex.submit(_config, channel, mode)
        _fp = _ex.submit(_products, channel)
        _fi = _ex.submit(_inventory, channel, mode)
        _fs = _ex.submit(_season_factor, channel, mode)
        cfg = _fc.result()
        products = _fp.result()
        inv = _fi.result()
        season = _fs.result()
    # 2026-11-06 S3: 原直读平铺键(b_to_c_days + c_safety_days), 而补货配置里真正的覆盖值是
    #   `mode_bbcc_c_safety_days`(=3) —— 于是补货建议按 3 天备货、规则引擎按 6 天判缺口,
    #   同一件事差一倍。改走 core.metrics.periods(mode 前缀优先, 平铺兜底)。
    from core import metrics as _M
    _pp = _M.periods(cfg)
    lead = int(_pp["b2c"] if mode == "bbcc" else _pp["traditional"])

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
            _bc = str(r.get("barcode") or "").strip()   # 2026-10-29 按 69码(空码降级)
            if wt == "platform_b":
                b_stock[(sku, _bc)] = b_stock.get((sku, _bc), 0) + qty
                b_transit[(sku, _bc)] = b_transit.get((sku, _bc), 0) + tty
            elif wt == "own":
                pass
            else:
                a = agg.setdefault((sku, _bc), {"avail": 0, "transit": 0, "c_transit": 0, "safety": 0})
                a["avail"] += qty
                a["transit"] += tty
                a["c_transit"] += ctt
                a["safety"] += saf
        for (sku, _bc), st in agg.items():
            avail, c_transit, transit, safety = st["avail"], st["c_transit"], st["transit"], st["safety"]
            # 动态安全线周期(一盘货): C周期=b_to_c+c_safety, B周期=ship_to_b+safety_multiplier, 全周期=C+B
            # 2026-11-06 S3: 同样收敛到 core.metrics.periods —— 与规则引擎/断货卡/补货需求同一份
            _c_period = int(_pp["b2c"])
            _b_period = int(_pp["bbcc"]) - int(_pp["b2c"])
            _full_period = int(_pp["bbcc"])
            _st8 = _rd(sku, "__C__", _bc)
            ds7 = round(float(_st8.get("ds7_cut") or 0), 1)
            ds14 = round(float(_st8.get("ds14_cut") or 0), 1)
            ds28 = round(float(_st8.get("ds28_cut") or 0), 1)
            # 活性门控: 近 7/14 天有销售=近期活跃(×1); 仅 28 天历史销售=弱化(×0.5); 全零=0
            _act = 1.0 if (ds7 > 0 or ds14 > 0) else (0.5 if ds28 > 0 else 0.0)
            ds = round(_fused_cut(_st8) * season * _act, 1)
            # 需求门控(与 traditional 对齐): 日销 < 门槛 视为无实际需求, 缺口不触发补货
            has_demand = ds >= _MIN_DS
            # 安全天数(库存行 safety_days 优先, 回退配置)
            safety_days = float(cfg.get("safety_multiplier") or 0)
            effective_safety = round(ds * safety_days, 1) if has_demand else 0
            # C 缺口 = 日销×lead − C可用 − B→C调拨在途(保留1位小数)
            c_gap = max(round(ds * lead - avail - c_transit, 1), 0) if has_demand else 0
            b_available = b_stock.get((sku, _bc), 0)
            b_in_transit = b_transit.get((sku, _bc), 0)
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
            # 2026-11-04 结构化: segs 是唯一真源, note = zh_note(segs)(文案与 segs 永不漂移);
            #   前端按 note_segs 走 i18n 出双语; note(中文)仍产出, 导出/历史/告警不受影响
            t7 = "up" if ds7 > ds14 * 1.15 else ("down" if ds7 < ds14 * 0.85 else "flat")
            t14 = "up" if ds14 > ds28 * 1.15 else ("down" if ds14 < ds28 * 0.85 else "flat")
            trend = {"k": "trend", "t7": t7, "t14": t14}
            if ds > 0 and ds < 5 and combined_turnover_current is not None and combined_turnover_current > 90:
                trend["x"] = "very_low"
            elif ds7 == 0 and ds14 == 0 and ds28 > 0:
                trend["x"] = "down14"
            elif ds7 > ds14 * 1.15 and ds14 > ds28 * 1.1:
                trend["x"] = "up"
            elif ds7 < ds14 * 0.85 and ds14 < ds28 * 0.9:
                trend["x"] = "down"
            elif ds7 > ds14 * 1.15:
                trend["x"] = "up7"
            elif ds7 < ds14 * 0.85:
                trend["x"] = "down7"
            else:
                trend["x"] = "flat"
            segs = []
            if ds > 0:
                segs.append(trend)
            if c_gap > 0 and not uneconomical:
                if b_gap <= 0:
                    segs.append({"k": "c_sug", "n": suggested})
                else:
                    # 缺口标注准确: C缺口(c_gap) 被 B 仓覆盖后剩 B缺口(b_gap)——决定 B 补货量
                    segs.append({"k": "b_sug", "n": b_box, "c_gap": c_gap, "b_gap": b_gap,
                                 "consume": round(ds * b_ship_days + effective_safety, 1), "box": box})
                if b_gap > 0 and b_available <= 0:
                    segs.append({"k": "b_empty"})
                elif b_gap > 0:
                    segs.append({"k": "b_low", "n": b_available})
            if uneconomical:
                segs.append({"k": "unecon"})
            if b_gap > 0:
                c_cover = round((avail + transit) / ds, 1) if ds > 0 else 0
                b_idle = max(round(c_cover - b_ship_days, 1), 0)
            else:
                b_idle = 0
            b_free = int(cfg.get("b_free_days") or 15)
            if b_idle > b_free:
                segs.append({"k": "b_fee_over", "n": b_free})
            elif b_idle > b_free - 5:
                segs.append({"k": "b_fee_near", "n": b_free})
            tw90 = int(cfg.get("turnover_warning_90") or 90)
            has_replen = (suggested > 0 or b_box > 0)
            turn_check = combined_turnover if has_replen and combined_turnover is not None else combined_turnover_current
            if turn_check is not None and turn_check > tw90:
                segs.append({"k": "turn_over", "w": "after" if has_replen else "cur",
                             "v": turn_check, "t": tw90})
            elif turn_check is not None and turn_check > tw90 - 15:
                segs.append({"k": "turn_near", "w": "after" if has_replen else "cur",
                             "v": turn_check, "t": tw90})
            if ds <= 0:
                if b_available > 0:
                    segs.append({"k": "no_sales_b"})
                elif avail > 0:
                    segs.append({"k": "no_sales_c"})
                else:
                    segs.append({"k": "no_sales"})
            # 动态安全线预警(一盘货 BC 合计 vs 全周期; 只提示不参与计算)
            elif _full_period > 0:
                _bc_total = avail + c_transit + b_available + b_in_transit
                _days_left = _bc_total / ds if ds > 0 else 999
                if _days_left <= _full_period:
                    segs.append({"k": "safety_bc_urgent", "d": round(_days_left), "p": _full_period})
                elif _days_left <= _full_period + 2:
                    segs.append({"k": "safety_bc_near", "d": round(_days_left), "p": _full_period})
            if not segs:
                segs.append({"k": "ok"})
            # P1: 濒临断货反哺 —— 建议补>0 即 Adj-DOS≤补货周期(缺口), note 前置 🔴 已濒临
            if suggested > 0 or b_box > 0:
                segs.insert(0, {"k": "critical"})
            note = zh_note(segs)
            suggestions.append({
                # 2026-10-29 按码: 优先用库存行的 69码(按码分组), 无则回退商品档案
                "sku": sku, "barcode": _bc or prod.get("barcode", ""),
                "product_name": prod.get("product_name", ""), "brand": prod.get("brand", ""),
                "store": prod.get("store", ""), "category": prod.get("category", ""),
                "available_qty": avail, "safety_qty": round(ds * _full_period, 1) if ds > 0 else 0,
                "in_transit_qty": transit,
                "c_transit": c_transit, "b_transit": b_in_transit,
                "b_stock": b_available, "c_stock": avail, "b_gap": b_gap,
                "daily_sales": ds, "daily_sales_7": ds7,
                "daily_sales_14": ds14, "daily_sales_28": ds28,
                "daily_sales_60": round(_fused_cut(_rd(sku, "__C__")) or ds, 1),
                "raw_suggested": c_gap, "suggested_qty": suggested,
                "b_suggested": b_box, "b_replenish_raw": b_replenish,
                "days_to_empty": days_to_empty, "after_turnover": after_turnover,
                "c_turnover": c_turnover, "transit_turnover": transit_turnover,
                "combined_turnover_current": combined_turnover_current,
                "combined_turnover": combined_turnover,
                "note": note, "note_segs": segs,
            })
    else:
        # 传统: 逐仓(物化表逐仓行 cut 列——已含削峰+shrink, 与原 _wh_sm+shrink 口径一致)
        tw90 = int(cfg.get("turnover_warning_90") or 90)
        tw90 = int(cfg.get("turnover_warning_90") or 90)
        # 安全天数口径(与 bbcc 同构): mode_traditional_safety_multiplier → cfg["safety_multiplier"]
        # 静态 inventory.safety_qty 不再参与补货量(早期遗留, 仅看板断货卡/低库存规则作兜底)
        safety_days = float(cfg.get("safety_multiplier") or 0)
        for r in inv:
            if r.get("warehouse_type") != "platform":
                continue
            sku = r.get("sku")
            wh = r.get("warehouse")
            _bc = str(r.get("barcode") or "").strip()   # 2026-10-29 按 69码(空码降级)
            _st9 = _rd(sku, wh, _bc)
            w7 = round(float(_st9.get("ds7_cut") or 0), 1)
            w14 = round(float(_st9.get("ds14_cut") or 0), 1)
            w28 = round(float(_st9.get("ds28_cut") or 0), 1)
            # 活性门控(逐仓): 近 7/14 天该仓有销售=活跃(×1); 仅 28 天历史=弱化(×0.5)
            _act = 1.0 if (w7 > 0 or w14 > 0) else (0.5 if w28 > 0 else 0.0)
            ds = round(_fused_cut(_st9) * season * _act, 1)
            avail = int(r.get("available_qty") or 0)
            transit = int(r.get("in_transit_qty") or 0)
            effective_safety = round(ds * safety_days, 1) if ds > 0 else 0
            raw = ds * (lead + safety_days) - avail - transit
            suggested = _trad_suggested(ds, lead, safety_days, avail, transit)
            uneconomical = ds >= _MIN_DS and 0 < raw < 1
            _trad_period = lead + int(safety_days)
            # 动态安全线预警(逐仓: 该仓可售 avail+transit vs 传统周期, 只提示不参与计算)
            _hint_seg = None
            if ds > 0 and _trad_period > 0:
                _dl = (avail + transit) / ds
                if _dl <= _trad_period:
                    _hint_seg = {"k": "safety_urgent", "d": round(_dl), "p": _trad_period}
                elif _dl <= _trad_period + 2:
                    _hint_seg = {"k": "safety_near", "d": round(_dl), "p": _trad_period}
            prod = products.get(sku, {})
            box = int(prod.get("box_qty") or 1)
            box_qty = ((suggested + box - 1) // box * box) if suggested > 0 else 0
            after_turnover = round((avail + transit + box_qty) / ds, 1) if ds > 0 else 999
            _tn, _ts = _trad_note(ds, w7, w14, w28, avail, effective_safety, box_qty, suggested,
                                  after_turnover, tw90, uneconomical, _hint_seg)
            suggestions.append({
                "sku": sku, "barcode": _bc or prod.get("barcode", ""),
                "product_name": prod.get("product_name", ""), "brand": prod.get("brand", ""),
                "store": prod.get("store", ""), "warehouse": wh, "category": prod.get("category", ""),
                "available_qty": avail, "safety_qty": round(ds * _trad_period, 1) if ds > 0 else 0,
                "in_transit_qty": transit,
                "effective_safety": effective_safety,
                "daily_sales": round(ds, 1), "daily_sales_7": round(w7, 1),
                "daily_sales_14": round(w14, 1), "daily_sales_28": round(w28, 1),
                "daily_sales_60": round(_fused_cut(_rd(sku, "__C__")) or ds, 1),
                "suggested_qty": box_qty, "after_turnover": after_turnover,
                "days_to_empty": round(avail / ds, 1) if ds > 0 else 999,
                "note": _tn, "note_segs": _ts,
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


def _inventory(channel, mode="bbcc"):
    # 2026-10-29 修复(按码真根因): 原 SELECT 漏 barcode → 主循环 r.get("barcode") 恒 '' →
    #   agg key 退化为 (sku,'') 两码合并, 补货按码从未生效(输出 barcode 只是 products 兜底, 假象)
    # §15.174 取数裁剪(纯读侧, 两个分支都不用 own 行):
    #   bbcc 分支 `elif wt == "own": pass`、traditional 分支 `if wt != "platform": continue`
    #   —— own 仓行**一条都不用**。线上 jd 库存 90100 行里 own 10100 行(11%)。
    #   同时只取真正读到的 8 列(原 INVENTORY_COLS 12 列, 多 safety_days/locked_qty/
    #   product_name/beginning_stock 共 1.5MB)。
    #   过滤条件按 mode 走: bbcc 要 platform+platform_b, traditional 只要 platform。
    from routes.common import check_cols as _cc
    _wt = ("platform", "platform_b") if mode == "bbcc" else ("platform",)
    _cols = ("sku, COALESCE(barcode,'') AS barcode, warehouse, warehouse_type, "
             "available_qty, in_transit_qty, c_transit, safety_qty")
    _ph = ",".join(["%s"] * len(_wt))
    _rows = query("SELECT " + _cols + " FROM inventory WHERE channel=%s "
                  "AND warehouse_type IN (" + _ph + ")", [channel] + list(_wt))
    _cc(_rows, ("barcode", "warehouse"), "repl", "补货物料")
    return _rows

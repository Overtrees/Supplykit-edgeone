"""purchase 建议计算模块(2026-10-11 拆分——采购/滞销建议独立; purchase.py 减负)
函数: _build_purchase(采购建议) / disposal_suggestions(滞销建议)
依赖: biz.sales + replenishment 辅助(函数内延迟 import——防循环)
"""
import json
import time as _time
from datetime import datetime, timedelta, timezone

from db import query, one, execute, executemany
from routes.common import ok, fail, traced, try_err
from routes.analysis_cache import register as _register_cache, cache_get as _cache_get
from core.alert_lifecycle import close as _close_alerts  # 2026-11-03 D8
from core.note_segs import zh_note  # 2026-11-04 结构化备注: segs 单一真源, note=zh_note(segs)
from core.clock import now_cn

# 采购需求告警的单语句 upsert(2026-11-06 S2-4): 依赖 alerts.uq_alerts_dim。
#   warehouse / barcode 显式置空 —— 采购是 SKU 级语义(不按仓), 两个并发 builder
#   落进同一维度键, 不再各插一条。
# ⚠ 顺序不能改: reopen_count / created_at 必须在 `status='active'` 之前赋值,
#   因为 ON DUPLICATE KEY UPDATE 从左到右执行, 后面读到的已是更新后的 status。
_PURCHASE_ALERT_UPSERT = (
    "INSERT INTO alerts(alert_type, title, description, severity, source, related_sku, "
    "status, channel, warehouse, barcode) "
    "VALUES('purchase_need',%s,%s,'warning','purchase_engine',%s,'active',%s,'','') "
    "ON DUPLICATE KEY UPDATE "
    "reopen_count=COALESCE(reopen_count,0)+IF(status<>'active',1,0), "
    "created_at=IF(status<>'active', NOW(), created_at), "
    "title=VALUES(title), description=VALUES(description), "
    "closed_reason='', closed_at=NULL, status='active'"
)


# 内置默认滞销品类线(2026-10-25 抽取公共共享——get_slow_cats 接口与 disposal 计算同源, 防两处漂移)
_DEFAULT_SLOW_CATS = [
    {"name": "食品", "slow_days": 30, "shelf_months": 3,
     "cats": "酱油,酱料,调味汁,食用油,醋,料酒,蚝油,芝麻油,辣椒酱,拌面酱,老抽,生抽,"
             "陈醋,香醋,白醋,米醋,花椒油,藤椒油,辣椒油,芥末油,番茄酱,甜辣酱,沙拉酱,"
             "芝麻酱,花生酱,豆瓣酱,豆豉,腐乳,糟卤,鱼露,咖喱块,咖喱粉,五香粉,孜然粉,"
             "花椒粉,辣椒粉,胡椒粉,十三香,卤料包,炖肉料,鸡精,味精,白糖,冰糖,红糖,"
             "麦芽糖,蜂蜜,黄酒,米酒,薯片,虾条,爆米花,坚果,瓜子,花生,饼干,威化,"
             "巧克力,糖果", "enabled": True},
    {"name": "个护家清", "slow_days": 60, "shelf_months": 6,
     "cats": "洗衣液,洗洁精,洗手液,消毒液,纸巾,湿巾,垃圾袋,保鲜膜,保鲜袋,收纳盒",
     "enabled": True},
]


def _build_purchase(channel, mode, days=28, with_alerts=False):
    """采购建议计算体(共享缓存 builder, 返回全量列表)

    2026-11-06 S2-1 审计 P0-10: **读接口零副作用**。
      原来这个 builder 里写 purchase_need 告警 —— 后果是"有没有人打开采购页"决定了
      告警是否更新: 没人看 → 告警停在最后一次被浏览的时刻, 而看板上采购卡却显示"正常"。
      更糟的是上一轮把缓存粒度改成一页一条之后, **每翻一页就重写一遍告警**
      (线上实证: 同 SKU 13 条 purchase_need, 正好等于一次分页重算的次数)。
      现在 builder 只算不写; 写入由 core.alert_jobs 的写入侧入口驱动。
    :param with_alerts: 仅供 alert_jobs 在"算完立刻同步"时使用(同一进程内复用结果, 不重算)
    """
    from biz.sales import load_daily_sales, calc_sales_multi

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
    except Exception as _e:
            try_err('purchase', '静默降级', _e)

    from routes.common import ch_supports_b as _csb
    _ch_b = _csb(channel)   # 该渠道是否走 BBCC(B 仓调拨链) —— 原 `channel == "jd"`
    # §15.176 读侧并行(不改任何算法/口径, 只消除无依赖串行): 线上 list_head.timing
    #   (2026-11-08 14:56) purchase calc=3.0s / sql=4.0s/10 条 —— 四段取数严格串行:
    #   配置 → 商品(1 万行) → 日销物化(2 万行) → 库存(9 万行), 每段都在等自己的往返+传输。
    #   商品与库存两段与日销互不依赖, 先发出去并行取, 主线程照旧做日销与融合;
    #   `_f_inv.result()` 在库存循环处才取, 不改变任何计算顺序与结果(逐字段等价见验收 triple)。
    from concurrent.futures import ThreadPoolExecutor as _TPE
    _ex = _TPE(max_workers=2)
    _f_prod = _ex.submit(
        query,
        "SELECT sku, product_name, barcode, brand, store, category, box_qty, price, "
        "supplier_code FROM products WHERE (deleted_at IS NULL OR deleted_at='') "
        "AND channel=%s", [channel])
    # §15.180 下推聚合(纯读侧, 算法一行未动): 主循环对每个 (sku, 69码) 只做无条件累加
    #   (available/transit/safety) + 按 warehouse_type 分桶 + safety_days 取 max +
    #   own 首仓名, 全是可结合的聚合 —— 下推到 SQL 后线上 jd 实测 9 万行 → 3 万行。
    #   `warehouse_type<>'platform_b' OR 1=1` 复刻「非 BBCC 渠道/非 bbcc 模式跳过 B 仓行」,
    #   且该 continue 发生在累加之前, 故等价。
    #   own_warehouse 由「遍历到的第一个」改为 MIN(warehouse): 多自有仓时取值口径从
    #   "返回序首个"（本身不确定）变为"字典序最小"，单仓场景完全一致; 该字段只用于展示。
    _inc_b = 1 if (_ch_b and mode == "bbcc") else 0
    _f_inv = _ex.submit(
        query,
        "SELECT sku, COALESCE(barcode,'') AS bc, warehouse_type, "
        "SUM(available_qty) AS available_qty, SUM(in_transit_qty) AS in_transit_qty, "
        "SUM(safety_qty) AS safety_qty, MAX(safety_days) AS safety_days, "
        "MIN(CASE WHEN warehouse_type='own' THEN warehouse END) AS warehouse "
        "FROM inventory WHERE channel=%s AND (warehouse_type<>'platform_b' OR %s=1) "
        "GROUP BY sku, bc, warehouse_type", [channel, _inc_b])
    products = {}
    for p in _f_prod.result():
        products[p.get("sku")] = p
    if not products:
        _ex.shutdown(wait=False)
        # 2026-11-06 P1-4: 这里原来 `return ok({"suggestions": []})` —— 返回**已包装的 dict**,
        #   而本函数其余所有路径都 `return result`(list)。契约不一致的后果是连锁的:
        #     ① 调用方 `shape(rows)` 迭代 dict 得到的是 key 字符串("ok"/"data");
        #     ② need_only=1 时 `r.get("actual_purchase")` 对字符串调用 .get → AttributeError → 500
        #        (看板「采购&补货」卡正好走 need_only=1);
        #     ③ 不筛时返回 {"suggestions": <dict>}, 前端 `data.suggestions` 不是数组 → 表格空白;
        #     ④ 更糟的是这个坏值会被写进 analysis_cache, 300s 内所有请求都复现。
        #   新建渠道 / 重置后未导商品时(商品库为空)就是这条路径。
        return []

    # 日销 14+28 双窗口 → fused(趋势加权)
    # 2026-10-17 物化短路: 读物化 plain ds14/ds28 现场算 14/28 自权重(与直算 calc_sales_multi 逐 SKU 等价——
    # 自权重公式同下; 物化缺失(fused 空)时回退直算, 保证全 SKU 覆盖)——
    # 曾 load_daily_sales(全 products 2000 SKU 分片拉快照)+calc_sales_multi(14/28) 每次 2s+
    fused = {}
    _rws = {}
    _bc_stats = {}
    try:
        from biz.sales import get_sku_sales_stats, WH_C, slim_on
        if slim_on():
            # §15.172 取数裁剪(与 repl 同一做法, 单一真源 `biz.sales.slim_on`):
            #   一次 `barcode="all"` 取「合计 + 全部按码」, 只取 ds14/ds28 两列, 按行分流。
            #   原实现是 2 次往返、全 11 列; 线上 purchase `calc=3.5s` 里 `sql=3.3s/12 条`。
            _all = get_sku_sales_stats(channel, wh=WH_C, days=28, barcode="all",
                                       fields=("ds14", "ds28")) or {}
            for _k, _r2 in _all.items():
                _bc = str(_r2.get("barcode") or "")
                if _bc:
                    _bc_stats["%s|%s" % (str(_r2.get("sku") or ""), _bc)] = _r2
                else:
                    _rws[_k] = _r2
        else:
            _rws = get_sku_sales_stats(channel, wh=WH_C, days=28) or {}   # 2026-10-30 全仓→C仓(__ALL__ 已删)
            # 2026-10-29 按码: 读按码行, key = "sku|barcode"
            try:
                # 2026-10-29 修复(N+1): 原按 codes 逐个 get_sku_sales_stats —— 1000 码 × (CREATE IF NOT EXISTS
                # + COUNT + SELECT) ≈ 3000 次往返 → 冷启动固定 46.00s(实测 46.0/49.5s)。
                # 改 barcode='*' 一次取全部按码行(与断货/进销存同法), 往返 1 次。
                for _r2 in (get_sku_sales_stats(channel, wh=WH_C, days=28, barcode="*") or {}).values():
                    _bc = str(_r2.get("barcode") or "")
                    if _bc:
                        _bc_stats["%s|%s" % (str(_r2.get("sku") or ""), _bc)] = _r2
            except Exception as _ebc:
                try_err('purchase', '按码物化读降级', _ebc)
                _bc_stats = {}
        for _r in (_rws or {}).values():
            _sku = str(_r.get("sku") or "")
            _d14 = float(_r.get("ds14") or 0)
            _d28 = float(_r.get("ds28") or 0)
            if _d14 > _d28 * 1.15:
                w14, w28 = 0.55, 0.45
            elif _d14 < _d28 * 0.85:
                w14, w28 = 0.35, 0.65
            else:
                w14, w28 = 0.20, 0.80
            fused[_sku] = round(_d14 * w14 + _d28 * w28, 1)
    except Exception as _e:
        try_err('purchase', '物化读降级(走直算)', _e)
    if not fused:
        daily = load_daily_sales(days, channel, skus=set(products.keys()))
        multi = calc_sales_multi(daily, windows=[14, 28])
        s14m, s28m = multi[14], multi[28]
        for sku in set(list(s14m) + list(s28m)):
            a, b = s14m.get(sku, 0), s28m.get(sku, 0)
            if a > b * 1.15:
                w14, w28 = 0.55, 0.45
            elif a < b * 0.85:
                w14, w28 = 0.35, 0.65
            else:
                w14, w28 = 0.20, 0.80
            fused[sku] = round(a * w14 + b * w28, 1)
    # 2026-10-17 物化短路补 s14m/s28m(第 175 行展示字段引用——曾物化命中时 UnboundLocalError → 采购页无数据):
    # 从物化行现场取(与直算同构——物化行即三窗口底料)
    s14m = {}
    s28m = {}
    # 2026-10-29: 复用上面同一次物化读(原再次 _gss2(...) 全表查一遍, 纯重复往返)
    try:
        for _v2 in (_rws or {}).values():
            s14m[_v2.get("sku") or ""] = float(_v2.get("ds14") or 0)
            s28m[_v2.get("sku") or ""] = float(_v2.get("ds28") or 0)
    except Exception as _e14:
        try_err('purchase', 's14/s28 取值降级', _e14)

    # 系统总库存(own/plat/B 口径: B 仓仅 jd+bbcc 参与链路)
    inv = {}
    # 2026-10-29 按 69码: 库存聚合 key = (sku, barcode), 空码归入 "" 组(降级)
    for i in _f_inv.result():
        s = (i.get("sku"), str(i.get("bc") or "").strip())
        wt = i.get("warehouse_type") or "platform"
        # 2026-10-30 配置化: B 仓参与条件 = 渠道支持 BBCC 体系 且 当前为 bbcc 模式
        if wt == "platform_b" and (not _ch_b or mode != "bbcc"):
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
    for (sku, _bc), st in inv.items():
        # 2026-10-29 按 69码: 有码读按码日销, 缺失则**降级**读合计(不含69)
        _ds_src = _bc_stats.get("%s|%s" % (sku, _bc)) if _bc else None
        if _ds_src:
            _d14 = float(_ds_src.get("ds14") or 0)
            _d28 = float(_ds_src.get("ds28") or 0)
            if _d14 > _d28 * 1.15:
                _w14, _w28 = 0.55, 0.45
            elif _d14 < _d28 * 0.85:
                _w14, _w28 = 0.35, 0.65
            else:
                _w14, _w28 = 0.20, 0.80
            _base_ds = round(_d14 * _w14 + _d28 * _w28, 1)
        else:
            _base_ds = fused.get(sku, 0)   # 无码 / 按码缺失 → 降级合计
        ds = round(_base_ds * active_factor, 1)
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
        # 2026-11-04 结构化: segs 唯一真源 → note=zh_note(segs); 前端按 note_segs 走 i18n
        segs = []
        # 动态安全线预警(采购周期, 供应商级参数优先; 可售=系统仓+自有仓; 只提示不参与计算)
        _purchase_period = int(_lead) + int(_safe_days)
        if _purchase_period > 0:
            # 2026-10-29 修复: sys_total 已累加所有仓型(含 own), 再加 own 会重复计
            #   → 可撑天数虚高 → 该报的库存告急反而不报
            _avl_all = sys_total
            _dl = _avl_all / ds
            if _dl <= _purchase_period:
                segs.append({"k": "p_urgent", "d": round(_dl), "p": _purchase_period})
            elif _dl <= _purchase_period + 2:
                segs.append({"k": "p_near", "d": round(_dl), "p": _purchase_period})
        if purchase_qty > 0:
            # 通俗格式(对齐 bbcc 风格): 需采购N件(箱规,实购) · 补后周转
            if box_qty > 1:
                segs.append({"k": "p_need_box", "n": purchase_qty, "box": box_qty,
                             "act": actual_purchase})
            else:
                segs.append({"k": "p_need", "n": purchase_qty})
            if target_turn > 0:
                segs.append({"k": "p_turn", "v": after_turnover, "target": target_turn,
                             "cmp": ">" if after_turnover > target_turn else "<"})
        if not segs:
            segs.append({"k": "ok"})
        note = zh_note(segs)
        result.append({
            "sku": sku, "barcode": _bc or prod.get("barcode", ""),
            "product_name": prod.get("product_name") or sku, "brand": prod.get("brand", ""),
            "store": prod.get("store", ""), "warehouse": st["own_warehouse"],
            "category": prod.get("category", ""),
            "sys_available": st["available"], "sys_transit": st["transit"], "sys_total": sys_total,
            "own_available": st["own_avail"], "own_transit": st["own_transit"],
            "b_transit": st["b_transit"], "plat_available": st["plat_avail"],
            "plat_transit": st["plat_transit"], "b_available": st["b_avail"],
            "safety_qty": round(ds * _purchase_period, 1), "daily_sales": ds,
            "daily_sales_14": round(s14m.get(sku, 0), 1), "daily_sales_28": round(s28m.get(sku, 0), 1),
            "daily_sales_60": round(fused.get(sku, 0), 1),
            "supplier_code": _sup,
            "purchase_qty": purchase_qty, "box_qty": box_qty, "actual_purchase": actual_purchase,
            "after_stock": after_stock, "after_turnover": after_turnover,
            "target_turnover": target_turn, "days_to_empty": days_to_empty,
            "note": note, "note_segs": segs,
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
                # 2026-11-04 结构化: 追加 MOQ 片段并重派 note(不再手拼字符串, 防两份漂移)
                _r.setdefault("note_segs", []).append({
                    "k": "p_moq", "sup": _sup, "moq": g["moq"], "tot": g["total_raw"],
                    "old": _old, "new": _r["purchase_qty"]})
                _r["note"] = zh_note(_r["note_segs"])

    # 采购告警 purchase_need —— 2026-11-06 S2-1: 只在显式要求时写(读接口零副作用)。
    #   写入逻辑本身已抽到 sync_purchase_alerts(), 这里只是"算完顺手同步"的快捷入口,
    #   供 core.alert_jobs 在同一进程内复用本次结果(不重算一遍)。
    if with_alerts:
        sync_purchase_alerts(channel, result)
    _ex.shutdown(wait=True)      # §15.176: 两个预取线程已 result() 过, 这里只回收池

    result.sort(key=lambda x: x["days_to_empty"])
    return result


def sync_purchase_alerts(channel, result):
    """把一次采购建议结果同步成 purchase_need 告警(写入侧专用, **不得在读接口里调用**)

    判定: 需采购且可撑 <14 天 → active; 不再需要采购 → 关闭。
    返回 {"opened": n, "closed": n} 供 job 日志。

    2026-11-06 S2-4: 写入改单语句 upsert(依赖 alerts.uq_alerts_dim)。
      · 原来"先 SELECT 已有 active SKU 集合 → 不在集合里才裸 INSERT"是 check-then-act,
        并发两个 builder 各读到"不存在"就各插一条(线上实证同 SKU 13 条);
      · 建唯一键后裸 INSERT 会直接 1062, 被外层 except 吞掉 → 告警整批不更新。
      · existing 只用于判断"要不要关闭"(那是业务条件, 库约束管不了), 写入不再依赖它。
    """
    try:
        # 采购是 SKU 级(不按仓): warehouse / barcode 置空 → 落在同一维度键上,
        #   两个并发 writer 不再各插一条。
        _existing = set()
        for r in query("SELECT related_sku FROM alerts WHERE alert_type='purchase_need' "
                       "AND status='active' AND channel=%s", [channel]):
            _existing.add(r.get("related_sku"))
        ins, upd = [], []
        for _r in result:
            sku = _r.get("sku")
            if _r.get("purchase_qty", 0) > 0 and _r.get("days_to_empty", 999) < 14:
                ins.append(("需采购: %s" % _r.get("product_name"),
                            # 2026-10-30 口径对齐: 低库存/断货/采购统一用"可售(可用+在途)",
                            #   原此处只报 sys_available(纯可用) 与另两处口径不一致
                            "可售%d件, 建议采购%d件, 可撑%d天" % (_r.get("sys_total") or 0,
                                                            _r.get("actual_purchase") or 0,
                                                            _r.get("days_to_empty") or 0),
                            sku, channel))
            elif _r.get("purchase_qty", 0) == 0 and sku in _existing:
                upd.append(sku)
        for i in range(0, len(ins), 100):
            executemany(_PURCHASE_ALERT_UPSERT, ins[i:i + 100])
        if upd:
            ph = ",".join(["%s"] * len(upd))
            # ph 为本函数生成的 %s 占位符串(非用户数据), 直接拼接安全; channel 走参数化
            _close_alerts("alert_type='purchase_need' AND related_sku IN (" + ph + ") AND channel=%s",
                          upd + [channel], "purchase_converted", status="closed")
        return {"opened": len(ins), "closed": len(upd)}
    except Exception as _e:
        try_err('purchase', '采购告警同步降级', _e)
        return {"opened": 0, "closed": 0}

def _disposal_compute(channel="jd", debug: int = 0):
    """滞销候选**全量计算**(唯一公式所在), 返回 (已排序全量 items, perf)。

    2026-11-07 P0(本轮): 从 `disposal_suggestions` 拆出 —— 物化重建要算它、
      现算兜底也要算它, 两处必须同一份代码(红线 9: 算法不复制)。
      读侧 `disposal_suggestions` 只负责"读物化还是现算" + search/分页/包装。
    · 不含 search / 分页 / ok() 包装 —— 这些归读侧, 与物化 SQL 同源(见 list_store._where)。
    · 依赖 `now_cn()`(临期/在库/30天/14天窗口), 业务日期是确定性输入域 →
      list_store 把日期纳入 fp, 跨天必重建, 不会静默返回昨天的 level。

    判据不变: ① 临期 → black ② B仓超免费期 → 成本压力
      ③ 滞销主判据(品类滞销线/观察线) → yellow/observe ④ yellow + 成本压力 → red
    """
    import time as _tm_d
    _T0 = _tm_d.time()
    _PF = {}

    def _mk(name):
        if debug:
            _PF[name] = round((_tm_d.time() - _T0) * 1000)
    from routes.common import ch_supports_b as _csb2
    _ch_b = _csb2(channel)   # 该渠道是否走 BBCC(B 仓调拨链)
    from biz.sales import calc_sales_multi
    now = now_cn().replace(tzinfo=None)

    # 2026-11-07: 三个配置项原来是三条独立 `SELECT value ... WHERE key=?` —— 每条都要付
    #   一次往返(线上固定 0.23s/条)。一次读回本渠道全部配置, 后面纯内存取。
    _cfg_all = {}
    try:
        for _cr in query("SELECT `key`, value FROM replenishment_config WHERE channel=%s", [channel]):
            if _cr.get("key"):
                _cfg_all[str(_cr["key"])] = _cr.get("value")
    except Exception as _ecf:
        try_err('purchase', '滞销配置读取降级', _ecf)

    def _cfg_number(key, default):
        try:
            return int(_cfg_all.get(key) or default)
        except Exception:
            return default

    def _cfg_list(key, default):
        _v = _cfg_all.get(key)
        if _v:
            try:
                d = json.loads(_v)
                # 空数组视为"未自定义"→ 返回内置默认(避免丢个护家清等品类配置, 全按食品线误判)
                if isinstance(d, list) and d:
                    return d
            except Exception as _e:
                    try_err('purchase', '静默降级', _e)
        return default

    _mk("cfg")
    cat_cfg = _cfg_list("slow_cats", _DEFAULT_SLOW_CATS)
    b_free = _cfg_number("b_free_days", 15)
    fund_threshold = _cfg_number("slow_fund_threshold", 10000)

    products = {}
    for r in query("SELECT sku, product_name, price, volume, category, best_before, brand "
                   "FROM products WHERE (deleted_at IS NULL OR deleted_at='') AND channel=%s",
                   [channel]):
        products[r.get("sku")] = r
    _mk("products")
    # 最后销售日(按 69码) —— 2026-11-07: 改读物化 `sku_sales_stats.last_sale`。
    #   原来这里扫两遍: `daily_sales_snapshot` 全渠道 GROUP BY(10 万行, 实测 1.28s)
    #   + 近 90 天 orders GROUP BY(30 万行, 0.73s) —— 表达的都是"这个码最后哪天卖过",
    #   而物化表里早就有这条(补货/断货读的就是它)。一次物化读同时拿到 ds28 与 last_sale。
    #   降级规则保持不变: 该码无记录且档案有码、且该 SKU 只有空码组 → 用 SKU 合计。
    last_order = {}      # sku → 最后销售日(所有码合计, 仅无码行/历史未归码降级用)
    last_order_bc = {}   # (sku, 69码) → 最后销售日
    _sku_has_bc = set()  # 该 SKU 存在过非空 69码 的销售记录(用于区分"冷码"与"整体未归码")
    # 28 天日销 —— SKU 级(降级用) + 按码"sku|69码"(主用)
    # 2026-11-07 P0(去重叠加): 原来这里 `load_daily_sales_grouped(28, 全渠道 1 万 SKU)`
    #   把 28 天快照(约 20 万行)全拉回 Python 再算一遍三窗口 —— 而同一份结果早已物化在
    #   `sku_sales_stats`(补货/断货/健康读的就是它)。线上实测 disposal 5~8s, 大头就是这趟。
    #   现在直接读物化 __C__ 行: 合计行 → daily_map, 按码行 → daily_bc。
    #   口径等价性: 该渠道只有 platform 仓产生销售(__ALL__ 与 __C__ 逐 SKU 0 差异, 见
    #   biz.sales._rebuild 的注释), 且 ds28 与 calc_sales_multi(plain)[28] 是同一函数同一窗口。
    daily_map = {}
    daily_bc = {}
    if products:
        from biz.sales import get_sku_sales_stats as _gss_d, WH_C as _WH_C_D
        # 2026-11-07 定位修正: 不带 wh 会把该渠道**全部仓 × 全部码** 12.6 万行拉回来(实测 2.7s) ——
        #   而滞销只需要"全国 C 仓"这一维(销售只在 platform 仓发生, own 不产生销售)。
        #   加 wh=__C__ 后行数降到约 2 万, 口径与页面/补货一致。
        _rws_d = _gss_d(channel, skus=list(products.keys()), days=28, wh=_WH_C_D,
                        barcode="all", fields=("ds28", "last_sale")) or {}
        for _rd in _rws_d.values():
            _sd = str(_rd.get("sku") or "")
            _bd = str(_rd.get("barcode") or "")
            # 末销: 跨仓取最大(同一码可能分布在多个仓, 任一仓卖过就算该码卖过)
            _lsd = str(_rd.get("last_sale") or "")[:10]
            if _bd:
                _sku_has_bc.add(_sd)
                if _lsd and _lsd > last_order_bc.get((_sd, _bd), ""):
                    last_order_bc[(_sd, _bd)] = _lsd
            if _lsd and _lsd > last_order.get(_sd, ""):
                last_order[_sd] = _lsd
            # 日销只取 __C__(全国 platform 合计) —— 与页面/补货同口径
            if str(_rd.get("warehouse") or "") != _WH_C_D:
                continue
            if _bd:
                daily_bc["%s|%s" % (_sd, _bd)] = float(_rd.get("ds28") or 0)
            else:
                daily_map[_sd] = float(_rd.get("ds28") or 0)
    _mk("sales_stats")
    # B 仓入库批次(到仓日期)
    b_arrival = {}
    for r in query("SELECT sku, arrival_date FROM purchase_orders "
                   "WHERE channel=%s AND arrival_date IS NOT NULL AND arrival_date!=''", [channel]):
        try:
            b_arrival[str(r.get("sku"))] = datetime.strptime(str(r.get("arrival_date"))[:10], "%Y-%m-%d")
        except Exception as _e:
                try_err('purchase', '静默降级', _e)
    _mk("b_arrival")
    # 已处置(30 天内)
    disposed = {}
    cutoff30 = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    for r in query("SELECT sku, warehouse, action FROM disposal_records "
                   "WHERE channel=%s AND created_at>=%s", [channel, cutoff30]):
        disposed[(str(r.get("sku")), str(r.get("warehouse") or ""))] = str(r.get("action") or "")
    _mk("disposed")
    # 伪滞销线索(近14天补货建议)
    pseudo = set()
    c14 = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    for r in query("SELECT DISTINCT related_sku FROM alerts WHERE channel=%s "
                   "AND alert_type IN ('replenish','rewrite') AND created_at>=%s "
                   "AND related_sku IS NOT NULL AND related_sku!=''", [channel, c14]):
        pseudo.add(str(r.get("related_sku")))

    _mk("pseudo")
    _cat_match = {}
    _ld_days = {}
    # §15.176: best_before 只有几百个不同取值(同 SKU 多仓/多码共享同一日期), 但原实现对
    #   **每一行库存**(9 万行)都 strptime 一次 —— 线上 debug 分段实测 inventory_loop 1.6s,
    #   属纯 CPU 重复解析。按值缓存后同一日期只解析一次(与 _ld_days 同款, 结果逐行等价)。
    _bb_dd = {}
    sug = {"black": "临期紧急处理(促销/退供)", "red": "退货供应商/清仓甩卖",
           "yellow": "补货降量/持续跟踪", "observe": "继续观察/补货降量"}
    items = []
    _mk("cfg2")
    # 2026-10-29 按 69码: 库存行已按码拆分 → 处置建议也按 (sku, barcode, 仓) 出
    # 2026-11-07: `available_qty<=0` 原先在 Python 里跳过, 但行仍要从 DB 传回来 ——
    #   9 万行里这类占三成, 下推到 WHERE 直接少传一批(库存为 0 本就不产生滞销建议)。
    for r in query("SELECT sku, COALESCE(barcode,'') AS bc, warehouse, warehouse_type, "
                   "available_qty FROM inventory WHERE channel=%s AND available_qty>0", [channel]):
        sku = str(r.get("sku") or "")
        _bc = str(r.get("bc") or "").strip()
        wh = str(r.get("warehouse") or "")
        wht = str(r.get("warehouse_type") or "")
        avail = int(r.get("available_qty") or 0)
        if avail <= 0:
            continue
        p = products.get(sku)
        if not p:
            continue
        cat = p.get("category") or ""
        # 2026-11-07 CPU 热点: 原来对**每一行库存**(9 万行)都遍历一遍品类配置并 split 字符串 ——
        #   品类实际只有几十种, 结果是同一个 category 被匹配上万次。线上冷实例实测该循环
        #   占 26s(0.29ms/行, 正常应是微秒级)。按 category 记忆化后只算一次。
        if cat in _cat_match:
            match = _cat_match[cat]
        else:
            match = None
            for c in cat_cfg:
                if c.get("enabled") is False:
                    continue
                if any(w.strip() and w.strip() in cat for w in str(c.get("cats") or "").split(",")):
                    match = c
                    break
            _cat_match[cat] = match
        slow_days = int(match.get("slow_days", 30)) if match else 30
        shelf_m = int(match.get("shelf_months", 3)) if match else 3
        cat_name = (match.get("name") or "未归类") if match else "未归类(默认食品线)"
        # 末销/日销按 69码(2026-11-01): 多码时冷码不被热码稀释
        #   by_sku_bc_wh 只装非空码 → 码组缺失有两种含义, 必须分开:
        #     · 该 SKU 已存在别的码组 → 本码确实没卖过(冷码), 不降级, 否则滞销被掩盖
        #     · 该 SKU 只有空码组 → 历史未归码, 降级到 SKU 合计
        _ldbc = last_order_bc.get((sku, _bc))
        if _ldbc is None:
            _ldbc = last_order.get(sku, "") if (_bc and sku not in _sku_has_bc) else ""
        ld = _ldbc or ""
        # 2026-11-04 去兜底: 从未销售 → days_zero = None(前端显示「从未销售」), 不再用 999 冒充;
        #   末销日期在未来(数据错) → 如实给负数, 不夹 0。
        # 日期解析同样按值缓存: 9 万行里 ld 只有几百个不同取值, strptime 是纯 CPU 浪费
        if not ld:
            days_zero = None
        elif ld in _ld_days:
            days_zero = _ld_days[ld]
        else:
            days_zero = (now - datetime.strptime(ld, "%Y-%m-%d")).days
            _ld_days[ld] = days_zero
        # 阈值/排序比较键: 从未销售 = 最滞销(仅内部用, 不写进返回值/不展示)
        _dz = float("inf") if days_zero is None else days_zero
        _dzs = "从未销售" if days_zero is None else "%d天未销售" % days_zero
        _kbc = "%s|%s" % (sku, _bc)
        if _kbc in daily_bc:
            daily = float(daily_bc[_kbc] or 0)
        elif _bc and sku not in _sku_has_bc:
            daily = float(daily_map.get(sku, 0) or 0)   # 整体未归码 → 降级
        else:
            daily = 0.0
        # 2026-11-04 去兜底: 无销量 → 周转 None(前端显示「—」), 不再返回 999.0 假天数
        turnover = round(avail / daily, 1) if daily > 0 else None
        fund = round(avail * float(p.get("price") or 0), 0)
        reason, level, b_storage = [], None, None
        # ① 临期风险(black 紧急)
        bb = str(p.get("best_before") or "")[:10]
        shelf_days = shelf_m * 30
        if bb:
            try:
                if bb in _bb_dd:
                    dd = _bb_dd[bb]
                else:
                    dd = _bb_dd[bb] = (datetime.strptime(bb, "%Y-%m-%d") - now).days
                if dd <= shelf_days:
                    level = "black"
                    reason.append("距保质期%d天(<%d月临期线)" % (dd, shelf_m) if dd >= 0
                                  else "已过期%d天" % (-dd))
            except Exception as _e:
                    try_err('purchase', '静默降级', _e)
        # ② B仓超免费期(仓储费成本)
        if wht == "platform_b" and _ch_b:
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
        if _dz >= slow_days:
            if level is None:
                level = "yellow"
            reason.append("%s: %s(超%d天线)" % (cat_name, _dzs, slow_days))
        elif _dz >= observe_days:
            if level is None:
                level = "observe"
            reason.append("%s: %s(接近%d天线, 建议观察)" % (cat_name, _dzs, slow_days))
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
            "sku": sku, "barcode": _bc, "product_name": p.get("product_name") or sku,
            "channel": channel,
            "warehouse": wh, "warehouse_type": wht, "category": cat,
            "stock": avail, "turnover_days": turnover, "fund_occupied": fund,
            "daily_sales": round(daily, 1), "days_zero": days_zero,
            "cat_line": cat_name, "slow_days": slow_days,
            "level": level, "reason": reason, "suggestion": sug.get(level, ""),
            "b_storage": b_storage, "best_before": bb, "brand": p.get("brand") or "",
            # 2026-10-29 按码: 先查 (sku,barcode,仓), 缺失降级 (sku,仓) —— 兼容旧标记
            "disposed": ((sku, _bc, wh) in disposed) or ((sku, wh) in disposed),
            "disposed_action": disposed.get((sku, _bc, wh)) or disposed.get((sku, wh), ""),
        })
    _mk("inventory_loop")
    # 2026-11-07 P0: 排序补 (sku,barcode,warehouse) 成**全序 tie-breaker**(红线 12)。
    #   原来 key 只有 (level, -days_zero), 同值行顺序取决于 inventory 物理返回序(TiDB 不保证)。
    #   物化落 seq 后读侧按 seq 切, 与现算的 Python sort 会错位(§15.154 断货快照同教训)。
    items.sort(key=lambda x: ({"black": 0, "red": 1, "yellow": 2, "observe": 3}.get(x["level"], 9),
                               -(x["days_zero"] if x["days_zero"] is not None else 1e9),
                               str(x.get("sku", "")), str(x.get("barcode", "")),
                               str(x.get("warehouse", ""))))
    _mk("total")
    return items, _PF


def _disposal_respond(channel, page, page_size, search, debug):
    """现算路径的响应包装: 唯一公式 _disposal_compute + 同源筛选/分页。

    筛选走 `list_page.shape`(内部 filter_rows) —— 与物化读侧的 SQL `_where` 逐字等价,
    不复制第二份筛选(红线 9)。debug 保留真实分段计时(物化命中时没有分段, 故只现算才带)。
    """
    from core.list_page import shape as _shape
    items, _PF = _disposal_compute(channel, debug)
    _it, _total = _shape(items, search=search, page=page, page_size=page_size)
    if page > 0 and page_size > 0:
        _out = {"items": _it, "total": _total, "page": page, "page_size": page_size}
        if debug:
            _out["_perf"] = _PF
        return ok(_out)
    if debug:
        return ok({"_perf": _PF, "items": _it})
    return ok(_it)


def disposal_suggestions(channel: str = "jd", page: int = 0, page_size: int = 0,
                         search: str = "", debug: int = 0,
                         legacy: int = 0, state: int = 0, force: int = 0):
    """滞销处置建议读侧: 先读物化, fp 不命中退回现算(慢但正确)+后台重建。

    2026-11-07 P0 根治「每次请求全量现算 2.5s」: 与 unit_store/own_store/list_store 同骨架,
      `_disposal_compute` 算一次落 seq, 读侧按 seq 分页; 重建由域计数 fp(含业务日期)驱动,
      在后台, 无 TTL。判据/形状契约逐字不变:
      · 分页 {items,total,page,page_size} · 裸调返回列表 · debug 带 _perf 分段。
    debug / legacy 强制现算(诊断 + 线上逐字段对账); state/force 为物化四维诊断口。
    """
    from core import list_store as _LS
    scope = "disposal|%s" % channel
    if state or force:
        if force:
            _LS.register_scope("disposal", scope, channel, "", 0, "")
            _rb = _LS.build("disposal", scope, force=True)
            return ok({"rebuild": _rb, "state": _LS.status("disposal", scope)})
        return ok(_LS.status("disposal", scope))
    if debug or legacy:
        return _disposal_respond(channel, page, page_size, search, debug)
    _d = _LS.serve("disposal", scope, channel, "", 0, "",
                   lambda: _disposal_compute(channel)[0],
                   search=search, page=page, page_size=page_size)
    # §15.161: __swr 放响应顶层, 无分页裸调的 data 仍是纯列表(形状契约)
    _hd = _LS.pop_swr(_d)
    # 无分页裸调: 旧契约 data 直接是列表(前端 `(d.items || d || [])` 兼容, 但形状要守住)
    if page <= 0 or page_size <= 0:
        return ok(_d["items"], **_hd)
    return ok(_d, **_hd)

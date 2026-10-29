"""规则评估/告警关闭模块(2026-10-15 拆分——core/rules.py evaluate_stock_skus 208 行>200 红线)
函数: evaluate_stock_skus(每日全量评估+恢复关闭) / close_oversell_for_skus(删单联动 SKU 级孤儿关闭)
依赖: core.rules(evaluate_many/load_rules_for——函数内延迟 import 防循环) + db + dashboard(已有延迟)
"""
from db import query, one, execute
from routes.common import try_err


def close_orphan_for_skus(skus, channel="jd"):
    """删单联动通用化(2026-10-15): 该 SKU active 告警 → 其规则 event → 重评估(基于现有未删订单)——命中保留/未命中关;
    + low_stock 无行特殊(无未删订单且无在档——关); 替代硬编码 oversell/low_stock(自定义 alert_type 覆盖)"""
    if not skus:
        return
    from db import query as _q, execute as _e
    from routes.common import try_err as _terr
    try:
        _skus = list(set(skus))
        _ph = ",".join(["%s"] * len(_skus))
        # 未删订单量 + 在档
        _rows = _q("SELECT sku, SUM(quantity) AS q FROM orders WHERE deleted_at IS NULL OR deleted_at='' "
                   "AND channel=%s AND sku IN (%s) GROUP BY sku" % ("%s", _ph), [channel] + _skus)
        _live = {r.get("sku"): r.get("q") or 0 for r in _rows}
        _inf = _q("SELECT sku FROM products WHERE channel=%s AND sku IN (%s) AND (deleted_at IS NULL OR deleted_at='') "
                  "UNION SELECT sku FROM inventory WHERE channel=%s AND sku IN (%s)" % ("%s", _ph, "%s", _ph),
                  [channel] + _skus + [channel] + _skus)
        _infile = {r.get("sku") for r in _inf}
        _inv = _q("SELECT sku, SUM(available_qty) AS a FROM inventory WHERE channel=%s AND sku IN (%s) "
                  "GROUP BY sku" % ("%s", _ph), [channel] + _skus)
        _avail = {r.get("sku"): r.get("a") or 0 for r in _inv}
        # low_stock 特殊: 无未删订单且无在档(只有订单页有——订单删了孤儿)——关(有在档/真缺货保留)
        _ls_orphan = [s for s in _skus if not _live.get(s) and s not in _infile]
        if _ls_orphan:
            _ph3 = ",".join(["%s"] * len(_ls_orphan))
            _e("UPDATE alerts SET status='inactive' WHERE alert_type='low_stock' AND status='active' "
               "AND channel=%s AND related_sku IN (%s)" % ("%s", _ph3), [channel] + _ls_orphan)
        # 通用: 该 SKU active 告警 → 规则 event → 重评估(基于现有订单)——命中保留/未命中关
        _al = _q("SELECT DISTINCT alert_type FROM alerts WHERE status='active' AND channel=%s AND source='rules_engine' "
                 "AND related_sku IN (%s)" % ("%s", _ph), [channel] + _skus)
        _ats = [r.get("alert_type") for r in _al if r.get("alert_type")]
        for _at in _ats:
            # 2026-10-17 修复: low_stock 不参与删单重评估——safety_qty=0 硬编码 → available<0 恒假 → 全关
            # (删除测试单涉 1100 seed SKU → 2093 条 low_stock 全 inactive 实证; 安全线=库存维度, 删单不影响库存,
            #  low_stock 孤儿已在上方 _ls_orphan 分支按无单无档处理——通用重评估再碰 low_stock 属误伤)
            if _at == "low_stock":
                continue
            _rl = _q("SELECT event FROM rules WHERE alert_type=%s AND channel=%s AND is_active=1 "
                     "AND (deleted_at IS NULL OR deleted_at='')", [_at, channel])
            if not _rl:
                continue
            _ev = _rl[0].get("event") or "order.created"
            from core.rules import load_rules_for
            _rules = load_rules_for(_ev, channel)
            _rel = [r for r in _rules if r.get("alert_type") == _at]
            if not _rel:
                continue
            _ctxs = []
            for _sk in _skus:
                _av = float(_avail.get(_sk, 0) or 0)
                _ctxs.append({"sku": _sk, "channel": channel,
                              "order": {"quantity": _live.get(_sk, 0) or 0}, "order_qty": _live.get(_sk, 0) or 0,
                              "available_stock": _av,
                              "inv": {"available_qty": _av, "safety_qty": 0, "in_transit_qty": 0}})
            if not _ctxs:
                continue
            from core.rules import evaluate_many
            _hits = evaluate_many(_ev, _ctxs, channel, _rel, return_hits=True)[1]
            _hit_skus = {h[1] for h in _hits if h[0] == _at and h[3] == channel}
            _close_sk = [sk for sk in _skus if sk not in _hit_skus]
            if _close_sk:
                _ph4 = ",".join(["%s"] * len(_close_sk))
                _e("UPDATE alerts SET status='inactive' WHERE alert_type=%s AND status='active' "
                   "AND channel=%s AND related_sku IN (%s)" % ("%s", "%s", _ph4), [_at, channel] + _close_sk)
    except Exception as _e2:
        _terr('core_rules', 'close_orphan 降级', _e2)


def close_oversell_for_skus(skus, channel="jd"):
    """删单联动(2026-10-15): 该 SKU 未删订单量 vs 可用库存重评估——不超卖 → 关 active oversell 告警
    (曾只 invalidate_all 缓存——告警表不清——孤儿 oversell 残留看板)"""
    if not skus:
        return
    from db import query as _q, execute as _e
    from routes.common import try_err as _terr
    try:
        _ph = ",".join(["%s"] * len(skus))
        # 未删订单量(待发货/已完成等有效状态——与规则引擎同口径)
        _rows = _q("SELECT sku, SUM(quantity) AS q FROM orders WHERE deleted_at IS NULL OR deleted_at='' "
                   "AND channel=%s AND sku IN (%s) GROUP BY sku" % ("%s", _ph), [channel] + list(skus))
        _live = {r.get("sku"): r.get("q") or 0 for r in _rows}
        _inv = _q("SELECT sku, SUM(available_qty) AS a FROM inventory WHERE channel=%s AND sku IN (%s) "
                  "GROUP BY sku" % ("%s", _ph), [channel] + list(skus))
        _avail = {r.get("sku"): r.get("a") or 0 for r in _inv}
        _close_sku = [s for s in set(skus) if float(_live.get(s, 0) or 0) <= float(_avail.get(s, 0) or 0)]
        if _close_sku:
            _ph2 = ",".join(["%s"] * len(_close_sku))
            _e("UPDATE alerts SET status='inactive' WHERE alert_type='oversell' AND status='active' "
               "AND channel=%s AND related_sku IN (%s)" % ("%s", _ph2), [channel] + _close_sku)
        # low_stock 孤儿(2026-10-15 修正): 无未删订单 且 无在档(products/inventory 均无——只有订单页有——订单删了告警孤儿)才关;
        # 有在档(商品/进销存有该 SKU——即使 avail 0 真缺货)保留——不误伤
        _ph_in = ",".join(["%s"] * len(set(skus)))
        _inf = _q("SELECT sku FROM products WHERE channel=%s AND sku IN (%s) AND (deleted_at IS NULL OR deleted_at='') "
                  "UNION SELECT sku FROM inventory WHERE channel=%s AND sku IN (%s)" % ("%s", _ph_in, "%s", _ph_in),
                  [channel] + list(set(skus)) + [channel] + list(set(skus)))
        _infile = {r.get("sku") for r in _inf}
        _ls_orphan = [s for s in set(skus) if not _live.get(s) and s not in _infile]
        if _ls_orphan:
            _ph3 = ",".join(["%s"] * len(_ls_orphan))
            _e("UPDATE alerts SET status='inactive' WHERE alert_type='low_stock' AND status='active' "
               "AND channel=%s AND related_sku IN (%s)" % ("%s", _ph3), [channel] + _ls_orphan)
    except Exception as _e2:
        _terr('core_rules', 'close_oversell 降级', _e2)

def evaluate_stock_skus(channel, limit=100000, mode=None):
    from core.rules import evaluate_many, load_rules_for, _CALC_VARS  # 延迟(防循环——_CALC_VARS 常量随迁依赖)
    """对库存行批量评估 inventory.changed/scheduled.daily(批量版, 秒级)

    跳过 warehouse 为空的行(脏数据/导入错误行无实际仓归属, 不产生告警)
    若存在引用计算变量的规则(inv.adj_dos/buffer/otif/ss_dyn/accel_rate/health.score/params.*),
    注入断货判定同源的计算值(SKU 聚合粒度)——规则与看板断货/健康计算联动, 否则走快速路径(不加载日销)
    """
    from datetime import datetime, timezone
    rows = query("SELECT sku, warehouse, warehouse_type, product_name, available_qty, "
                 "safety_qty, in_transit_qty FROM inventory WHERE channel=%s "
                 "AND warehouse IS NOT NULL AND warehouse!='' "
                 "ORDER BY id LIMIT %s", [channel, limit])
    if not rows:
        return []
    # 规则是否引用计算变量(决定是否注入; 无引用 → 快速路径, 不加载日销/供应商/加速)
    # alert_enabled=0 的参数载体规则(如内置断货/健康)不产生告警 → 不参与评估/注入
    def _is_alert_rule(r):
        try:
            ae = (json.loads(r.get("params") or "{}") or {}).get("alert_enabled")
            return str(ae) != "0"  # 前端存字符串 '0', 统一字符串比较
        except Exception:
            return True
    _daily_rules = [r for r in load_rules_for("scheduled.daily", channel, mode) if _is_alert_rule(r)]
    _inv_rules = [r for r in load_rules_for("inventory.changed", channel, mode) if _is_alert_rule(r)]
    # 2026-10-29 每日兜底补 order.created(超卖): 原只评 inventory.changed + scheduled.daily
    # → 超卖仅靠"订单导入"触发, 不导入就永不生成/不校正(待处理"其他"恒 0 的根因之一)
    _order_rules = [r for r in load_rules_for("order.created", channel, mode) if _is_alert_rule(r)]
    _need_calc_daily = any(any(_v in str(r.get("condition_json") or "") for _v in _CALC_VARS) for r in _daily_rules)
    _need_calc_inv = any(any(_v in str(r.get("condition_json") or "") for _v in _CALC_VARS) for r in _inv_rules)
    _need_health = any("health." in str(r.get("condition_json") or "") for r in _daily_rules)
    _need_calc = _need_calc_daily or _need_calc_inv
    last_map = {}
    for r in query("SELECT sku, MAX(date) AS m FROM daily_sales_snapshot WHERE channel=%s GROUP BY sku",
                   [channel]):
        last_map[str(r.get("sku"))] = str(r.get("m") or "")[:10]
    now = datetime.now(timezone.utc)

    # 计算变量注入(与看板断货判定同源): SKU 聚合近似 + 规则参数(看板计算 params)
    _agg = None
    _fused = {}
    _sigma = {}
    _otif_map = {}
    _accel = {}
    _health = None
    _lit_trad = 10.0
    _lit_bbcc = 3.0
    _rp = {}
    if _need_calc:
        try:
            _cfg = {r2.get("key"): r2.get("value") for r2 in
                    query("SELECT `key`, value FROM replenishment_config WHERE channel=%s", [channel])}
            def _mcv(key, mode, default):
                v = _cfg.get("mode_%s_%s" % (mode, key))
                if v is None:
                    v = _cfg.get(key)
                try:
                    return float(v or default)
                except Exception:
                    return default
            _lit_trad = _mcv("lead_time_days", "traditional", 10)
            _lit_bbcc = _mcv("b_to_c_days", "bbcc", 3) + _mcv("c_safety_days", "bbcc", 0)
            # 动态安全线周期(与补货/采购备注同口径): bbcc 全周期=C+B / 传统=lead+安全 / 采购=前置+安全
            _bbcc_period = (_mcv("b_to_c_days", "bbcc", 3) + _mcv("c_safety_days", "bbcc", 0)
                            + _mcv("ship_to_b_days", "bbcc", 0) + _mcv("safety_multiplier", "bbcc", 0))
            _trad_period = _mcv("lead_time_days", "traditional", 10) + _mcv("safety_multiplier", "traditional", 0)
            _purchase_period = _mcv("purchase_lead_days", "bbcc", 14) + _mcv("purchase_safety_days", "bbcc", 3)
        except Exception as _e:
            try_err("rules", "评估加载补货周期配置降级", _e)
        try:
            # 看板计算参数(规则 params 优先, 兼容 config)
            from routes.dashboard import _rule_params
            _rp = _rule_params("stockout")
        except Exception as _e:
            try_err("rules", "评估加载看板计算参数降级", _e)
        try:
            for _r3 in query("SELECT supplier_code, MAX(score) AS score FROM suppliers GROUP BY supplier_code"):
                try:
                    _sc = float(_r3.get("score") or 0)
                    _otif_map[_r3.get("supplier_code")] = max(min(_sc / 5.0, 1.0),
                                                              float(_rp.get("otif_min", 0.6))) if _sc > 0 else 1.0
                except Exception as _e:
                        try_err('core_rules', '静默降级', _e)
        except Exception as _e:
            try_err("rules", "评估加载 OTIF 降级", _e)
        try:
            from biz.sales import get_sales_digest
            _by_sku, _fused, _sigma = get_sales_digest(channel)  # 共享日销(进程内缓存, 与看板同源)
        except Exception as _e:
            try_err("rules", "评估日销聚合降级", _e)
        try:
            from routes.dashboard import _hourly_accel
            _accel = _hourly_accel(channel, now,
                                   ratio=float(_rp.get("accel_ratio", 1.3)),
                                   min_qty=float(_rp.get("accel_min_qty", 10)))
        except Exception as _e:
            try_err("rules", "评估小时加速降级", _e)
        if _need_health:
            try:
                from routes.dashboard import _health_index
                _health = (_health_index(channel) or {}).get("score")
            except Exception as _e:
                try_err("rules", "评估健康分降级", _e)
        # SKU 聚合(avail/transit/safety + 供应商)
        _agg = {}
        _prod_sup = {}
        try:
            for _r4 in query("SELECT sku, supplier_code FROM products WHERE channel=%s "
                             "AND (deleted_at IS NULL OR deleted_at='')", [channel]):
                _prod_sup[_r4.get("sku")] = _r4.get("supplier_code") or ""
        except Exception as _e:
            try_err("rules", "评估加载供应商映射降级", _e)
        for _r in rows:
            _sku = str(_r.get("sku") or "")
            _st = _agg.setdefault(_sku, {"avail": 0, "transit": 0, "safety": 0})
            _st["avail"] += int(_r.get("available_qty") or 0)
            _st["transit"] += int(_r.get("in_transit_qty") or 0)
            _st["safety"] += int(_r.get("safety_qty") or 0)

    inv_ctxs, daily_ctxs = [], []
    for r in rows:
        sku = str(r.get("sku") or "")
        if not sku:
            continue
        last = last_map.get(sku, "")
        days = 999
        if last:
            try:
                days = max((now - datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days, 0)
            except Exception:
                days = 999
        base = {
            "sku": sku, "channel": channel,
            "inv": {"available_qty": int(r.get("available_qty") or 0),
                    # 快速路径(无计算变量引用规则)无 safety_qty 消费者 → 0; 动态值在 _need_calc 分支注入
                    "safety_qty": 0,
                    "in_transit_qty": int(r.get("in_transit_qty") or 0),
                    "warehouse_type": r.get("warehouse_type", ""),
                    "warehouse": r.get("warehouse", ""),
                    "product_name": r.get("product_name") or sku},
            "stock": int(r.get("available_qty") or 0),
            "days_since_last": days,
            "product_name": r.get("product_name") or sku,
        }
        if _need_calc and _agg is not None:
            try:
                _st = _agg.get(sku, {"avail": 0, "transit": 0, "safety": 0})
                _ds = _fused.get(sku, 0)
                _otif = _otif_map.get(_prod_sup.get(sku, ""), 1.0)
                _ss_z = float(_rp.get("ss_z", 1.65))
                _ss = _ss_z * _sigma.get(sku, 0) * (_lit_trad ** 0.5)
                _adj = (_st["avail"] + _st["transit"] * _otif) / _ds if _ds > 0 else 999.0
                # 动态安全线(替代静态 safety_qty): 按行维度周期——platform=补货周期(jd→bbcc全周期/other→传统),
                # own=采购周期; ds≤0 → 0(无销量不触发低库存告警, 去静态噪音)
                _wt = str(r.get("warehouse_type") or "")
                if _wt == "own":
                    _period = _purchase_period
                elif str(channel) == "jd":
                    _period = _bbcc_period
                else:
                    _period = _trad_period
                _dyn_safety = round(_ds * _period, 1) if _ds > 0 and _period > 0 else 0.0
                _buf = _st["avail"] / max(max(_dyn_safety, _ss), 1)
                base["inv"]["safety_qty"] = _dyn_safety
                base["inv"]["adj_dos"] = round(_adj, 2)
                base["inv"]["buffer"] = round(_buf, 2)
                base["inv"]["otif"] = _otif
                base["inv"]["ss_dyn"] = round(_ss, 1)
                base["inv"]["accel_rate"] = round(_accel.get(sku, 1.0), 2)
                base["health"] = {"score": _health if _health is not None else 999.0}
                base["health_score"] = _health if _health is not None else 999.0
                base["lit_trad"] = _lit_trad
                base["lit_bbcc"] = _lit_bbcc
            except Exception as _e:
                    try_err('core_rules', '静默降级', _e)
        inv_ctxs.append(base)
        daily_ctxs.append(base)
    r1, hits1 = evaluate_many("inventory.changed", inv_ctxs, channel, _inv_rules, return_hits=True)
    r2, hits2 = evaluate_many("scheduled.daily", daily_ctxs, channel, _daily_rules, return_hits=True)
    hits = hits1 | hits2
    out = list(dict.fromkeys(r1 + r2))
    # 2026-10-29 order.created 全量评估(超卖每日校正):
    # ctx 用**累计未删订单量**(order.total_qty) vs 当前可用库存 —— 与 close_oversell 同口径
    if _order_rules:
        try:
            _cum = {}
            _skus_all = [str(r.get("sku") or "") for r in rows if r.get("sku")]
            _skus_u = list(dict.fromkeys(_skus_all))
            for _i5 in range(0, len(_skus_u), 500):
                _b5 = _skus_u[_i5:_i5 + 500]
                _ph5 = ",".join(["%s"] * len(_b5))
                for _r5 in query("SELECT sku, SUM(quantity) AS q FROM orders WHERE channel=%s "
                                 "AND (deleted_at IS NULL OR deleted_at='') AND sku IN (%s) "
                                 "GROUP BY sku" % ("%s", _ph5), [channel] + _b5):
                    _cum[str(_r5.get("sku"))] = int(_r5.get("q") or 0)
            o_ctxs = []
            _seen = set()
            for _sk, _v in _cum.items():
                if _sk in _seen or not _v:
                    continue  # 无订单的 SKU 不参与超卖(0 > 库存 恒假)
                _seen.add(_sk)
                _st = _agg.get(_sk, {}) if isinstance(_agg, dict) else {}
                _av = int(_st.get("avail") or 0)
                o_ctxs.append({"sku": _sk, "channel": channel,
                               "order": {"quantity": _v, "total_qty": _v}, "order_qty": _v,
                               "available_stock": _av,
                               "inv": {"available_qty": _av, "safety_qty": 0, "in_transit_qty": 0}})
            if o_ctxs:
                r3, hits3 = evaluate_many("order.created", o_ctxs, channel, _order_rules, return_hits=True)
                hits = hits | hits3
                out = list(dict.fromkeys(out + r3))
        except Exception as _e:
            try_err('core_rules', '每日超卖评估降级', _e)
    # 恢复自动关闭(完整性): 每日全量快照下, 该事件规则 alert_type 的 active 告警
    # 若 SKU×仓 未命中(已恢复/不满足) → inactive, 防止库存补足后旧告警残留虚高计数
    try:
        # 2026-10-28 根治误关: 原 _ats 取自 hits(只含本次评估的 alert_type), 但若某类型本次 0 命中
        # → 根本不在 hits 里 → 该类型全部 active 告警不被清理(反而是安全的)。
        # 真正 bug 在下方: _close 判定用 hits 集合, 而 order.created(超卖)事件不在本次评估范围
        # → 若 hits 里恰好含 oversell? 不会(本函数只评 inventory.changed/scheduled.daily)。
        # 但历史数据里 oversell 被关——根因是 evaluate_many 对其他类型产生 alert_type 重名。
        # 修复: 只对"本次实际评估过的事件"产生的 alert_type 做关闭(白名单), 绝不跨事件误关。
        _evaluated_ats = set()
        for _rl in list(_daily_rules) + list(_inv_rules) + list(_order_rules):
            _at0 = _rl.get("alert_type")
            if _at0:
                _evaluated_ats.add(_at0)
        if hits:
            _ats = sorted({h[0] for h in hits} & _evaluated_ats)
            for _at in _ats:
                _act = query("SELECT id, related_sku, warehouse FROM alerts "
                             "WHERE alert_type=%s AND channel=%s AND status='active' AND source='rules_engine'",
                             [_at, channel])
                _close = [a["id"] for a in _act
                          if (_at, a.get("related_sku"), a.get("warehouse") or "", channel) not in hits]
                for _i in range(0, len(_close), 200):
                    _b = _close[_i:_i + 200]
                    if _b:
                        execute("UPDATE alerts SET status='inactive' WHERE id IN (%s)"
                                % ",".join(["%s"] * len(_b)), _b)
    except Exception as _e:
            try_err('core_rules', '静默降级', _e)
    # P2 动作审计: 规则 params.log=1 → 触发摘要写 quality_logs(追踪规则触发历史, 非仅当前 active 告警)
    try:
        for _rl in list(_daily_rules) + list(_inv_rules):
            if (_rl.get("_params") or {}).get("log") and hits:
                _n = sum(1 for h in hits if h[0] == _rl.get("alert_type"))
                if _n:
                    execute("INSERT INTO quality_logs(log_type, level, message, source) "
                            "VALUES('rule_action','info',%s,'rules')",
                            ("规则[%s] 触发 %d 个 SKU×仓" % (_rl.get("name") or _rl.get("id"), _n)))
    except Exception as _e:
            try_err('core_rules', '静默降级', _e)
    # 返回触发规则名(不泄漏查询细节)
    return out

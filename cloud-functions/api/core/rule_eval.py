"""规则评估/告警关闭模块(2026-10-15 拆分——core/rules.py evaluate_stock_skus 208 行>200 红线)
函数: evaluate_stock_skus(每日全量评估+恢复关闭) / close_oversell_for_skus(删单联动 SKU 级孤儿关闭)
依赖: core.rules(evaluate_many/load_rules_for——函数内延迟 import 防循环) + db + dashboard(已有延迟)
"""
LAST_DIAG = {}   # 2026-10-29 诊断用: 最近一次评估的关键计数(供 /rules/evaluate 返回定位)
_WARNED_REFS = set()  # 2026-11-05: 未登记字段只留痕一次(避免每次评估刷日志)

import json  # 2026-10-29 修复: _is_alert_rule 用 json.loads 但从未 import → NameError 被 except 吞
             #   → 所有规则都被当"告警规则"(含 alert_enabled=0 的参数载体), 内置断货/健康规则重复产告警
from db import query, one, execute
from routes.common import try_err
# 2026-11-06 S3: 日销取值从 `biz.sales.pick_ds`(需要调用方手拼两套映射)改为
#   `core.metrics.Series.value` —— 映射由模块统一持有, 调用方只给 (sku, 码, 维)。
# 2026-11-03 D8: 关闭一律走生命周期入口(写 closed_reason/closed_at, 状态机单一真源)
from core.alert_lifecycle import close as _close_alerts, close_by_ids as _close_ids


def _open_qty_map(channel, skus):
    """{sku: (未发货量, 未删订单量)} —— 超卖判定与孤儿判定的**唯一**取数口

    2026-11-06 两处修:
      ① 原 SQL 写成 `deleted_at IS NULL OR deleted_at='' AND channel=%s AND sku IN (...)`。
         `AND` 优先级高于 `OR` → 等价于 `deleted_at IS NULL OR (…AND channel AND sku…)`,
         于是 **只要这行没被删就命中, channel 与 sku 条件全被绕过** ——
         别的渠道、别的 SKU 的订单量都会被算进这个渠道的 `_live`, 超卖判定随之失真。
      ② 全历史累计销量改成**未发货量**(与 evaluate_stock_skus 同一口径)。原来三处各写
         一遍 `SUM(quantity)`, 于是"评估说超卖、删单联动说没超卖" —— 告警永远关不掉。
    """
    out = {}
    if not skus:
        return out
    from core import metrics as _M
    _days, _st = _M.oversell_window(_M.rule_params_of(channel, "oversell"))
    _cond = _M.oversell_sql_cond(_days, _st)
    _sk = list(set(skus))
    _ph = ",".join(["%s"] * len(_sk))
    try:
        for r in query(
                "SELECT sku, SUM(quantity) AS q, "
                "SUM(CASE WHEN {c} THEN quantity ELSE 0 END) AS oq "
                "FROM orders WHERE (deleted_at IS NULL OR deleted_at='') "
                "AND channel=%s AND sku IN ({ph}) GROUP BY sku".replace("{c}", _cond).replace("{ph}", _ph),
                [channel] + _sk):
            out[str(r.get("sku") or "")] = (int(r.get("oq") or 0), int(r.get("q") or 0))
    except Exception as _e:
        try_err("core_rules", "未发货订单量加载降级", _e)
    return out


def _close_grain_mismatch(channel, grain, limit=2000):
    """关闭与新粒度不符的 active 告警 —— 粒度切换后的一次性收尾

    告警的唯一键是 (channel, alert_type, sku, barcode, warehouse, source)。粒度从
    "SKU×69码" 换成 "SKU"(或反向)时, 旧告警的键里 barcode 与新键永远不相等 →
    "未命中即关闭"本该关掉它们, 但那条路径: ①只在有命中时走 ②受批量误关护栏限制
    (单轮拟关 >=80% 判为数据异常)。于是改粒度后旧告警会长期挂在列表上, 计数虚高。

    这里只按"键结构已失效"这一确定事实关闭, 不依赖本轮评估结果是否完整。
    """
    _expect_empty = str(grain) != "barcode"       # sku 粒度 → barcode 应为空
    try:
        _rows = query("SELECT id FROM alerts WHERE channel=%s AND status='active' "
                      "AND source='rules_engine' "
                      "AND ((COALESCE(barcode,'')='') <> %s) LIMIT %d"
                      % ("%s", "%s", int(limit)),
                      [channel, 1 if _expect_empty else 0])
        if _rows:
            _close_ids([r["id"] for r in _rows], "grain_changed")
    except Exception as _e:
        try_err("core_rules", "粒度切换收尾降级", _e)


def close_orphan_for_skus(skus, channel):
    """删单联动通用化(2026-10-15): 该 SKU active 告警 → 其规则 event → 重评估(基于现有未删订单)——命中保留/未命中关;
    + low_stock 无行特殊(无未删订单且无在档——关); 替代硬编码 oversell/low_stock(自定义 alert_type 覆盖)

    2026-11-06 审计 P1-20: channel 去默认值(原 "jd")——默认值让 other 渠道的删单
    跑去重评估 jd 的告警, 是最典型的"静默错配", 必须强制调用方显式给渠道。"""
    if not skus:
        return
    from db import query as _q, execute as _e
    from routes.common import try_err as _terr
    try:
        _skus = list(set(skus))
        _ph = ",".join(["%s"] * len(_skus))
        # 未发货量(超卖判定) + 未删订单量(孤儿判定) —— 一次取回, 见 _open_qty_map
        _qm = _open_qty_map(channel, _skus)
        _open = {k: v[0] for k, v in _qm.items()}
        _live = {k: v[1] for k, v in _qm.items()}
        _inf = _q("SELECT sku FROM products WHERE channel=%s AND sku IN (%s) AND (deleted_at IS NULL OR deleted_at='') "
                  "UNION SELECT sku FROM inventory WHERE channel=%s AND sku IN (%s)  -- no-barcode-ok: 存在性判定" % ("%s", _ph, "%s", _ph),
                  [channel] + _skus + [channel] + _skus)
        _infile = {r.get("sku") for r in _inf}
        _inv = _q("SELECT sku, SUM(available_qty) AS a FROM inventory WHERE channel=%s AND sku IN (%s) "  # no-barcode-ok: SKU 汇总
                  "GROUP BY sku" % ("%s", _ph), [channel] + _skus)
        _avail = {r.get("sku"): r.get("a") or 0 for r in _inv}
        # low_stock 特殊: 无未删订单且无在档(只有订单页有——订单删了孤儿)——关(有在档/真缺货保留)
        _ls_orphan = [s for s in _skus if not _live.get(s) and s not in _infile]
        if _ls_orphan:
            _ph3 = ",".join(["%s"] * len(_ls_orphan))
            _close_alerts("alert_type='low_stock' AND channel=%s AND related_sku IN (%s)"
                          % ("%s", _ph3), [channel] + _ls_orphan, "ls_orphan_no_demand")
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
                _oq = _open.get(_sk, 0) or 0
                _ctxs.append({"sku": _sk, "channel": channel,
                              "order": {"quantity": _oq, "open_qty": _oq},
                              "order_qty": _oq,
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
                _close_alerts("alert_type=%s AND channel=%s AND related_sku IN (%s)"
                              % ("%s", "%s", _ph4), [_at, channel] + _close_sk, "rule_unhit")
    except Exception as _e2:
        _terr('core_rules', 'close_orphan 降级', _e2)


def close_oversell_for_skus(skus, channel):
    """删单联动(2026-10-15): 该 SKU **未发货订单量** vs 可用库存重评估——不超卖 → 关 active oversell 告警
    (曾只失效缓存——告警表不清——孤儿 oversell 残留看板)

    2026-11-06: channel 去默认值(原 "jd")——与 P1-20 同类, 默认值会让 other 的删单
    去关 jd 的告警。口径改用未发货量, 与 evaluate_stock_skus 同源(见 _open_qty_map)。"""
    if not skus:
        return
    from routes.common import try_err as _terr
    try:
        _ph = ",".join(["%s"] * len(skus))
        _qm = _open_qty_map(channel, list(skus))
        _open = {k: v[0] for k, v in _qm.items()}
        _inv = query("SELECT sku, SUM(available_qty) AS a FROM inventory WHERE channel=%s AND sku IN (%s) "  # no-barcode-ok: SKU 汇总
                     "GROUP BY sku" % ("%s", _ph), [channel] + list(skus))
        _avail = {r.get("sku"): r.get("a") or 0 for r in _inv}
        _close_sku = [s for s in set(skus) if float(_open.get(s, 0) or 0) <= float(_avail.get(s, 0) or 0)]
        if _close_sku:
            _ph2 = ",".join(["%s"] * len(_close_sku))
            _close_alerts("alert_type='oversell' AND channel=%s AND related_sku IN (%s)"
                          % ("%s", _ph2), [channel] + _close_sku, "oversell_cleared")
        # low_stock 孤儿(2026-10-15 修正): 无未删订单 且 无在档(products/inventory 均无——只有订单页有——订单删了告警孤儿)才关;
        # 有在档(商品/进销存有该 SKU——即使 avail 0 真缺货)保留——不误伤
        _ph_in = ",".join(["%s"] * len(set(skus)))
        _inf = query("SELECT sku FROM products WHERE channel=%s AND sku IN (%s) AND (deleted_at IS NULL OR deleted_at='') "
                     "UNION SELECT sku FROM inventory WHERE channel=%s AND sku IN (%s)  -- no-barcode-ok: 存在性判定" % ("%s", _ph_in, "%s", _ph_in),
                     [channel] + list(set(skus)) + [channel] + list(set(skus)))
        _infile = {r.get("sku") for r in _inf}
        # 孤儿判定用的是"有没有未删订单"(全历史), 不是未发货量 —— 一个早就发完的 SKU 仍有档, 不该被当孤儿
        _live = {k: v[1] for k, v in _qm.items()}
        _ls_orphan = [s for s in set(skus) if not _live.get(s) and s not in _infile]
        if _ls_orphan:
            _ph3 = ",".join(["%s"] * len(_ls_orphan))
            _close_alerts("alert_type='low_stock' AND channel=%s AND related_sku IN (%s)"
                          % ("%s", _ph3), [channel] + _ls_orphan, "ls_orphan_no_demand")
    except Exception as _e2:
        _terr('core_rules', 'close_oversell 降级', _e2)

def evaluate_stock_skus(channel, limit=100000, mode=None, skus=None, only_ats=None):
    import time as _t
    _t0 = _t.time()
    # 2026-10-29 分段计时(仅日志, 零数据影响): 先实测再决定是否下推 SQL
    _marks = []
    _tick_last = [_t.time()]

    def _tick(_n):
        _now = _t.time()
        _marks.append("%s=%.1f" % (_n, _now - _tick_last[0]))
        _tick_last[0] = _now
    _o_ctx_n = 0  # 审计用: order ctx 计数(作用域安全)
    from core.rules import evaluate_many, load_rules_for  # 延迟(防循环——常量随迁依赖)
    from core.rule_fields import needs_calc as _needs_calc, unknown_refs as _unknown_refs
    """对库存行批量评估 inventory.changed/scheduled.daily(批量版, 秒级)

    跳过 warehouse 为空的行(脏数据/导入错误行无实际仓归属, 不产生告警)
    若存在引用计算变量的规则(inv.adj_dos/buffer/otif/ss_dyn/accel_rate/health.score/params.*),
    注入断货判定同源的计算值(SKU 聚合粒度)——规则与看板断货/健康计算联动, 否则走快速路径(不加载日销)
    """
    from datetime import datetime
    from core.clock import CN_TZ, now_cn
    # 2026-10-29 按 69码: 库存行已按码拆分 → 行级判定自动按码; ctx 透出 barcode 供规则引用
    from routes.common import INVENTORY_COLS as _IC
    _SEL = ("SELECT " + _IC + " FROM inventory "
            "WHERE channel=%s AND warehouse IS NOT NULL AND warehouse!='' ")
    if skus:
        # 2026-10-29 增量入口: 出入库导入后只评本批 SKU(不全量/不拖慢), 分片防超长 IN
        _sk = [str(x) for x in skus if x]
        rows = []
        for _i0 in range(0, len(_sk), 500):
            _b0 = _sk[_i0:_i0 + 500]
            _ph0 = ",".join(["%s"] * len(_b0))
            rows.extend(query(_SEL + "AND sku IN (%s)" % _ph0, [channel] + _b0))
    else:
        rows = query(_SEL + "ORDER BY id LIMIT %s", [channel, limit])
    if not rows:
        return []
    _tick("rows")
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
    # 2026-11-03 D4: 只评受影响的 alert_type —— 规则保存时"改了一条规则的阈值",
    #   就该只算这条规则的 SKU, 而不是把同渠道所有规则重算一遍。
    #   安全性: 下方"未命中即关闭"只针对 _evaluated_ats(本次实际评估的 alert_type),
    #   被过滤掉的不在集合里 → 不会被误关。
    if only_ats:
        _want_ats = {str(x) for x in only_ats if x}
        _daily_rules = [r for r in _daily_rules if str(r.get("alert_type") or "") in _want_ats]
        _inv_rules = [r for r in _inv_rules if str(r.get("alert_type") or "") in _want_ats]
        _order_rules = [r for r in _order_rules if str(r.get("alert_type") or "") in _want_ats]
    # 2026-11-05 根治: 原用 _CALC_VARS 子串匹配 —— 与保存白名单是两份手工列表,
    #   supplier.score / inv.turnover_days 在白名单却不在子串表 → 走快速路径 → 键不注入
    #   → 条件恒 None → 规则静默失效。现由 core/rule_fields 的注册表统一判定。
    _need_calc_daily = any(_needs_calc(r.get("condition_json") or "") for r in _daily_rules)
    _need_calc_inv = any(_needs_calc(r.get("condition_json") or "") for r in _inv_rules)
    _need_health = any("health." in str(r.get("condition_json") or "") for r in _daily_rules)
    _need_calc = _need_calc_daily or _need_calc_inv
    # 未登记字段的历史规则: 留痕(每个字段进程内只记一次) —— 让"静默不触发"在日志里可见
    for _r in _daily_rules + _inv_rules + _order_rules:
        for _ref in _unknown_refs(str(_r.get("condition_json") or "")):
            if _ref not in _WARNED_REFS:
                _WARNED_REFS.add(_ref)
                try_err("rules", "规则引用了未登记字段(该规则永不触发): %s" % _ref,
                        KeyError(_ref))
    _tick("rules")
    last_map = {}
    for r in query("SELECT sku, MAX(date) AS m FROM daily_sales_snapshot WHERE channel=%s GROUP BY sku",
                   [channel]):
        last_map[str(r.get("sku"))] = str(r.get("m") or "")[:10]
    now = now_cn()   # 北京时间: 末销天数按自然日算
    _tick("lastmap")

    # 计算变量注入(与看板断货判定同源): SKU 聚合近似 + 规则参数(看板计算 params)
    _agg = None
    # 2026-11-06 S3: 原 `_ds_bc` / `_ds_agg` 两套手拼映射已删除 —— 日销取值统一走
    #   `core.metrics.Series.value`(按码优先、只降码不降维、绝不全国兜底)。
    # 2026-11-03 D4: 下面"计算变量注入"整块可能被跳过(only_ats 过滤后没有规则引用计算变量),
    #   但这三个映射在 ctx 构造时被**无条件**引用。必须在块外先初始化 ——
    #   否则浅档评估第一次就 `local variable '_exp_map' referenced before assignment`
    #   (线上实测: 任务 390ms 内 error, 且只影响"没有规则引用 calc 变量"的窄路径, 很难复现)。
    _exp_map = {}      # sku → 最近到期剩余天数
    _sup_score = {}    # supplier_code → 评分
    _own_map = {}      # sku → 自有仓供给侧(need/gap/cover_days)
    _prod_sup = {}     # sku → supplier_code(ctx 构造处无条件 get, 500 行外)

    def _ds_of(_sku, _bc, _wh):
        """日销取值: 该维该 69码 → 该维 SKU 合计 → 0

        维度(_wh)是既定业务对象, 不是可降级层级: bbcc→__BC__(一盘) / traditional+own→该仓。
        只做两级: ①该对象内该 69码 ②该对象内 SKU 合计(少码)—— 降级只降码, 不换维度。
        该维度无数据时返回 0(不触发): **绝不拿全国/全国C合计兜底**(会干扰本仓判断)。
        2026-11-06 S3: 实现已收敛到 `core.metrics.Series.value`, 这里只留签名。
        """
        if _SER is None:
            return 0.0
        return _SER.value("ds28", _sku, _bc, _wh)

    _sigma = {}
    _otif_map = {}
    _accel = {}
    _health = None
    _lit_trad = 10.0
    _lit_bbcc = 3.0
    _rp = {}
    _p = {}          # 周期(天) —— 由 core.metrics.periods 统一算, 见下方 _need_calc 分支
    _SER = None      # 日销切片(core.metrics.Series) —— 全渠道按码一次取回
    if _need_calc:
        try:
            from core import metrics as _M
            _cfg = {r2.get("key"): r2.get("value") for r2 in
                    query("SELECT `key`, value FROM replenishment_config WHERE channel=%s", [channel])}
            # 2026-11-06 S3: 周期口径收敛到 core.metrics.periods —— 看板/规则/采购
            #   原来各写一遍同样的五行, 连"mode 传哪个"都不同(other 用 jd 的周期算安全线)。
            _p = _M.periods(_cfg)
            _lit_trad = float(_p["lead"])          # σ 安全库存的地平线: 供应商→仓
            _lit_bbcc = float(_p["b2c"])           # 自有仓给下游备货的周期: C→B
        except Exception as _e:
            from core import metrics as _M
            _p = _M.periods({})
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
            from core import metrics as _M
            # 2026-11-06 S3: 一次取回(按码 + 逐仓 + 全维), 取代原来"按码一次 + 合计一次"
            #   两趟 12 万行 —— 只取 ds28/sigma 两列, 传输量降到原来的 1/5。
            #   同时取代 get_sales_digest: 它给的是 SKU 级 `__C__` 一个数, 正是本模块要消灭的东西。
            _SER = _M.load(channel, dims=None, with_barcode=True, fields=("ds28", "sigma"))
            _sigma = _SER.sigma_by_sku() if _SER is not None else {}
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
        # 2026-10-29 ctx 扩展: 批次效期(最近到期剩余天数) + 供应商评分 + 周转天数
        # 缺失一律不给键(→ 条件求值返回 None → 规则跳过), 绝不用 0 冒充(避免"剩余0天"误报)
        _exp_map = {}
        try:
            from datetime import datetime as _dtx
            from core.clock import now_cn as _now_cn
            _todayx = _now_cn().date()   # 北京时间自然日: 剩余保质期差一天就会误报临期
            for _rx in query("SELECT sku, MIN(exp_date) AS e FROM batches WHERE channel=%s "
                             "AND exp_date IS NOT NULL AND exp_date!='' GROUP BY sku", [channel]):
                _e = str(_rx.get("e") or "")[:10]
                if _e:
                    try:
                        _exp_map[str(_rx.get("sku"))] = (
                            _dtx.strptime(_e, "%Y-%m-%d").date() - _todayx).days
                    except Exception:
                        pass
        except Exception as _ex:
            try_err('core_rules', '批次效期加载降级', _ex)
        _sup_score = {}
        try:
            for _rs in query("SELECT supplier_code, MAX(score) AS sc FROM suppliers WHERE channel=%s "
                             "GROUP BY supplier_code", [channel]):
                _v = _rs.get("sc")
                if _v is not None:
                    _sup_score[str(_rs.get("supplier_code"))] = float(_v)
        except Exception as _ex2:
            try_err('core_rules', '供应商评分加载降级', _ex2)
        # ── 2026-11-01 自有仓供给侧 ctx(own_transfer_gap/own_overstock 规则用) ──
        # 背景: 自有仓(集货仓)不产生销售 —— daily_sales_snapshot 只有平台仓行,
        #   逐仓日销 ds=0 → 动态安全线=0 → 低库存口径恒不触发(口径错配, 非 bug)。
        #   自有仓该判的是供给侧三件事, 这里把判定所需的量都注入 ctx, 让规则可配置:
        #     own.available / own.transit        自有仓在仓/在途(逐 SKU 汇总)
        #     own.po_transit                    采购在途 —— 与 own.transit 同源(见下)
        #     own.can_ship                     可发量 = 在仓 + 在途
        #     own.need                          下游一个调拨周期的要货量 = 下游日销 × b2c_days × 覆盖倍数
        #     own.gap                           缺口 = need − can_ship(>0 即调拨覆盖不足)
        #     own.cover_days                    自有可用 ÷ 下游日销(积压判定)
        #   缺数据一律不给键(条件求值 None → 跳过), 不用 0 冒充。
        #
        # 2026-11-03 S3-4: 删除"从 purchase_orders 反推采购在途"的查询 —— 该表只有
        #   suggested_qty/actual_qty, 没有 qty/received_qty, 每次执行必 1054, 被 except 吞成
        #   "按 0 降级"。后果是 own.po_transit 恒 0、can_ship 少算, 而**日志里看不出来**。
        #   同一个错误 2026-11-02 已在 dashboard_own_health 修过一次(那里也留了断言), 这里漏了。
        #   口径按用户定案: 采购在途 = 库存导入的自有仓 in_transit_qty, 导入值即权威,
        #   再推一遍就是双计 —— 所以 po_transit 与 transit 同源, can_ship 只加一次。
        _own_map = {}
        try:
            _wm = {}
            for _ro in query("SELECT sku, warehouse_type, IFNULL(SUM(IFNULL(available_qty,0)),0) AS av, "
                             "IFNULL(SUM(IFNULL(in_transit_qty,0)),0) AS tr FROM inventory "
                             "WHERE channel=%s AND warehouse_type IN ('own','platform','platform_b') "
                             "GROUP BY sku, warehouse_type", [channel]):
                _wm.setdefault(str(_ro.get("sku") or ""), {})[
                    str(_ro.get("warehouse_type") or "")] = (
                    int(_ro.get("av") or 0), int(_ro.get("tr") or 0))
            # 阈值同样走规则 params(可配置): 这里只给"默认值", 规则条件用 params.* 覆盖
            _own_cover_mult = float(_rp.get("transfer_cover_mult", 1.0) or 1.0) if _rp else 1.0
            for _sk, _d in _wm.items():
                _oa, _ot = _d.get("own", (0, 0))
                if _oa <= 0 and _ot <= 0:
                    continue  # 非自有仓 SKU 不注入(避免给全量 SKU 造键)
                _pb = _d.get("platform", (0, 0))
                _pbb = _d.get("platform_b", (0, 0))
                _down_av = _pb[0] + _pbb[0] + _pb[1] + _pbb[1]
                _ds_own = _ds_of(_sk, "", "")
                _can = _oa + _ot
                _own_map[_sk] = {
                    "available": _oa, "transit": _ot, "po_transit": _ot,
                    "can_ship": _can, "downstream_avail": _down_av,
                    "b2c_days": _lit_bbcc,
                    "cover_mult": _own_cover_mult,
                }
        except Exception as _eown:
            try_err('core_rules', '自有仓供应量加载降级(own.* 键不注入)', _eown)
        for _r in rows:
            _sku = str(_r.get("sku") or "")
            _st = _agg.setdefault(_sku, {"avail": 0, "transit": 0, "safety": 0})
            _st["avail"] += int(_r.get("available_qty") or 0)
            _st["transit"] += int(_r.get("in_transit_qty") or 0)
            _st["safety"] += int(_r.get("safety_qty") or 0)

    # 2026-10-29 模式分流(与补货建议的安全线提示完全同源):
    #   bbcc        → BC 合计(C可用+C在途+B可用+B在途) 对 ds×全周期, 粒度 SKU×69码 一条
    #   traditional → 逐仓(可用+在途) 对 该仓 ds×(lead+safety)
    #   own(两模式) → 逐仓(可用+在途) 对 该仓 ds×(采购前置+安全)
    #   原实现: 单仓 avail 对 全国 ds×周期 → 每仓必报(实测 jd 711→3157)
    #
    # 2026-11-06 S3: 归并规则搬到 `core.metrics.units` —— 看板健康卡/低库存用的是同一份。
    #   原来这段"platform+platform_b 合并成 BC"在这里、在 dashboard_health 里各写一遍
    #   (后者还按 SKU 合并、dyn 取 max), 两处结果永远对不上。现在只有一处。
    from core import metrics as _M
    # 超卖(order.created)判的是 SKU 级可用量, 与仓/码无关 → 用**原始库存行**汇总(含自有仓,
    #   那是真能发的货)。2026-11-06 S4: 原来这段只在 `_need_calc` 为真时才算, 于是"没有规则
    #   引用计算变量"的那条路径里 `_agg` 是 None → available_stock 恒 0 → 每个有订单的 SKU
    #   都被判超卖(假告警)。判定用的量不该取决于"别的规则要不要算变量"。
    if _order_rules and not isinstance(_agg, dict):
        _agg = {}
        for _r in rows:
            _sku0 = str(_r.get("sku") or "")
            if not _sku0:
                continue
            _st0 = _agg.setdefault(_sku0, {"avail": 0, "transit": 0, "safety": 0})
            _st0["avail"] += int(_r.get("available_qty") or 0)
            _st0["transit"] += int(_r.get("in_transit_qty") or 0)
            _st0["safety"] += int(_r.get("safety_qty") or 0)
    _mode_e = str(mode or _M.mode_of(channel))
    # 粒度由渠道补货模式决定(bbcc=SKU×69码 / traditional=SKU), 见 metrics.grain_of
    _grain_e = _M.grain_of(channel, _mode_e)
    _units = _M.units(rows, merge_bc=(_mode_e == "bbcc"), drop_platform_b=(_mode_e != "bbcc"),
                      grain=_grain_e)
    # 规则求值仍然吃"行"形态(condition_json 里写的是 inv.* / own.*), 这里把单元映射回行:
    #   BC 单元 → warehouse='__BC__' / warehouse_type='bc'; 其余单元 → 各自的仓与仓型。
    #   available_qty 只放"已到仓可用", in_transit_qty 单独放 —— 规则要含在途就显式相加,
    #   避免同一个字段在 bbcc/traditional 下含义不同(2026-10-30 定案)。
    rows = [{"sku": _u["sku"], "barcode": _u["barcode"], "warehouse": _u["warehouse"],
             "warehouse_type": _u["warehouse_type"],
             "product_name": _u.get("name") or _u["sku"],
             "available_qty": _u["avail"], "in_transit_qty": _u["transit"],
             "safety_qty": _u.get("manual_safety") or 0,
             "locked_qty": 0,
             # 2026-11-06 合并不静默: grain=sku 合并多码时把"各码可用"带进 ctx,
             #   规则文案 {code_detail} 可引用(如 "690..01:0 / 690..02:100")
             "code_detail": _M.code_detail_str(_u),
             "multi_barcode": bool(_u.get("multi_barcode"))} for _u in _units]

    _tick("calc_load")
    inv_ctxs, daily_ctxs = [], []
    for r in rows:
        sku = str(r.get("sku") or "")
        if not sku:
            continue
        last = last_map.get(sku, "")
        days = 999
        if last:
            try:
                days = max((now - datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=CN_TZ)).days, 0)
            except Exception:
                days = 999
        base = {
            "sku": sku, "barcode": str(r.get("barcode") or ""), "channel": channel,
            "inv": {"available_qty": int(r.get("available_qty") or 0),
                    # 快速路径(无计算变量引用规则)无 safety_qty 消费者 → 0; 动态值在 _need_calc 分支注入
                    "safety_qty": 0,
                    "in_transit_qty": int(r.get("in_transit_qty") or 0),
                    "locked_qty": int(r.get("locked_qty") or 0),
                    "warehouse_type": r.get("warehouse_type", ""),
                    "warehouse": r.get("warehouse", ""),
                    "product_name": r.get("product_name") or sku,
                    # 多码明细(grain=sku 合并时非空): 规则可引用 inv.code_detail 判"部分码断货"
                    "code_detail": str(r.get("code_detail") or ""),
                    "multi_barcode": bool(r.get("multi_barcode"))},
            "stock": int(r.get("available_qty") or 0),
            "days_since_last": days,
            # 2026-10-29 规则页字段对齐: 前端"日销(定时任务提供)"一直为空 → 补 SKU 级日销(逐仓优先)
            "daily_sales": round(float(_ds_of(
                sku, str(r.get("barcode") or ""),
                "__C__" if str(r.get("warehouse_type") or "") == "bc"
                else str(r.get("warehouse") or "")) or 0), 2),
            "product_name": r.get("product_name") or sku,
            # 顶层别名(告警文案 {code_detail} 可直接引用; 单码/码级判定时为空)
            "code_detail": str(r.get("code_detail") or ""),
        }
        # 有数据才注入(缺失=键不存在 → 条件跳过, 不误报)
        if sku in _exp_map:
            base["batch"] = {"days_to_expire": _exp_map[sku]}
        # 2026-11-01 自有仓供给侧键(仅 own 行注入 —— 规则需按 inv.warehouse_type=='own' 圈定):
        #   need/gap 依赖下游日销, 无日销时不给 need/gap(积压判定仍需 cover_days, 也无从算) →
        #   整块只在有下游日销时注入, 避免"缺口=need(0) - can" 产生负向误判
        if str(r.get("warehouse_type") or "") == "own" and sku in _own_map:
            _om = dict(_own_map[sku])
            _ds_down = _ds_of(sku, str(r.get("barcode") or ""), "__C__")
            if _ds_down and _ds_down > 0:
                # 下游日销可能存在(全国 C 口径) → 复算 need/gap/cover
                _b2c = float(_om.get("b2c_days") or 0)
                _need = _ds_down * _b2c * float(_om.get("cover_mult") or 1.0)
                base["own"] = {
                    "available": _om["available"], "transit": _om["transit"],
                    "po_transit": _om["po_transit"], "can_ship": _om["can_ship"],
                    "downstream_avail": _om["downstream_avail"],
                    "downstream_ds": round(_ds_down, 2),
                    "b2c_days": _b2c,
                    "need": round(_need, 1),
                    "gap": round(_need - _om["can_ship"], 1),
                    "cover_days": round(_om["available"] / _ds_down, 1),
                }
            else:
                base["own"] = {"available": _om["available"], "transit": _om["transit"],
                               "po_transit": _om["po_transit"], "can_ship": _om["can_ship"],
                               "downstream_avail": _om["downstream_avail"]}
        _supc = _prod_sup.get(sku, "")
        if _supc and _supc in _sup_score:
            base["supplier"] = {"score": _sup_score[_supc], "code": _supc}
        if _need_calc:
            try:
                _wt = str(r.get("warehouse_type") or "")
                # BC 盘的日销读 __C__(B 仓不产生销售); 自有仓读 __C__(要的是下游需求); 其余按自身仓
                _kind = (_M.DIM_BC if _wt == "bc"
                         else (_M.DIM_OWN if _wt == "own" else _M.DIM_WH))
                _ds = _ds_of(sku, str(r.get("barcode") or ""),
                             _M.dim_of(_kind, str(r.get("warehouse") or ""), _wt))
                _otif = _otif_map.get(_prod_sup.get(sku, ""), 1.0)
                # 2026-11-06 S3(P0-13): 量取**本单元**的 —— BC 单元=平台+B 合计, 逐仓单元=该仓。
                #   原实现用 `_agg[sku]`(该 SKU 所有仓型求和, 含集货仓)去除以单仓/BC 的日销,
                #   于是"BC 盘可撑天数"里混着自有仓的货, buffer 虚高 → 该报的没报。
                _u = {"sku": sku, "barcode": str(r.get("barcode") or ""), "kind": _kind,
                      "warehouse": str(r.get("warehouse") or ""),
                      "avail": int(r.get("available_qty") or 0),
                      "transit": int(r.get("in_transit_qty") or 0),
                      "manual_safety": int(r.get("safety_qty") or 0)}
                _v = _M.judge(_u, _ds, _p, channel, _mode_e, include_transit=False,
                              sigma=_sigma.get(sku, 0), z=float(_rp.get("ss_z", 1.65)),
                              otif=_otif, accel=_accel.get(sku, 1.0))
                # 2026-10-29 ctx 扩展: 周转天数(可撑天数, 无日销时不给键——避免 999 误判)
                if _ds > 0:
                    base.setdefault("inv", {})["turnover_days"] = _v["adj_dos"]
                elif _wt == "own" and base.get("own", {}).get("cover_days"):
                    # 2026-11-01 own 行补 turnover_days: 自有仓结构性无本仓日销(daily_sales_snapshot
                    #   只记平台仓) → 上面 _ds>0 永远不成立 → 规则页"周转天数"字段在 own 维度恒缺失,
                    #   用户选中即静默失效。own 的语义等价量 = 自有可用 ÷ 下游日销(= own.cover_days)。
                    base.setdefault("inv", {})["turnover_days"] = float(base["own"]["cover_days"])
                # 安全线: 手工 safety_qty>0 优先, 否则 ds×周期(周期按桶类型: BC/平台=补货周期,
                #   自有仓=采购周期)。ds≤0 → 0(无销量不触发低库存告警, 去静态噪音)
                base["inv"]["safety_qty"] = _v["line"]
                base["inv"]["adj_dos"] = _v["adj_dos"]
                base["inv"]["buffer"] = _v["buffer"]
                base["inv"]["otif"] = _otif
                base["inv"]["ss_dyn"] = _v["ss_dyn"]
                base["inv"]["accel_rate"] = _v["accel_rate"]
                base["health"] = {"score": _health if _health is not None else 999.0}
                base["health_score"] = _health if _health is not None else 999.0
                base["lit_trad"] = _lit_trad
                base["lit_bbcc"] = _lit_bbcc
            except Exception as _e:
                    try_err('core_rules', '静默降级', _e)

        inv_ctxs.append(base)
        daily_ctxs.append(base)
    _tick("ctx_build")
    r1, hits1 = evaluate_many("inventory.changed", inv_ctxs, channel, _inv_rules, return_hits=True)
    r2, hits2 = evaluate_many("scheduled.daily", daily_ctxs, channel, _daily_rules, return_hits=True)
    hits = hits1 | hits2
    _tick("evaluate_many")
    out = list(dict.fromkeys(r1 + r2))
    # order.created 全量评估(超卖每日校正)
    #
    # 2026-11-06 口径修正(用户定案: "全历史量肯定不行"):
    #   原口径 `SUM(quantity)`(**全历史累计销量**, 含已完成/已发货/已退货) > 当前可用。
    #   卖过一阵就必然成立 —— 线上实测 other 1,783 条, jd 414 条, 而绝大多数是"早就发完了"。
    #   超卖的真实语义是"卖出去还没发、而仓里不够发", 所以:
    #     · 只算未发货状态(`待确认`/`待发货`) —— 已发货的货 ERP 早扣过库存, 再算一次是双计;
    #     · 只算近 N 天下单(`rules.params.oversell_days`, 默认 14) —— 挂三个月的单是异常单。
    #   实测受影响量: other 1,783 → 390, jd 414 → 41。
    if _order_rules:
        try:
            # 2026-11-06 S4: 原实现按 500 个 SKU 分批查累计量、再按 200 个分批查金额/批量字段
            #   —— 8 万 SKU 要 ~560 次往返。线上实测 order_created 段独占 35.7s(整轮 55s 的 65%),
            #   这正是"评估跑不完 / 被平台掐掉 / pipeline_runs 停在 running"的直接原因。
            #   改成一条 GROUP BY: 全量订单聚成 1 万行, 1 次往返换 560 次。
            #   口径收窄后仍然一条 SQL —— 用**条件聚合**在同一次扫描里同时算
            #   "未发货量"(判定用)与"历史累计量"(排查用), 不额外扫一遍表。
            from core.rules import _rule_params_loaded as _rpl
            _ow_params = {}
            for _r9 in _order_rules:
                if str(_r9.get("alert_type") or "") == "oversell":
                    _ow_params = _rpl(_r9) or {}
                    break
            _ow_days, _ow_st = _M.oversell_window(_ow_params)
            _ow_cond = _M.oversell_sql_cond(_ow_days, _ow_st)
            _cum = {}
            _oagg = {}
            _skus_u = {str(r.get("sku") or "") for r in rows if r.get("sku")}
            _osql = (
                "SELECT sku, "
                "SUM(CASE WHEN {c} THEN quantity ELSE 0 END) AS open_qty, "
                "SUM(CASE WHEN {c} THEN total_amount ELSE 0 END) AS amt, "
                "MAX(CASE WHEN {c} THEN quantity ELSE 0 END) AS mx, "
                "MIN(CASE WHEN {c} THEN quantity END) AS mn, "
                "SUM(CASE WHEN {c} THEN 1 ELSE 0 END) AS lc, "
                "MAX(CASE WHEN {c} THEN unit_price ELSE 0 END) AS up "
                "FROM orders WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='') "
                "GROUP BY sku"
            ).replace("{c}", _ow_cond)
            for _r6 in query(_osql, [channel]):
                _sk6 = str(_r6.get("sku") or "")
                # 只保留本轮评估范围内的 SKU —— 不在库存里的 SKU 拿去比"可用 0"会造出假超卖
                if _sk6 not in _skus_u:
                    continue
                _op6 = int(_r6.get("open_qty") or 0)
                if _op6 > 0:
                    _cum[_sk6] = _op6
                _oagg[_sk6] = _r6
            o_ctxs = []
            for _sk, _v in _cum.items():
                _st = _agg.get(_sk, {}) if isinstance(_agg, dict) else {}
                _av = int(_st.get("avail") or 0)
                _oa = _oagg.get(_sk) or {}
                o_ctxs.append({"sku": _sk, "channel": channel,
                               "order": {"quantity": _v, "open_qty": _v,
                                         "total_amount": float(_oa.get("amt") or 0),
                                         "max_quantity": int(_oa.get("mx") or 0),
                                         "min_quantity": int(_oa.get("mn") or 0),
                                         "line_count": int(_oa.get("lc") or 0),
                                         "unit_price": float(_oa.get("up") or 0)},
                               "order_qty": _v,
                               "available_stock": _av,
                               "inv": {"available_qty": _av, "safety_qty": 0, "in_transit_qty": 0}})
            _o_ctx_n = len(o_ctxs)
            if o_ctxs:
                r3, hits3 = evaluate_many("order.created", o_ctxs, channel, _order_rules, return_hits=True)
                hits = hits | hits3
                out = list(dict.fromkeys(out + r3))
        except Exception as _e:
            try_err('core_rules', '每日超卖评估降级', _e)
    _tick("order_created")
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
                _act = query("SELECT id, related_sku, warehouse, COALESCE(barcode,'') AS bc, "
                             "COALESCE(warehouse_type,'') AS wt FROM alerts "
                             "WHERE alert_type=%s AND channel=%s AND status='active' AND source='rules_engine'",
                             [_at, channel])
                _only = {str(x) for x in skus} if skus else None  # 增量评估只关本批 SKU(防误关)
                # 2026-10-30 跨模式保护: 只关闭**本模式会产出**的仓型 ——
                #   bbcc 产出 own+bc / traditional 产出 own+platform+platform_b;
                #   否则跑一次 traditional 评估会把 bbcc 的 bc 告警全部关掉(反之亦然)
                _close_wt = (("own", "bc") if str(_mode_e) == "bbcc"
                             else ("own", "platform", "platform_b"))
                # 2026-11-06 修: **SKU 级告警没有"仓型"**, 原来一律要求 wt ∈ _close_wt
                #   → oversell(warehouse_type='')永远不满足条件 → 永远不会被自动关闭。
                #   线上实证: jd 35 条 oversell 全是 active, 而其他类型都在正常流转;
                #   再叠加"全历史销量"口径, 就是"只增不减"。空仓型不受跨模式保护约束
                #   (它本来就不属于任何仓型, 不存在"跑另一个模式会误关"的问题)。
                _close = [a["id"] for a in _act
                          if (_only is None or str(a.get("related_sku")) in _only)
                          and (not str(a.get("wt") or "")
                               or str(a.get("wt") or "") in _close_wt)
                          and (_at, a.get("related_sku"), a.get("warehouse") or "", channel,
                               str(a.get("bc") or "")) not in hits]
                # 2026-11-02 批量误关护栏(S1-4): 评估口径是"未命中即关闭", 一旦本轮数据不全
                #   (日销/库存装载降级、计算变量注入失败、规则条件 JSON 解析异常……)→ hits 变少
                #   → 满屏 active 告警被一次性关掉, 而**关掉的告警不会再自己亮回来**(下轮要重新命中
                #   才生成), 用户侧表现为"告警凭空消失"。这里设一道闸:
                #   全量评估 + 拟关闭数 >=30 且 >= 该类型 active 的 80% → 判定为"更像数据异常而非
                #   集体恢复", 本轮跳过关闭并落一条 error 日志。宁可短时虚高, 不可批量误关。
                #   增量评估(_only 非 None)只关本批 SKU, 天然安全, 不受此闸限制。
                if _only is None and len(_close) >= 30 and len(_close) >= int(len(_act) * 0.8):
                    try:
                        execute("INSERT INTO quality_logs(log_type, level, message, source) "
                                "VALUES('rule_guard','error',%s,'rules')",
                                ("告警批量关闭已拦截: type=%s channel=%s 拟关=%s/%s active "
                                 "(全量评估单轮关闭比例>=80%%, 疑数据不全; 如确属批量恢复请人工关一次)"
                                 % (_at, channel, len(_close), len(_act)),))
                    except Exception:
                        pass
                    continue
                if _close:
                    _close_ids(_close, "eval_unhit")
        # 粒度切换收尾(2026-11-06): 旧粒度的 active 告警的新键**永远不会**再被命中,
        #   却会被上面的批量误关护栏挡住(单轮 >=80% 视为数据异常)。粒度不符是确定的失效键,
        #   不是"疑似数据不全", 单独按键失效关闭, 否则改粒度后旧告警永远挂在列表上。
        _close_grain_mismatch(channel, _grain_e)
    except Exception as _e:
            try_err('core_rules', '静默降级', _e)
    # 2026-10-29 评估审计(只增日志, 零数据影响): 事件数/ctx 数/命中数/耗时
    # 作用: 漏报/误报排查, 命中数与告警表对账, 评估耗时趋势(替代方案: 不进程内缓存规则——
    #       收益低(每渠道仅 3-4 次查询)而风险高(多实例规则变更不一致))
    try:
        _tick("close")
        try:
            global LAST_DIAG
            LAST_DIAG = {"rows": len(rows), "inv_rules": len(_inv_rules),
                         "need_calc": bool(_need_calc), "agg": len(_agg or {}),
                         "ctx": len(inv_ctxs), "hits": len(hits),
                         "marks": list(_marks)}
        except Exception:
            pass
        _aud = ("评估审计 %s: inv_ctx=%d daily_ctx=%d order_ctx=%d 命中=%d 耗时=%.1fs 分段[%s]"
                % (channel, len(inv_ctxs), len(daily_ctxs), _o_ctx_n, len(hits),
                   _t.time() - _t0, " ".join(_marks)))
        if hits or (_t.time() - _t0) > 1.0:
            execute("INSERT INTO quality_logs(log_type, level, message, source) "
                    "VALUES('perf_debug','info',%s,'rules')", (_aud,))
    except Exception as _e:
        try_err('core_rules', '评估审计降级', _e)
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

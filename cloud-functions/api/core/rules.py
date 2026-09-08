"""轻量规则引擎(方案 B, 从 PA backend/app/core/rules.py 移植, TiDB 适配)

定义 → 评估 → 动作:
- condition_json: {left, op, right, warehouse?, and?: {...}}
  left/right 支持字段引用(inv.available_qty)与四则运算/括号/max()
- evaluate(event, context): 匹配 active 规则(event+channel+mode) → 条件满足 → _action_create_alert(去重)
"""
import json
import os

from db import query, one, execute, executemany


def _resolve_single(expr, ctx):
    """单个字段或数字: inv.available_qty → ctx['inv']['available_qty']; '2' → 2"""
    expr = str(expr).strip()
    try:
        if expr.replace(".", "", 1).isdigit():
            return float(expr)
    except Exception:
        pass
    val = ctx
    for p in expr.split("."):
        if isinstance(val, dict):
            val = val.get(p, 0)
        else:
            return 0
    return val


def _resolve_muldiv(expr, ctx):
    """乘除: 字段*系数 / 字段*字段 / 字段/字段"""
    expr = str(expr).strip()
    if "*" not in expr and "/" not in expr:
        return _resolve_single(expr, ctx)
    import re
    tokens = re.split(r"([*/])", expr)
    result, op = None, None
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        if t in "*/":
            op = t
        else:
            val = float(_resolve_single(t, ctx))
            if result is None:
                result = val
            elif op == "*":
                result *= val
            elif op == "/":
                result = result / val if val != 0 else 0
    return result


def _resolve_value(expr, ctx):
    """解析表达式: 支持括号分组 + 加减 + 乘除"""
    import re
    expr = str(expr).strip()
    if not expr:
        return 0
    while "(" in expr:
        m = re.search(r"\(([^()]+)\)", expr)
        if not m:
            break
        expr = expr[:m.start()] + str(_resolve_value(m.group(1), ctx)) + expr[m.end():]
    if "+" in expr or "-" in expr:
        tokens = re.split(r"([+-])", expr)
        total, sign = 0.0, 1.0
        for t in tokens:
            t = t.strip()
            if not t:
                continue
            if t == "+":
                sign = 1.0
            elif t == "-":
                sign = -1.0
            else:
                total += sign * _resolve_muldiv(t, ctx)
        return total
    return _resolve_muldiv(expr, ctx)


def _resolve_any(expr, ctx):
    """单值解析(兼容 字段*系数 / 纯数字 / 字段表达式)"""
    expr = str(expr).strip()
    if "*" in expr:
        a, b = expr.split("*", 1)
        return float(_resolve_value(a, ctx)) * float(b.strip())
    if expr.replace(".", "", 1).isdigit():
        return float(expr)
    return _resolve_value(expr, ctx)


def _check_single(cond, ctx):
    """单条条件: {left, op, right, warehouse?}; right 支持 max(a,b)"""
    try:
        left_raw = cond.get("left", "0")
        right_raw = cond.get("right", "0")
        op = cond.get("op", "<")
        wh = cond.get("warehouse", "")
        if wh:
            inv_wh = (ctx.get("inv") or {}).get("warehouse_type", "")
            if inv_wh != wh:
                return False
        if str(right_raw).startswith("max("):
            inner = right_raw[4:-1]
            parts = [p.strip() for p in inner.split(",")]
            right = max(_resolve_any(parts[0], ctx),
                        _resolve_any(parts[1], ctx) if len(parts) > 1 else 0)
        elif str(right_raw).replace(".", "", 1).isdigit():
            right = float(right_raw)
        elif "." in str(right_raw) or str(right_raw).startswith("inv.") or str(right_raw).startswith("order."):
            right = _resolve_value(right_raw, ctx)
        else:
            right = right_raw
        left = _resolve_value(left_raw, ctx)
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        return False
    except Exception:
        return False


def _check_condition(cond, ctx):
    if not cond:
        return False
    if not _check_single(cond, ctx):
        return False
    sub = cond.get("and")
    if sub:
        return _check_single(sub, ctx)
    return True


def _action_create_alert(ctx):
    """生成告警(去重: alert_type+related_sku+warehouse+status+channel+source=rules_engine)

    逐仓粒度: 规则按库存行(SKU×仓)评估, 去重 key 含 warehouse —— 传统多仓下每个仓的
    低库存/补货风险独立成条, 弹窗/预览按数据 warehouse 列显示实际仓名
    """
    try:
        rule = ctx["rule"]
        sku = ctx.get("sku", "")
        channel = ctx.get("channel", "jd")
        wh = (ctx.get("inv") or {}).get("warehouse", "")
        dup = one("SELECT COUNT(*) AS c FROM alerts WHERE alert_type=%s AND related_sku=%s "
                  "AND status='active' AND channel=%s AND source='rules_engine' AND warehouse=%s",
                  [rule.get("alert_type", ""), sku, channel, wh]) or {}
        if int(dup.get("c") or 0) > 0:
            return
        title_tpl = rule.get("alert_title", "") or ""
        desc_tpl = rule.get("alert_desc", "") or ""
        try:
            title = title_tpl.format(**ctx) if "{" in title_tpl else title_tpl
            desc = desc_tpl.format(**ctx) if "{" in desc_tpl else desc_tpl
        except Exception:
            title, desc = title_tpl, desc_tpl
        execute("INSERT INTO alerts(alert_type, title, description, severity, status, source, "
                "related_sku, related_rule_id, warehouse_type, warehouse, channel) "
                "VALUES(%s,%s,%s,%s,'active','rules_engine',%s,%s,%s,%s,%s)",
                (rule.get("alert_type", ""), title, desc, rule.get("severity", "warning"),
                 sku, int(rule.get("id") or 0),
                 (ctx.get("inv") or {}).get("warehouse_type", ""), wh, channel))
    except Exception:
        pass


def evaluate(event, context):
    """单条评估(兼容): 内部转批量"""
    return evaluate_many(event, [context], context.get("channel"))


def evaluate_many(event, contexts, channel=None, rule_cache=None, return_hits=False):
    """批量评估同事件上下文(性能版): 规则一次加载 + 告警去重预载 + executemany 批量插入

    避免逐条 evaluate 的 N×查询——2000 SKU 全量评估从分钟级降到秒级
    return_hits=True 时返回 (triggered, hits); hits={(alert_type, sku, warehouse, channel)}
    供每日全量评估后反向关闭已恢复告警(完整性: 库存补足后旧告警不残留)
    """
    if not contexts:
        return []
    rules = rule_cache if rule_cache is not None else load_rules_for(event, channel)
    rules = [r for r in rules if not (r.get("mode") or "") or r.get("mode") == (contexts[0].get("mode") or "")]
    for _rl in rules:
        _rl["_params"] = _rule_params_loaded(_rl)
    if not rules:
        return []
    # 预载已有 active 告警 key(去重, 含 warehouse 维度——逐仓粒度)
    existing = set()
    for r in query("SELECT alert_type, related_sku, channel, warehouse FROM alerts "
                   "WHERE status='active' AND source='rules_engine'"):
        existing.add((r.get("alert_type"), r.get("related_sku"), r.get("channel"),
                      r.get("warehouse") or ""))
    inserts = []
    triggered = []
    hits = set()
    for ctx in contexts:
        sku = ctx.get("sku", "")
        channel_x = ctx.get("channel") or channel or "jd"
        wh = (ctx.get("inv") or {}).get("warehouse", "")
        for rule in rules:
            try:
                cond = json.loads(rule.get("condition_json") or "{}")
            except Exception:
                continue
            ctx2 = {**ctx, "rule": rule,
                    "avail": int((ctx.get("inv") or {}).get("available_qty") or 0),
                    "safety": int((ctx.get("inv") or {}).get("safety_qty") or 0),
                    "product_name": (ctx.get("inv") or {}).get("product_name", ""),
                    "params": rule.get("_params") or {}}
            if not _check_condition(cond, ctx2):
                continue
            at = rule.get("alert_type", "")
            hits.add((at, sku, wh, channel_x))  # 条件触发即命中(含已存在告警, 反向关闭勿误关)
            key = (at, sku, channel_x, wh)
            if key in existing:
                continue
            existing.add(key)
            title_tpl = rule.get("alert_title", "") or ""
            desc_tpl = rule.get("alert_desc", "") or ""
            try:
                title = title_tpl.format(**ctx2) if "{" in title_tpl else title_tpl
                desc = desc_tpl.format(**ctx2) if "{" in desc_tpl else desc_tpl
            except Exception:
                title, desc = title_tpl, desc_tpl
            inserts.append((at, title, desc, rule.get("severity", "warning"),
                            sku, int(rule.get("id") or 0),
                            (ctx.get("inv") or {}).get("warehouse_type", ""), wh, channel_x))
            triggered.append(rule.get("name") or str(rule.get("id")))
            hits.add((at, sku, wh, channel_x))
    if inserts:
        for i in range(0, len(inserts), 100):
            try:
                executemany("INSERT INTO alerts(alert_type, title, description, severity, status, "
                            "source, related_sku, related_rule_id, warehouse_type, warehouse, channel) "
                            "VALUES(%s,%s,%s,%s,'active','rules_engine',%s,%s,%s,%s,%s)",
                            inserts[i:i + 100])
            except Exception:
                pass
    if return_hits:
        return list(dict.fromkeys(triggered)), hits
    return list(dict.fromkeys(triggered))


def load_rules_for(event, channel=None):
    """一次性加载某事件的全部 active 规则(性能: evaluate_many 复用)"""
    params = [event]
    sql = ("SELECT * FROM rules WHERE is_active=1 AND event=%s "
           "AND (deleted_at IS NULL OR deleted_at='')")
    if channel:
        sql += " AND channel=%s"
        params.append(channel)
    return query(sql, params)


def _rule_params_loaded(rule):
    """规则 params(JSON 列)解析为 dict, 供条件引用 params.key"""
    if not rule:
        return {}
    try:
        p = rule.get("params")
        if isinstance(p, dict):
            return p
        return json.loads(p or "{}")
    except Exception:
        return {}


_CALC_VARS = ("adj_dos", "buffer", "otif", "ss_dyn", "accel_rate", "health.", "params.")


def evaluate_stock_skus(channel, limit=100000):
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
    _daily_rules = load_rules_for("scheduled.daily", channel)
    _inv_rules = load_rules_for("inventory.changed", channel)
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
        except Exception:
            pass
        try:
            # 看板计算参数(规则 params 优先, 兼容 config)
            from routes.dashboard import _rule_params
            _rp = _rule_params("stockout")
        except Exception:
            pass
        try:
            for _r3 in query("SELECT supplier_code, MAX(score) AS score FROM suppliers GROUP BY supplier_code"):
                try:
                    _sc = float(_r3.get("score") or 0)
                    _otif_map[_r3.get("supplier_code")] = max(min(_sc / 5.0, 1.0),
                                                              float(_rp.get("otif_min", 0.6))) if _sc > 0 else 1.0
                except Exception:
                    pass
        except Exception:
            pass
        try:
            from biz.sales import load_daily_sales_grouped, calc_sales_multi, rolling_predict
            _by_sku, _ = load_daily_sales_grouped(28, channel)
            _multi = calc_sales_multi(_by_sku, windows=[7, 14, 28])
            _fused = {s: rolling_predict(_multi[7].get(s, 0), _multi[14].get(s, 0),
                                         _multi[28].get(s, 0)) for s in _by_sku}
            for _s, _d in _by_sku.items():
                if len(_d) >= 7:
                    _vl = list(_d.values())
                    _m = sum(_vl) / len(_vl)
                    _v = sum((x - _m) ** 2 for x in _vl) / len(_vl)
                    _sigma[_s] = _v ** 0.5
        except Exception:
            pass
        try:
            from routes.dashboard import _hourly_accel
            _accel = _hourly_accel(channel, now,
                                   ratio=float(_rp.get("accel_ratio", 1.3)),
                                   min_qty=float(_rp.get("accel_min_qty", 10)))
        except Exception:
            pass
        if _need_health:
            try:
                from routes.dashboard import _health_index
                _health = (_health_index(channel) or {}).get("score")
            except Exception:
                pass
        # SKU 聚合(avail/transit/safety + 供应商)
        _agg = {}
        _prod_sup = {}
        try:
            for _r4 in query("SELECT sku, supplier_code FROM products WHERE channel=%s "
                             "AND (deleted_at IS NULL OR deleted_at='')", [channel]):
                _prod_sup[_r4.get("sku")] = _r4.get("supplier_code") or ""
        except Exception:
            pass
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
                    "safety_qty": int(r.get("safety_qty") or 0),
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
                _buf = _st["avail"] / max(max(_st["safety"], _ss), 1)
                base["inv"]["adj_dos"] = round(_adj, 2)
                base["inv"]["buffer"] = round(_buf, 2)
                base["inv"]["otif"] = _otif
                base["inv"]["ss_dyn"] = round(_ss, 1)
                base["inv"]["accel_rate"] = round(_accel.get(sku, 1.0), 2)
                base["health"] = {"score": _health if _health is not None else 999.0}
                base["health_score"] = _health if _health is not None else 999.0
                base["lit_trad"] = _lit_trad
                base["lit_bbcc"] = _lit_bbcc
            except Exception:
                pass
        inv_ctxs.append(base)
        daily_ctxs.append(base)
    r1 = evaluate_many("inventory.changed", inv_ctxs, channel, _inv_rules)
    r2, hits = evaluate_many("scheduled.daily", daily_ctxs, channel, _daily_rules, return_hits=True)
    out = list(dict.fromkeys(r1 + r2))
    # 恢复自动关闭(完整性): 每日全量快照下, 该事件规则 alert_type 的 active 告警
    # 若 SKU×仓 未命中(已恢复/不满足) → inactive, 防止库存补足后旧告警残留虚高计数
    try:
        if hits:
            _ats = sorted({h[0] for h in hits})
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
    except Exception:
        pass
    # 返回触发规则名(不泄漏查询细节)
    return out
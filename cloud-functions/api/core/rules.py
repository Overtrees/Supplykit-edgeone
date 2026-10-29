"""轻量规则引擎(方案 B, 从 PA backend/app/core/rules.py 移植, TiDB 适配)

定义 → 评估 → 动作:
- condition_json: {left, op, right, warehouse?, and?: {...}}
  left/right 支持字段引用(inv.available_qty)与四则运算/括号/max()
- evaluate(event, context): 匹配 active 规则(event+channel+mode) → 条件满足 → _action_create_alert(去重)
"""
import json

from db import query, one, execute, executemany
from routes.common import try_err
from core.rule_eval import evaluate_stock_skus, close_oversell_for_skus, close_orphan_for_skus  # 拆分 re-export(2026-10-15)
from core.rule_cond import _resolve_single, _resolve_muldiv, _resolve_value, _resolve_any, _check_single, _check_condition














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
    except Exception as _e:
            try_err('core_rules', '静默降级', _e)


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
    # 规则 mode(bbcc/traditional)不再按 ctx.mode 过滤排除 —— 原逻辑在 evaluate_stock_skus
    # (ctx 无 mode 键)下 mode 指定规则永不评估(静默失效); mode 现仅用于 params.lit fallback
    # 参数选择(bbcc→b_to_c+c_safety, traditional→lead_time, 跟随补货模式)
    for _rl in rules:
        _rl["_params"] = _rule_params_loaded(_rl)
    if not rules:
        return ([], set()) if return_hits else []
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
            if str((rule.get("_params") or {}).get("alert_enabled")) == "0":
                continue  # 告警开关关闭(兼容字符串/数字): 规则仅作为计算参数载体
            # params.lit 模式 fallback: 规则未设 lit 时按规则 mode 跟随补货周期
            # (bbcc→b_to_c+c_safety, traditional→lead_time; 与断货卡模式双线一致)
            _rp2 = dict(rule.get("_params") or {})
            if "lit" not in _rp2 and ctx.get("lit_trad") is not None:
                _m = str(rule.get("mode") or "")
                if not _m:
                    _m = "bbcc" if ctx.get("channel") == "jd" else "traditional"  # 未指定=跟随渠道默认模式
                _rp2["lit"] = ctx.get("lit_bbcc" if _m == "bbcc" else "lit_trad")
            ctx2 = {**ctx, "rule": rule,
                    "avail": int((ctx.get("inv") or {}).get("available_qty") or 0),
                    "safety": float((ctx.get("inv") or {}).get("safety_qty") or 0),
                    "product_name": (ctx.get("inv") or {}).get("product_name", ""),
                    "params": _rp2}
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
            except Exception as _e:
                try_err("rules", "告警批量写入失败(%d条)" % len(inserts[i:i + 100]), _e)
    if return_hits:
        return list(dict.fromkeys(triggered)), hits
    return list(dict.fromkeys(triggered))


def load_rules_for(event, channel=None, mode=None):
    """一次性加载某事件的全部 active 规则(性能: evaluate_many 复用)

    2026-10-28 mode 生效(原只存不读): rules.mode='' 或缺失 = 全部模式通用;
    指定 mode(bbcc/traditional) 的规则仅在当前补货模式匹配时参与评估。
    mode=None → 不过滤(向后兼容, 老调用方行为不变)
    """
    params = [event]
    sql = ("SELECT * FROM rules WHERE is_active=1 AND event=%s "
           "AND (deleted_at IS NULL OR deleted_at='')")
    if channel:
        sql += " AND channel=%s"
        params.append(channel)
    if mode:
        # 空串/NULL 表示"全部"→ 始终生效; 否则需与当前模式一致
        sql += " AND (mode IS NULL OR mode='' OR mode=%s)"
        params.append(mode)
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


# ── 条件字段白名单(规则保存校验用, 防静默 0 致恒真/恒假) ──
_VALID_FIELDS = {
    "inv.available_qty", "inv.safety_qty", "inv.in_transit_qty", "inv.warehouse_type",
    "inv.warehouse", "inv.product_name", "inv.adj_dos", "inv.buffer", "inv.otif",
    "inv.ss_dyn", "inv.accel_rate", "inv.days_since_last",
    "order.quantity", "order.total_qty", "order.total_amount", "order.order_status",
    "order.product_name", "order.max_quantity", "order.min_quantity", "order.line_count",
    "flow.quantity", "flow.warehouse", "flow.date",
    "inbound.quantity", "inbound.warehouse", "inbound.date",
    "outbound.quantity", "outbound.warehouse", "outbound.date",
    "batch.days_to_expire", "supplier.score", "inv.turnover_days",
    "sku", "product_name", "days_since_last", "stock", "avail", "safety",
    "lit_trad", "lit_bbcc", "health", "health_score",
}


def validate_condition(cond):
    """校验规则条件引用的字段有效性(创建/更新规则时调用)

    防笔误字段(如 inv.saftey_qty)静默解析为 0 → 条件恒真/恒假难排查;
    返回错误列表, 空 = 通过; params.* 为动态键通配
    """
    import re
    errors = []

    def _check_ref(s, side):
        for m in re.finditer(r"[a-z_]+\.[a-z_]+", s):
            ref = m.group(0)
            if ref.startswith("params."):
                continue
            if ref not in _VALID_FIELDS:
                errors.append("%s 字段无效: %s" % (side, ref))

    def _walk(c):
        if not isinstance(c, dict):
            return
        for k in ("left", "right"):
            v = c.get(k)
            if isinstance(v, str):
                s = v.strip()
                if s.startswith("max("):
                    for p in s[4:-1].split(","):
                        _check_ref(p.strip(), k)
                else:
                    _check_ref(s, k)
        sub = c.get("and")
        if isinstance(sub, dict):
            _walk(sub)
        for sub in (c.get("or") or []):
            if isinstance(sub, dict):
                _walk(sub)

    _walk(cond)
    return errors


_CALC_VARS = ("adj_dos", "buffer", "otif", "ss_dyn", "accel_rate", "health.", "params.", "safety_qty")




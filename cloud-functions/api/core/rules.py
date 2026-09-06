"""轻量规则引擎(方案 B, 从 PA backend/app/core/rules.py 移植, TiDB 适配)

定义 → 评估 → 动作:
- condition_json: {left, op, right, warehouse?, and?: {...}}
  left/right 支持字段引用(inv.available_qty)与四则运算/括号/max()
- evaluate(event, context): 匹配 active 规则(event+channel+mode) → 条件满足 → _action_create_alert(去重)
"""
import json
import os

from db import query, one, execute


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
    """生成告警(去重: alert_type+related_sku+status+channel+source=rules_engine)"""
    try:
        rule = ctx["rule"]
        sku = ctx.get("sku", "")
        channel = ctx.get("channel", "jd")
        dup = one("SELECT COUNT(*) AS c FROM alerts WHERE alert_type=%s AND related_sku=%s "
                  "AND status='active' AND channel=%s AND source='rules_engine'",
                  [rule.get("alert_type", ""), sku, channel]) or {}
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
                "related_sku, related_rule_id, warehouse_type, channel) "
                "VALUES(%s,%s,%s,%s,'active','rules_engine',%s,%s,%s,%s)",
                (rule.get("alert_type", ""), title, desc, rule.get("severity", "warning"),
                 sku, int(rule.get("id") or 0),
                 (ctx.get("inv") or {}).get("warehouse_type", ""), channel))
    except Exception:
        pass


def evaluate(event, context):
    """按事件匹配 active 规则(渠道/模式隔离), 条件满足则生成告警; 返回触发规则名列表"""
    results = []
    try:
        params = [event]
        sql = ("SELECT * FROM rules WHERE is_active=1 AND event=%s "
               "AND (deleted_at IS NULL OR deleted_at='')")
        if context.get("channel"):
            sql += " AND channel=%s"
            params.append(context["channel"])
        rules = query(sql, params)
        for rule in rules:
            try:
                cond = json.loads(rule.get("condition_json") or "{}")
            except Exception:
                continue
            rule_mode = rule.get("mode") or ""
            ctx_mode = context.get("mode") or ""
            if rule_mode and rule_mode != ctx_mode:
                continue
            ctx = {**context, "rule": rule,
                   "avail": int((context.get("inv") or {}).get("available_qty") or 0),
                   "safety": int((context.get("inv") or {}).get("safety_qty") or 0),
                   "product_name": (context.get("inv") or {}).get("product_name", "")}
            if _check_condition(cond, ctx):
                _action_create_alert(ctx)
                results.append(rule.get("name") or str(rule.get("id")))
    except Exception:
        pass
    return results


def evaluate_stock_skus(channel, limit=500):
    """对库存 SKU 批量评估 inventory.changed/scheduled.daily(cleansing 导入与每日任务用)"""
    rows = query("SELECT sku, warehouse, warehouse_type, product_name, available_qty, "
                 "safety_qty, in_transit_qty FROM inventory WHERE channel=%s "
                 "ORDER BY id LIMIT %s", [channel, limit])
    last_map = {}
    for r in query("SELECT sku, MAX(date) AS m FROM daily_sales_snapshot WHERE channel=%s GROUP BY sku", [channel]):
        last_map[str(r.get("sku"))] = str(r.get("m") or "")[:10]
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    triggered = []
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
        ctx = {
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
        triggered += evaluate("inventory.changed", {**ctx})
        triggered += evaluate("scheduled.daily", {**ctx})
    return list(set(triggered))
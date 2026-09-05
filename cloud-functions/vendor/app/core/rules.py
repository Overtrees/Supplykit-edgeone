"""轻量规则引擎：定义 → 评估 → 执行动作"""
import json
from datetime import datetime

# ─── 内置动作 ──────────────────────────────────────────────────────────────

def _action_create_alert(ctx):
    db = ctx.get('db') or get_db()
    # 去重按 渠道 + 来源 + alert_type + sku 四个维度（修复：缺 channel/source 导致跨渠道/跨来源误挡）
    existing = db.table("alerts").select("id")\
        .eq("alert_type", ctx['rule']['alert_type'])\
        .eq("related_sku", ctx.get('sku',''))\
        .eq("status", "active")\
        .eq("channel", ctx.get('channel', 'jd'))\
        .eq("source", "rules_engine").execute().data
    if existing:
        return
    db.table("alerts").insert({
        "alert_type": ctx['rule']['alert_type'],
        "title": ctx['rule']['alert_title'].format(**ctx),
        "description": ctx['rule']['alert_desc'].format(**ctx),
        "severity": ctx['rule'].get('severity', 'warning'),
        "source": "rules_engine",
        "related_sku": ctx.get('sku',''),
        "related_rule_id": int(ctx['rule'].get('id') or 0),
        "status": "active",
        "channel": ctx.get('channel', 'jd'),
        "warehouse_type": (ctx.get('inv') or {}).get('warehouse_type') or '',
    }).execute()
    # 同时记录事件到 events 表
    try:
        from app.api.routes.events import create_event
        create_event(db, 'rule.triggered', 'rule', str(ctx['rule'].get('id','')),
                     f"规则触发: {ctx['rule']['name']} → {ctx['rule']['alert_title'].format(**ctx)}",
                     {'rule_name': ctx['rule']['name'], 'alert_type': ctx['rule']['alert_type'],
                      'severity': ctx['rule'].get('severity','warning'), 'sku': ctx.get('sku','')})
    except Exception:
        pass

def _action_tag_slow_moving(ctx):
    db = ctx['db']
    db.table("products").update({"tag": "slow_moving"}).eq("sku", ctx.get('sku','')).execute()

def _action_suggest_restock(ctx):
    ctx['rule']['alert_type'] = 'replenish'
    _action_create_alert(ctx)

# ─── 规则定义 ──────────────────────────────────────────────────────────────

RULES = [
    {
        "name": "低库存预警",
        "event": "inventory.changed",
        "condition": lambda ctx: 0 < int(ctx['inv'].get('safety_qty',0)) and int(ctx['inv'].get('available_qty',0)) < int(ctx['inv'].get('safety_qty',0)),
        "alert_type": "low_stock",
        "alert_title": "低库存预警: {product_name}",
        "alert_desc": "可用 {avail} < 安全线 {safety}",
        "severity": "warning",
        "actions": [_action_create_alert, _action_suggest_restock],
    },
    {
        "name": "紧急补货",
        "event": "inventory.changed",
        "condition": lambda ctx: int(ctx['inv'].get('safety_qty',0)) > 0 and int(ctx['inv'].get('available_qty',0)) <= max(1, int(int(ctx['inv'].get('safety_qty',0)) * 0.3)),
        "alert_type": "replenish",
        "alert_title": "紧急补货: {product_name}",
        "alert_desc": "可用 {avail}，低于安全线 30%，建议补货",
        "severity": "error",
        "actions": [_action_create_alert],
    },
    {
        "name": "超卖保护",
        "event": "order.created",
        "condition": lambda ctx: ctx.get('order_qty',0) > ctx.get('available_stock',0),
        "alert_type": "oversell",
        "alert_title": "超卖告警: {sku}",
        "alert_desc": "订单数量 {order_qty} 超过可用库存 {available_stock}",
        "severity": "error",
        "actions": [_action_create_alert],
    },
    {
        "name": "滞销识别",
        "event": "scheduled.daily",
        "condition": lambda ctx: ctx.get('days_since_last', 999) > 30 and ctx.get('stock',0) > 0,
        "alert_type": "slow_moving",
        "alert_title": "滞销: {product_name}",
        "alert_desc": "{days_since_last} 天无销售，库存 {stock} 件",
        "severity": "warning",
        "actions": [_action_create_alert, _action_tag_slow_moving],
    },
]

# ─── 评估引擎 ──────────────────────────────────────────────────────────────

def _resolve_value(expr: str, ctx: dict):
    """解析条件表达式，支持组合运算+括号分组：
    inv.available_qty + inv.in_transit_qty                  （加法）
    (inv.available_qty + inv.in_transit_qty) / daily_sales  （括号分组）
    """
    expr = str(expr).strip()
    if not expr:
        return 0
    # 处理括号：递归计算括号内表达式
    while '(' in expr:
        import re
        m = re.search(r'\(([^()]+)\)', expr)
        if not m:
            break
        inner = _resolve_value(m.group(1), ctx)
        expr = expr[:m.start()] + str(inner) + expr[m.end():]
    if '+' in expr or '-' in expr:
        import re
        add_tokens = re.split(r'([+-])', expr)
        total = 0.0
        sign = 1.0
        for t in add_tokens:
            t = t.strip()
            if not t:
                continue
            if t == '+':
                sign = 1.0
            elif t == '-':
                sign = -1.0
            else:
                total += sign * _resolve_muldiv(t, ctx)
        return total
    return _resolve_muldiv(expr, ctx)


def _resolve_muldiv(expr, ctx):
    """解析乘除：字段*系数 / 字段*字段 / 字段/字段"""
    import re
    expr = str(expr).strip()
    if '*' not in expr and '/' not in expr:
        return _resolve_single(expr, ctx)
    md_tokens = re.split(r'([*/])', expr)
    result = None
    op = None
    for t in md_tokens:
        t = t.strip()
        if not t:
            continue
        if t in '*/':
            op = t
        else:
            val = float(_resolve_single(t, ctx))
            if result is None:
                result = val
            elif op == '*':
                result *= val
            elif op == '/':
                result = result / val if val != 0 else 0
    return result


def _resolve_single(expr, ctx):
    """解析单个字段或数字，如 inv.available_qty → ctx['inv']['available_qty']；'2' → 2"""
    expr = str(expr).strip()
    # 纯数字直接返回
    try:
        if expr.replace('.', '', 1).isdigit():
            return float(expr)
    except Exception:
        pass
    parts = expr.split('.')
    val = ctx
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p, 0)
        else:
            return 0
    return val  # 支持字符串和数值

def _check_single(cond: dict, ctx: dict) -> bool:
    """判断单条条件"""
    try:
        left_raw = cond.get('left', '0')
        right_raw = cond.get('right', '0')
        op = cond.get('op', '<')
        # 仓库过滤
        wh = cond.get('warehouse', '')
        if wh:
            inv_wh = ctx.get('inv', {}).get('warehouse_type', '')
            if inv_wh != wh:
                return False
        if right_raw.startswith('max('):
            inner = right_raw[4:-1]
            parts = [p.strip() for p in inner.split(',')]
            # 支持字段*系数表达式（如 inv.safety_qty*0.3）
            def _resolve_any(p):
                if '*' in p:
                    a, b = p.split('*', 1)
                    return float(_resolve_value(a, ctx)) * float(b.strip())
                if p.replace('.','',1).isdigit():
                    return float(p)
                return _resolve_value(p, ctx)
            right = max(_resolve_any(parts[0]), _resolve_any(parts[1]) if len(parts) > 1 else 0)
        elif right_raw.replace('.','',1).isdigit():
            right = float(right_raw)
        elif '.' in right_raw or right_raw.startswith('inv.'):
            right = _resolve_value(right_raw, ctx)
        else:
            right = right_raw
        left = _resolve_value(left_raw, ctx)
        if op == '<': return left < right
        if op == '<=': return left <= right
        if op == '>': return left > right
        if op == '>=': return left >= right
        if op == '==': return left == right
        if op == '!=': return left != right
        return False
    except Exception as e:
        import logging; logging.warning(f"[rules] evaluate condition error: {e}")
        return False

def _check_condition(cond: dict, ctx: dict) -> bool:
    """判断条件（含 AND 子条件）"""
    if not _check_single(cond, ctx):
        return False
    sub = cond.get('and')
    if sub:
        return _check_single(sub, ctx)
    return True

def evaluate(event: str, context: dict):
    """根据事件名匹配数据库中的规则，满足条件则执行动作（按渠道隔离）"""
    results = []
    try:
        from app.core.database import get_db
        db = get_db()
        # 只加载与当前事件匹配且符合渠道的规则（渠道隔离：jd/other 规则互不干扰）
        q = db.table("rules").select("*").eq("is_active", 1).eq("event", event)
        ctx_channel = context.get('channel')
        if ctx_channel:
            q = q.eq("channel", ctx_channel)
        db_rules = q.execute().data
        for rule in db_rules:
            try:
                cond = json.loads(rule.get('condition_json', '{}'))
            except: continue
            # 补货模式过滤
            rule_mode = rule.get('mode', '') or ''
            ctx_mode = context.get('mode', '') or ''
            if rule_mode and rule_mode != ctx_mode:
                continue
            ctx = {**context, 'rule': rule, 'avail': context.get('inv',{}).get('available_qty',0),
                   'safety': context.get('inv',{}).get('safety_qty',0),
                   'product_name': context.get('inv',{}).get('product_name','')}
            if _check_condition(cond, ctx):
                _action_create_alert(ctx)
                results.append(rule['name'])
    except Exception as e:
        results.append(f"DB rules error: {e}")
    return results

"""规则条件解析模块(2026-10-11 拆分——core/rules.py 减负; 纯函数——无 db 依赖)
函数: _resolve_single/_resolve_muldiv/_resolve_value/_resolve_any(表达式解析) / _check_single/_check_condition(条件判定)
依赖: re(标准库) + routes.common.try_err
"""
import re

from routes.common import try_err



def _resolve_single(expr, ctx):
    """单个字段或数字: inv.available_qty → ctx['inv']['available_qty']; '2' → 2"""
    expr = str(expr).strip()
    try:
        if expr.replace(".", "", 1).isdigit():
            return float(expr)
    except Exception as _e:
            try_err('core_rules', '静默降级', _e)
    # 2026-10-29 缺失字段语义修正: 原 val.get(p, 0) → 缺数据被当 0
    # 后果: batch.days_to_expire 缺失 → 0 → "剩余0天" → 临期规则**全部误触发**
    #       supplier.score 缺失 → 0 → 低分规则误报
    # 修: 缺失返回 None; 条件判定遇 None 直接跳过(不命中), 日志留痕便于排查
    val = ctx
    for p in expr.split("."):
        if isinstance(val, dict):
            if p not in val:
                return None
            val = val.get(p)
        else:
            return None
    if val is None:
        return None
    # 字符串数值化(前端 params 存字符串 '3', 兼容数字比较)
    if isinstance(val, str):
        try:
            return float(val)
        except Exception:
            return val
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
            _raw = _resolve_single(t, ctx)
            if _raw is None:
                return None  # 字段缺失 → 整个表达式缺失(不按 0 计算)
            val = float(_raw)
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
        _sub_v = _resolve_value(m.group(1), ctx)
        if _sub_v is None:
            return None  # 括号内字段缺失 → 整体缺失(原 str(None)='None' → 后续 float 崩溃)
        expr = expr[:m.start()] + str(_sub_v) + expr[m.end():]
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
                _mv = _resolve_muldiv(t, ctx)
                if _mv is None:
                    return None
                total += sign * _mv
        return total
    return _resolve_muldiv(expr, ctx)

def _resolve_any(expr, ctx):
    """单值解析(兼容 字段*系数 / 纯数字 / 字段表达式)"""
    expr = str(expr).strip()
    if "*" in expr:
        a, b = expr.split("*", 1)
        _av = _resolve_value(a, ctx)
        if _av is None:
            return None
        return float(_av) * float(b.strip())
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
            _a0 = _resolve_any(parts[0], ctx)
            _a1 = _resolve_any(parts[1], ctx) if len(parts) > 1 else 0
            if _a0 is None or _a1 is None:
                return False  # max() 内含缺失字段 → 条件跳过
            right = max(_a0, _a1)
        elif str(right_raw).replace(".", "", 1).isdigit():
            right = float(right_raw)
        elif "." in str(right_raw) or str(right_raw).startswith("inv.") or str(right_raw).startswith("order."):
            right = _resolve_value(right_raw, ctx)
        else:
            right = right_raw
        left = _resolve_value(left_raw, ctx)
        # 2026-10-29 字段缺失(未提供数据) → 该条件不成立(不误报), 而非按 0 比较
        if left is None or right is None:
            return False
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
    """条件判定(P2 扩展): (主条件 && and 子条件) 或 or 组任一满足即触发
    cond = {left,op,right,warehouse?, and?:{...}, or?:[{...},...]}
    """
    if not cond:
        return False
    if _check_single(cond, ctx):
        sub = cond.get("and")
        if not sub or _check_single(sub, ctx):
            return True
    # or 组(可拓展: 断货'时间线或缓冲破位'等多重条件)
    for _sub in (cond.get("or") or []):
        if isinstance(_sub, dict) and _check_single(_sub, ctx):
            return True
    return False

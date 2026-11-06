"""规则字段注册表 —— 白名单 / 注入判定 / 可用事件域的**唯一真源**

2026-11-05 根治「静默吞规则」:
    原来有三份手写列表, 任一份漏登记 → 规则页可选可保存, 但求值时 ctx 没有这个键
    → _resolve_single 返回 None → _check_single 直接 False → 规则**永不触发且不报错**:
      ① core/rules.py 的 _VALID_FIELDS     (保存校验白名单)
      ② core/rules.py 的 _CALC_VARS        (是否进入"计算变量注入"分支)
      ③ frontend/src/pages/rules/labels.tsx (规则页下拉)
    实测漏网: supplier.score / inv.turnover_days 在 ①③ 里、不在 ② →
      单独引用它们的规则走"快速路径", supplier/turnover 键不注入, 条件恒空。
    反向也漏: inv.locked_qty / order.unit_price 在 ③ 里、不在 ① → 选中保存直接被拒。

现在: 所有字段只在这里登记一次, 白名单与注入判定全部派生;
    规则页下拉的字段清单由 local_test 与本表比对(见 test_field_registry), 漂移即失败。

登记内容 = (kind, scopes):
    kind   base   快速路径的 ctx 里就有, 不需要额外加载
           calc   需要 SKU 聚合/日销/供应商/批次等计算量 —— rule_eval 的 _need_calc 分支注入
           event  由具体事件上下文提供(order.* / flow.* 等)
    scopes inv    库存行上下文(inventory.changed / scheduled.daily)
           order  订单上下文(order.created)
           flow   出入库上下文(inbound.created / outbound.created)
    只有登记了某个 scope 的字段, 在该事件的规则里才允许保存 ——
    否则放行 = 保证求值为空 = 静默失效。
"""
import json
import re

BASE = "base"
CALC = "calc"
EVENT = "event"

_INV = ("inv",)
_ORD = ("order",)
_FLW = ("flow",)
_ALL = ("inv", "order", "flow")

FIELD_SPEC = {
    # ── 顶层标量: 库存行直接给(evaluate_stock_skus 的 base) ──
    "sku": (BASE, _ALL),
    "channel": (BASE, _ALL),
    "barcode": (BASE, _INV),
    "product_name": (BASE, _INV),
    "stock": (BASE, _INV),
    "avail": (BASE, _INV),          # = inv.available_qty 的顶层别名
    "safety": (BASE, _INV),
    "transit": (BASE, _INV),
    "sellable": (BASE, _INV),       # 可用 + 在途(与断货/采购同源)
    "days_since_last": (BASE, _INV),
    "daily_sales": (BASE, _INV),
    # ── inv.* : 静态列 ──
    "inv.available_qty": (BASE, _ALL),
    # 注: 只有库存行上下文给真实值。order/inbound/outbound 的 ctx 里 in_transit_qty 与
    #   safety_qty 是**写死的 0**, 放行它们等于让"低于安全线"在订单/出入库事件里恒不成立 →
    #   作用域收窄到 inv, 在这些事件的规则里选择会被保存校验当场拒绝。
    "inv.in_transit_qty": (BASE, _INV),
    "inv.locked_qty": (BASE, _INV),
    "inv.warehouse_type": (BASE, _INV),
    "inv.warehouse": (BASE, _INV),
    "inv.product_name": (BASE, _INV),
    # ── inv.* : 计算注入(_need_calc 分支) ──
    #   safety_qty 在快速路径里是常量 0 → 必须走注入, 否则"低于安全线"恒不成立
    "inv.safety_qty": (CALC, _INV),
    "inv.adj_dos": (CALC, _INV),
    "inv.buffer": (CALC, _INV),
    "inv.otif": (CALC, _INV),
    "inv.ss_dyn": (CALC, _INV),
    "inv.accel_rate": (CALC, _INV),
    "inv.turnover_days": (CALC, _INV),
    # ── 计算注入: 健康度/前置期 ──
    "health.score": (CALC, _INV),
    "health_score": (CALC, _INV),
    "lit_trad": (CALC, _INV),
    "lit_bbcc": (CALC, _INV),
    # ── 计算注入: 批次效期(仅最近到期批) ──
    "batch.days_to_expire": (CALC, _INV),
    # ── 计算注入: 供应商(供应商评分规则) ──
    "supplier.score": (CALC, _INV),
    "supplier.code": (CALC, _INV),
    # ── 计算注入: 自有仓供给侧(仅 warehouse_type=own 的行) ──
    "own.available": (CALC, _INV),
    "own.transit": (CALC, _INV),
    "own.po_transit": (CALC, _INV),
    "own.can_ship": (CALC, _INV),
    "own.downstream_ds": (CALC, _INV),
    "own.downstream_avail": (CALC, _INV),
    "own.b2c_days": (CALC, _INV),
    "own.need": (CALC, _INV),
    "own.gap": (CALC, _INV),
    "own.cover_days": (CALC, _INV),
    # ── 订单上下文(order.created) ──
    "order.quantity": (EVENT, _ORD),
    # 2026-11-06 超卖口径: 判定量是**未发货订单量**(待确认/待发货 且 近 N 天),
    #   不是历史累计销量 —— 后者只要卖过一阵就恒大于当前可用, 每个 SKU 都"超卖"。
    #   `open_qty` 是判定用的那个数; `total_qty` 保留为同一值(老规则条件不改也能用);
    #   `lifetime_qty` 给全历史量, 供规则页排查"到底卖了多少"。
    "order.open_qty": (EVENT, _ORD),
    "order.lifetime_qty": (EVENT, _ORD),
    "order.total_qty": (EVENT, _ORD),
    "order.total_amount": (EVENT, _ORD),
    "order.max_quantity": (EVENT, _ORD),
    "order.min_quantity": (EVENT, _ORD),
    "order.line_count": (EVENT, _ORD),
    "order.unit_price": (EVENT, _ORD),
    # 注: order.order_status / order.product_name 原在白名单里, 但订单上下文是"按 SKU 聚合"
    #   (一个 SKU 多单多状态), 给不出有意义的单值 → 不登记。规则页也没有这两项。
    #   历史规则若引用它们, 编辑保存时会收到明确错误, 而不是继续静默不触发。
    # ── 出入库上下文(inbound.created / outbound.created) ──
    #   三个前缀在 cleansing 的 flow ctx 里同时注入同一份数量/仓/日期,
    #   保留是为了兼容用户按"进/出"语义分别写的规则
    "flow.quantity": (EVENT, _FLW),
    "flow.warehouse": (EVENT, _FLW),
    "flow.date": (EVENT, _FLW),
    "inbound.quantity": (EVENT, _FLW),
    "inbound.warehouse": (EVENT, _FLW),
    "inbound.date": (EVENT, _FLW),
    "outbound.quantity": (EVENT, _FLW),
    "outbound.warehouse": (EVENT, _FLW),
    "outbound.date": (EVENT, _FLW),
}

# 规则事件 → scope。未登记的事件一律只做"字段存在性"校验(不判 scope),
#   避免新事件接入时保存被误拦。
EVENT_SCOPE = {
    "inventory.changed": "inv",
    "scheduled.daily": "inv",
    "order.created": "order",
    "inbound.created": "flow",
    "outbound.created": "flow",
}

VALID_FIELDS = frozenset(FIELD_SPEC)

# 条件校验/求值只需"某个引用是否作用于计算变量"这一个判断
_REF_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_SKIP_TOKENS = frozenset({"max", "min", "abs", "round", "true", "false", "null"})


def _refs_expr(expr):
    """从一个表达式串里抽字段引用(去重保序), 排除函数名/布尔字面量"""
    out = []
    for m in _REF_RE.finditer(str(expr or "")):
        tok = m.group(0)
        if tok in _SKIP_TOKENS:
            continue
        if tok not in out:
            out.append(tok)
    return out


def refs_in(cond):
    """抽出条件里 left/right 引用的全部字段(递归 and / or)

    cond 可为 dict 或 condition_json 字符串; 只解析 left/right 的**值**,
    不能对整段 JSON 做正则 —— 否则 and/or/left/right 这些键名会被当成字段。
    """
    if isinstance(cond, str):
        try:
            cond = json.loads(cond or "{}")
        except Exception:
            return _refs_expr(cond)
    out = []

    def _walk(c):
        if not isinstance(c, dict):
            return
        for k in ("left", "right"):
            v = c.get(k)
            if isinstance(v, (str, int, float)):
                for r in _refs_expr(v):
                    if r not in out:
                        out.append(r)
        _walk(c.get("and"))
        for sub in (c.get("or") or []):
            _walk(sub)

    _walk(cond)
    return out


def needs_calc(cond):
    """该条件是否需要走"计算变量注入"分支

    未知字段(历史脏数据/未登记的新字段)保守返回 True:
      宁可多算一次(几条 SQL), 也不能让它悄悄走快速路径 → ctx 缺键 → 恒不触发。
    """
    for ref in refs_in(cond):
        if ref.startswith("params."):
            return True          # params.* 由 _rule_params_loaded 提供, 属注入路径
        spec = FIELD_SPEC.get(ref)
        if spec is None:
            return True
        if spec[0] == CALC:
            return True
    return False


def unknown_refs(cond):
    """未登记字段(用于评估期留痕, 让"静默不触发"变成"日志里看得见")"""
    return [r for r in refs_in(cond) if r not in FIELD_SPEC and not r.startswith("params.")]


def validate_refs(cond, event=None):
    """校验条件字段: 存在性 + 事件可用域

    返回错误列表(空 = 通过)。error 文案直接给用户看, 必须说清"哪个字段、为什么不行"。
    """
    errors = []
    scope = EVENT_SCOPE.get(str(event or "")) if event else None
    for ref in refs_in(cond):
        if ref.startswith("params."):
            continue
        spec = FIELD_SPEC.get(ref)
        if spec is None:
            errors.append("字段无效: %s(未登记, 检查拼写)" % ref)
            continue
        if scope and scope not in spec[1]:
            errors.append("字段 %s 不适用于事件 %s(该事件上下文不提供此值)" % (ref, event))
    return errors


def calc_tokens():
    """派生的子串标记(兼容旧代码/测试对 _CALC_VARS 的引用)

    注意: 这只是**兼容层**, 真正的判定请用 needs_calc() ——
    子串匹配会把 params.foo_buffer 之类误判, 也会漏掉无点字段。
    """
    toks = set()
    for name, (kind, _scopes) in FIELD_SPEC.items():
        if kind != CALC:
            continue
        if "." in name:
            toks.add(name.split(".")[-1])
            toks.add(name)
        else:
            toks.add(name)
    # 整组前缀(该 root 下全部字段都是 calc 时才算) —— own./batch./supplier.
    for root in ("own", "batch", "supplier"):
        if all(FIELD_SPEC[k][0] == CALC for k in FIELD_SPEC if k.startswith(root + ".")):
            toks.add(root + ".")
    toks.add("params.")
    return tuple(sorted(toks))

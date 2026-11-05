"""规则测试弹窗的模拟 ctx 构造

2026-11-05 从 routes/rules.py 抽出(该文件已超 600 行红线)。
单一职责: 把测试弹窗入参(body.inv/order/own/batch/supplier/params)拼成**与真实评估同形**的 ctx。

踩过四次, 每次表现都一样: 引擎里有注入、测试 ctx 没注入 → `_resolve_single` 取到 None
→ `_check_single` 直接 False → 弹窗永远显示"未触发", 用户以为规则写错了。
  ① own.*      (2026-11-01)
  ② batch.*    (2026-11-01)
  ③ supplier.score / inv.turnover_days (2026-11-05)
  ④ inv.warehouse_type 默认空串 → 规则上的仓型守卫 `"" != "own"` 恒 False (2026-11-05)
新增计算变量时**必须同步这里**; local_test 有静态断言兜底。
"""
from routes.common import try_err

# 评估链路上真实存在的仓型; 其它值(含空串)一律落到 own, 见 build_sim_ctx 注释
_WT_OK = ("own", "platform", "platform_b", "bc")


def build_sim_ctx(cond_json, params, body, channel="jd"):
    """构造模拟 ctx; cond_json 为字符串(用于判断"这条规则引用了哪组变量", 只注入需要的组)"""
    inv = body.get("inv") or {}
    order = body.get("order") or {}
    cj = str(cond_json or "")
    av0 = int(inv.get("available_qty") or 0)
    tr0 = int(inv.get("in_transit_qty") or 0)
    sf0 = int(inv.get("safety_qty") or 0)
    # 仓型: 真实评估里每一行**都有**具体仓型(own / platform / platform_b, bbcc 另有 bc 桶),
    #   从来没有"空仓型"的行。原默认 "" 会让规则上的仓型守卫(`cond.warehouse`)判
    #   `"" != "own"` → 直接 False → 弹窗永远"未触发"(第四次同类: 引擎里有, 测试 ctx 里没有)。
    #   未传/非法值统一落 own —— 自有仓是主场景, 也是内置自有仓规则的前置;
    #   要测别的仓型由弹窗下拉显式选择, 不再用空串冒充"任意仓型"。
    _wt = str(inv.get("warehouse_type") or "").strip()
    if _wt not in _WT_OK:
        _wt = "own"
    ctx = {
        "inv": {
            "available_qty": av0,
            "safety_qty": sf0,
            "in_transit_qty": tr0,
            "warehouse_type": _wt,
            "days_since_last": int(inv.get("days_since_last") or 0),
            "adj_dos": float(inv.get("adj_dos") or 0),
            "buffer": float(inv.get("buffer") or 0),
        },
        "order": {"quantity": int(order.get("quantity") or 0),
                  "total_qty": int(order.get("total_qty") or order.get("quantity") or 0),
                  "total_amount": float(order.get("total_amount") or 0)},
        "channel": channel,
        "days_since_last": int(inv.get("days_since_last") or 0),
        "stock": av0,
        "params": params,
        # 顶层别名(真实 ctx 由 evaluate_many 注入, 缺了会让 `sellable < safety` 这类条件恒 False)
        "avail": av0, "safety": sf0, "transit": tr0, "sellable": av0 + tr0,
        "product_name": str(inv.get("product_name") or ""),
        "daily_sales": float(inv.get("daily_sales") or 0),
        "health": ({"score": float((body.get("health") or {}).get("score") or 100)}
                   if "health." in cj else None),
    }
    # supplier.*: 未传默认满分(低分规则不触发), 传了按传入值
    _sup = body.get("supplier")
    if isinstance(_sup, dict) and _sup:
        ctx["supplier"] = dict(_sup)
    elif "supplier." in cj:
        ctx["supplier"] = {"score": 100.0, "code": ""}
    # inv.turnover_days: 传了用传入值, 否则按 可用/日销 推(与引擎同式)
    if "inv.turnover_days" in cj:
        _ds = float(inv.get("daily_sales") or 0)
        _td = inv.get("turnover_days")
        ctx["inv"]["turnover_days"] = (float(_td) if _td is not None
                                       else (round(av0 / _ds, 2) if _ds > 0 else 0.0))
    # own.*: 先补基础量, 再按引擎同式推 need/gap/cover_days(前端留空 = 不传)
    #   注入条件与引擎对齐: **仅 own 行**有 own.* 键(rule_eval: `warehouse_type == 'own'`)。
    #   以前这里无条件注入 → 选"平台仓"测自有仓规则会显示"已触发", 而线上永远不触发,
    #   是"弹窗说谎"的另一个方向。
    _own = body.get("own")
    _sim = dict(_own) if isinstance(_own, dict) else {}
    if "own." in cj and _wt == "own":
        try:
            _b2c = float(params.get("b2c_days") or 6)
        except Exception:
            _b2c = 6.0
        try:
            _dsd = float(_sim.get("downstream_ds") or 10)
        except Exception:
            _dsd = 10.0
        _sim.setdefault("available", av0)
        _sim.setdefault("transit", tr0)
        _sim.setdefault("po_transit", 0.0)
        _sim.setdefault("can_ship", av0 + tr0 + float(_sim["po_transit"]))
        _sim.setdefault("downstream_ds", _dsd)
        _sim.setdefault("downstream_avail", 0.0)
        _sim.setdefault("b2c_days", _b2c)
        _sim.setdefault("need", round(float(_sim["downstream_ds"]) * float(_sim["b2c_days"]), 2))
        if "gap" not in _sim:
            try:
                _sim["gap"] = round(float(_sim["need"]) - float(_sim["can_ship"]), 2)
            except Exception as _e:
                try_err('rules', '测试模式 own.gap 推导失败', _e)
                _sim["gap"] = 0.0
        if "cover_days" not in _sim:
            _dsn = float(_sim["downstream_ds"])
            _sim["cover_days"] = round(float(_sim["available"]) / _dsn, 2) if _dsn > 0 else 0.0
        ctx["own"] = _sim
    # batch.*: 未传默认 365 天(远未临期), 用户改小即可观察触发
    _batch = body.get("batch")
    if isinstance(_batch, dict) and _batch:
        ctx["batch"] = dict(_batch)
    elif "batch." in cj:
        ctx["batch"] = {"days_to_expire": 365, "qty": float(av0)}
    return ctx

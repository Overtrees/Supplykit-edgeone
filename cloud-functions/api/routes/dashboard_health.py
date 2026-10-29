"""dashboard 健康指数模块(2026-10-11 拆分第三步——dashboard.py 减负)
函数: _sync_health_snapshot(健康快照写) / _health_index(动态安全线四档判定)
依赖: db/biz.sales/query——无 dashboard 内部依赖(独立)
"""
from datetime import datetime, timedelta, timezone

from db import query, one, execute
from routes.common import try_err
from routes.dashboard_stock_risk import _rule_params  # health 判定用规则参数(2026-10-11 移动时补依赖)


def _sync_health_snapshot(channel, health):
    """健康分快照幂等写入(每次 summary 请求, ON DUPLICATE 键 date+channel, 多实例安全)——
    健康趋势数据源: 不依赖 schedules, 看板每次访问刷新当天分"""
    try:
        if not isinstance(health, dict):
            try:
                import json as _j2
                health = _j2.loads(health or "{}")
            except Exception:
                return
        _sc = health.get("score")
        if _sc is None and not health.get("own"):
            return
        execute("INSERT INTO health_snapshot(`date`, channel, score, own_score, platform_score, bc_score) "
                "VALUES(%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE score=VALUES(score), own_score=VALUES(own_score), "
                "platform_score=VALUES(platform_score), bc_score=VALUES(bc_score)",
                [datetime.now(timezone.utc).strftime("%Y-%m-%d"), channel,
                 int(_sc) if _sc is not None else -1,
                 int((health.get("own") or {}).get("score") or -1),
                 int((health.get("platform") or {}).get("score") or -1),
                 int((health.get("bc") or {}).get("score") or -1)])
    except Exception as _e:
            try_err('dashboard', '静默降级', _e)

def _health_index(channel, mode="bbcc", _inv_rows=None, _ds_map=None):
    """健康指数四档(动态安全线判定): platform/platform_b 用补货周期(按 mode), own 用采购周期
    ds×周期 = 动态安全线; ds≤0 → 安全线 0 → 有库存即 healthy(无销量不判不健康); avail=0 → out"""
    rows = _inv_rows if _inv_rows is not None else query(
        "SELECT sku, warehouse_type, available_qty FROM inventory "
        "WHERE channel=%s AND warehouse IS NOT NULL AND warehouse!=''", [channel])
    # 日销(共享 get_sales_digest, 与规则引擎/低库存卡同源)
    ds_map = _ds_map if _ds_map is not None else {}
    if not ds_map:
        try:
            from biz.sales import get_sales_digest
            _, ds_map, _ = get_sales_digest(channel)
        except Exception as _e:
                try_err('dash', '健康指数日销降级', _e)
    # 动态安全线周期(与补货/采购备注同口径)
    _cfg = {}
    try:
        _cfg = {r.get("key"): r.get("value") for r in
                query("SELECT `key`, value FROM replenishment_config WHERE channel=%s OR channel=''", [channel])}
    except Exception as _e:
            try_err('dash', '健康指数配置加载降级', _e)

    def _mc(key, mode2, default):
        v = _cfg.get("mode_%s_%s" % (mode2, key))
        if v is None:
            v = _cfg.get(key)
        try:
            return int(float(v or default))
        except Exception:
            return default
    _hp_bbcc = (_mc("b_to_c_days", mode, 3) + _mc("c_safety_days", mode, 0)
                + _mc("ship_to_b_days", mode, 0) + _mc("safety_multiplier", mode, 0))
    _hp_trad = _mc("lead_time_days", mode, 10) + _mc("safety_multiplier", mode, 0)
    _hp_pur = _mc("purchase_lead_days", mode, 14) + _mc("purchase_safety_days", mode, 3)
    _period = _hp_bbcc if mode == "bbcc" else _hp_trad

    _z = {"healthy": 0, "warning": 0, "out_of_stock": 0, "total": 0}
    hw = {"own": dict(_z), "platform": dict(_z), "platform_b": dict(_z)}
    bc_agg = {}
    # 2026-10-29 按 69码(用户定案 A): 库存行按码拆分 → **逐行独立判定与计数**
    #   同 SKU 不同 69码 各自算一个健康单元(粒度更细, 用户可见数字随之变为"库存行数")
    for r in rows:
        wt = str(r.get("warehouse_type") or "")
        avail = int(r.get("available_qty") or 0)
        sku = str(r.get("sku") or "")
        _ds = ds_map.get(sku, 0)
        _per = _hp_pur if wt == "own" else _period
        _dyn = round(_ds * _per, 1) if _ds > 0 and _per > 0 else 0.0
        st = hw.get(wt)
        if st is None:
            continue
        st["total"] += 1
        if avail <= 0:
            st["out_of_stock"] += 1
        elif _dyn <= 0 or avail >= _dyn:
            st["healthy"] += 1
        else:
            st["warning"] += 1
        if wt in ("platform", "platform_b"):
            b = bc_agg.setdefault(sku, {"avail": 0, "dyn": 0.0})
            b["avail"] += avail
            b["dyn"] = max(b["dyn"], _dyn)
    bc = {"healthy": 0, "warning": 0, "out_of_stock": 0, "total": len(bc_agg)}
    for b in bc_agg.values():
        if b["avail"] <= 0:
            bc["out_of_stock"] += 1
        elif b["dyn"] <= 0 or b["avail"] >= b["dyn"]:
            bc["healthy"] += 1
        else:
            bc["warning"] += 1

    # 健康分档参数: 内置'库存健康监控'规则 params 配置(默认 85/60 与硬编码一致)
    _hrp = _rule_params("health", channel)
    try:
        _hg = float(_hrp.get("health_good", 85))
        _hw = float(_hrp.get("health_warning", 60))
    except Exception:
        _hg, _hw = 85.0, 60.0

    def _score(r):
        total = int(r.get("total") or 0)
        healthy = int(r.get("healthy") or 0)
        # 空维度(total=0, 如 other 渠道无 B 仓) → score=None/level=empty, 前端显示"—"而非 100 分误导
        if total == 0:
            return {"score": None, "healthy": 0, "warning": 0, "out_of_stock": 0, "total": 0,
                    "level": "empty"}
        score = round(healthy / total * 100, 0)
        return {"score": score, "healthy": healthy, "warning": int(r.get("warning") or 0),
                "out_of_stock": int(r.get("out_of_stock") or 0), "total": total,
                "level": "good" if score >= _hg else ("warning" if score >= _hw else "danger")}

    all_rows = {"healthy": sum(int(hw.get(k, _z).get("healthy") or 0) for k in hw),
                "warning": sum(int(hw.get(k, _z).get("warning") or 0) for k in hw),
                "out_of_stock": sum(int(hw.get(k, _z).get("out_of_stock") or 0) for k in hw),
                "total": sum(int(hw.get(k, _z).get("total") or 0) for k in hw)}
    return {"own": _score(hw.get("own", _z)), "platform": _score(hw.get("platform", _z)),
            "platform_b": _score(hw.get("platform_b", _z)), "bc": _score(bc),
            "score": _score(all_rows)["score"], "level": _score(all_rows)["level"]}

"""原生 rules 路由(方案 B): 规则引擎 CRUD + test"""
import json

from fastapi import APIRouter
from fastapi import Request

from db import query, one, execute
from routes.common import ok, fail, traced

router = APIRouter(tags=["rules"])

_FIELDS = "id, name, event, condition_json, alert_type, alert_title, alert_desc, severity, is_active, channel, mode, created_at, updated_at, deleted_at"


@router.get("/rules")
@traced
def list_rules(channel: str = "jd", include_deleted: int = 0):
    """规则列表(channel=all 或空返回全部, 否则按渠道过滤——修复 jd+other 混返致前端重复显示)"""
    conds, params = [], []
    if channel and channel != "all":
        conds.append("channel=%s")
        params.append(channel)
    if not include_deleted:
        conds.append("(deleted_at IS NULL OR deleted_at='')")
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows = query("SELECT %s FROM rules%s ORDER BY id ASC" % (_FIELDS, where), params)
    out = []
    for r in rows:
        try:
            r["condition"] = json.loads(r.get("condition_json") or "{}")
        except Exception:
            r["condition"] = {}
        out.append(r)
    return ok(out)


@router.post("/rules")
@traced
async def create_rule(request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    name = d.get("name") or ""
    if not name:
        return fail("缺少 name")
    cond = d.get("condition") or d.get("condition_json") or {}
    execute("INSERT INTO rules(name, event, condition_json, alert_type, alert_title, alert_desc, severity, is_active, channel, mode) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (name, d.get("event", ""), json.dumps(cond, ensure_ascii=False),
             d.get("alert_type", ""), d.get("alert_title", ""), d.get("alert_desc", ""),
             d.get("severity", "warning"), 1 if d.get("is_active", 1) else 0,
             d.get("channel", "jd"), d.get("mode", "")))
    from routes.analysis_cache import invalidate_all
    invalidate_all()  # 规则新建 → 看板/接口缓存即时失效
    return ok({"id": 0})


@router.put("/rules/{rid}")
@traced
async def update_rule(rid: int, request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    sets = []
    params = []
    for f in ("name", "event", "alert_type", "alert_title", "alert_desc", "severity", "mode", "channel"):
        if f in d:
            sets.append("`%s` = %%s" % f)
            params.append(d[f])
    if "is_active" in d:
        sets.append("is_active = %s")
        params.append(1 if d["is_active"] else 0)
    if "condition" in d:
        sets.append("condition_json = %s")
        params.append(json.dumps(d["condition"], ensure_ascii=False))
    if not sets:
        return fail("无更新字段")
    params.append(rid)
    execute("UPDATE rules SET %s WHERE id=%%s" % ", ".join(sets), params)
    from routes.analysis_cache import invalidate_all
    invalidate_all()  # 规则编辑 → 缓存即时失效
    return ok({})


def _close_alerts_for_rules(ids):
    """规则删除/停用时联动关闭其产出的 active 告警(PA 行为, 免等每日孤儿清理)"""
    try:
        if not ids:
            return
        ph = ",".join(["%s"] * len(ids))
        pairs = set()
        for r in query("SELECT alert_type, channel FROM rules WHERE id IN (%s)" % ph, ids):
            pairs.add((r.get("alert_type"), r.get("channel") or "jd"))
        for at, ch in pairs:
            execute("UPDATE alerts SET status='inactive' WHERE alert_type=%s AND channel=%s "
                    "AND status='active' AND source IN ('rules_engine','event_bus')", [at, ch])
    except Exception:
        pass


@router.delete("/rules/{rid}")
@traced
def delete_rule(rid: int):
    # 软删除 + 联动关闭该类告警
    _close_alerts_for_rules([rid])
    execute("UPDATE rules SET deleted_at=NOW(), is_active=0 WHERE id=%s", [rid])
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok({})


@router.post("/rules/{rid}/restore")
@traced
def restore_rule(rid: int):
    execute("UPDATE rules SET deleted_at='', is_active=1 WHERE id=%s", [rid])
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok({})


@router.post("/rules/{rid}/permanent-delete")
@traced
def permanent_delete_rule(rid: int):
    execute("DELETE FROM rules WHERE id=%s", [rid])
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok({})


@router.post("/rules/batch")
@traced
async def rules_batch(request: Request):
    """批量操作: {action: active|inactive|delete|restore|purge, ids: []}"""
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    action = d.get("action", "")
    ids = d.get("ids") or []
    if not ids:
        return fail("缺少 ids")
    ph = ",".join(["%s"] * len(ids))
    if action == "active":
        execute("UPDATE rules SET is_active=1 WHERE id IN (%s)" % ph, ids)
    elif action == "inactive":
        # 停用联动关闭该类告警(PA 行为)
        _close_alerts_for_rules(ids)
        execute("UPDATE rules SET is_active=0 WHERE id IN (%s)" % ph, ids)
    elif action == "delete":
        _close_alerts_for_rules(ids)
        execute("UPDATE rules SET deleted_at=NOW(), is_active=0 WHERE id IN (%s)" % ph, ids)
    elif action == "restore":
        execute("UPDATE rules SET deleted_at='', is_active=1 WHERE id IN (%s)" % ph, ids)
    elif action == "purge":
        _close_alerts_for_rules(ids)
        execute("DELETE FROM rules WHERE id IN (%s)" % ph, ids)
    else:
        return fail("未知操作: " + str(action))
    from routes.analysis_cache import invalidate_all
    invalidate_all()  # 批量启用/停用/删除/恢复/永久删 → 看板告警计数/规则联动即时失效
    return ok({"updated": len(ids)})


@router.post("/rules/{rid}/test")
@traced
async def test_rule(rid: int, request: Request):
    """规则引擎可视化调试: 传入模拟库存/订单数据, 真实评估条件是否触发"""
    row = one("SELECT * FROM rules WHERE id=%s", [rid])
    if not row:
        return fail("规则不存在", 404)
    try:
        cond = json.loads(row.get("condition_json") or "{}")
    except Exception:
        cond = {}
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    inv = body.get("inv") or {}
    order = body.get("order") or {}
    ctx = {
        "inv": {
            "available_qty": int(inv.get("available_qty") or 0),
            "safety_qty": int(inv.get("safety_qty") or 0),
            "in_transit_qty": int(inv.get("in_transit_qty") or 0),
            "warehouse_type": inv.get("warehouse_type", ""),
            "days_since_last": int(inv.get("days_since_last") or 0),
        },
        "order": {"quantity": int(order.get("quantity") or 0),
                  "total_amount": float(order.get("total_amount") or 0)},
        "channel": row.get("channel") or "jd",
        "days_since_last": int(inv.get("days_since_last") or 0),
        "stock": int(inv.get("available_qty") or 0),
    }
    from core.rules import _check_condition, _resolve_value
    triggered = _check_condition(cond, ctx)
    detail = {}
    try:
        left_raw = cond.get("left", "")
        right_raw = cond.get("right", "")
        detail["left"] = left_raw
        detail["right"] = right_raw
        detail["op"] = cond.get("op", "<")
        detail["left_value"] = _resolve_value(left_raw, ctx)
        if str(right_raw).startswith("max("):
            detail["right_value"] = "max(%s)" % right_raw[4:-1]
        elif str(right_raw).replace(".", "", 1).isdigit():
            detail["right_value"] = float(right_raw)
        elif "." in str(right_raw):
            detail["right_value"] = _resolve_value(right_raw, ctx)
        else:
            detail["right_value"] = right_raw
        detail["warehouse"] = cond.get("warehouse", "")
    except Exception:
        pass
    return ok({"triggered": triggered,
               "alert_title": row.get("alert_title", ""),
               "alert_desc": row.get("alert_desc", ""),
               "detail": detail})

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
    if include_deleted:
        rows = query("SELECT %s FROM rules ORDER BY id ASC" % _FIELDS, [])
    else:
        rows = query("SELECT %s FROM rules WHERE (deleted_at IS NULL OR deleted_at='') ORDER BY id ASC" % _FIELDS, [])
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
    return ok({})


@router.delete("/rules/{rid}")
@traced
def delete_rule(rid: int):
    # 软删除
    execute("UPDATE rules SET deleted_at=NOW(), is_active=0 WHERE id=%s", [rid])
    return ok({})


@router.post("/rules/{rid}/restore")
@traced
def restore_rule(rid: int):
    execute("UPDATE rules SET deleted_at='', is_active=1 WHERE id=%s", [rid])
    return ok({})


@router.post("/rules/{rid}/permanent-delete")
@traced
def permanent_delete_rule(rid: int):
    execute("DELETE FROM rules WHERE id=%s", [rid])
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
        execute("UPDATE rules SET is_active=0 WHERE id IN (%s)" % ph, ids)
    elif action == "delete":
        execute("UPDATE rules SET deleted_at=NOW(), is_active=0 WHERE id IN (%s)" % ph, ids)
    elif action == "restore":
        execute("UPDATE rules SET deleted_at='', is_active=1 WHERE id IN (%s)" % ph, ids)
    elif action == "purge":
        execute("DELETE FROM rules WHERE id IN (%s)" % ph, ids)
    else:
        return fail("未知操作: " + str(action))
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

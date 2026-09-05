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
def list_rules(channel: str = "jd"):
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


@router.get("/rules/{rid}/test")
@traced
def test_rule(rid: int):
    row = one("SELECT * FROM rules WHERE id=%s", [rid])
    if not row:
        return fail("规则不存在")
    try:
        cond = json.loads(row.get("condition_json") or "{}")
    except Exception:
        cond = {}
    return ok({"rule": row.get("name"), "condition": cond, "test": "ok"})

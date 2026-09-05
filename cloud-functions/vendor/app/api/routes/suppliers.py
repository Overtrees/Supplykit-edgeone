from fastapi import APIRouter, Depends
from app.core.database import get_db
from app.core.response import ok, fail
import time

router = APIRouter(prefix="/api/suppliers", tags=["suppliers"])
_suppliers_cache = {}
_SUP_CACHE_TTL = 30

@router.get("")
def list_suppliers(db = get_db(), search: str = "", channel: str = 'jd'):
    try:
        _v = db.table("replenishment_config").select("*").eq("key", "_suppliers_version").execute().data
        _ver = int(_v[0]["value"]) if _v and _v[0].get("value") else 0
    except: _ver = 0
    _key = f"sup_{channel}_{search}_{_ver}"
    _cached = _suppliers_cache.get(_key)
    if _cached and time.time() - _cached['ts'] < 180:
        return _cached['data']
    q = db.table("suppliers").select("*").eq("channel", channel)
    if search:
        like = f"%{search}%"
        q = q.ilike("supplier_name", like).or_(q.ilike("supplier_code", like))
    data = q.order("id", desc=True).execute().data
    # 每品牌单行: 供应商 brand 多值(逗号分隔)拆成多行, 每行一个品牌(便于导入导出/结构化展示)
    expanded = []
    for s in data:
        s = dict(s)
        brands = [b.strip() for b in str(s.get('brand') or '').split('，') if b.strip()]
        if len(brands) <= 1:
            expanded.append(s)
        else:
            for b in brands:
                row = dict(s)
                row['brand'] = b
                row['_key'] = f"{s.get('supplier_code')}|{b}"
                expanded.append(row)
    _suppliers_cache[_key] = {'data': ok(expanded), 'ts': time.time()}
    return ok(expanded)

@router.post("")
def create_supplier(body: dict, db = get_db()):
    _suppliers_cache.clear()
    try:
        from datetime import datetime, timezone
        v = db.table("replenishment_config").select("*").eq("key", "_suppliers_version").execute().data
        nv = (int(v[0]["value"]) + 1) if v and v[0].get("value") else 1
        db.table("replenishment_config").upsert({"key": "_suppliers_version", "value": str(nv), "channel": "jd", "updated_at": datetime.now(timezone.utc).isoformat()}, conflict_col='key')
    except: pass
    data = db.table("suppliers").insert({
        "supplier_code": body.get("supplier_code"),
        "supplier_name": body.get("supplier_name"),
        "contact_person": body.get("contact_person", ""),
        "contact_phone": body.get("contact_phone", ""),
        "score": int(body.get("score", 0)),
        "status": body.get("status", "active"),
        "channel": body.get("channel", 'jd'),
    }).execute().data
    return data[0] if data else {"ok": True}

@router.put("/{sid}")
def update_supplier(sid: int, body: dict, db = get_db()):
    _suppliers_cache.clear()
    try:
        from datetime import datetime, timezone
        v = db.table("replenishment_config").select("*").eq("key", "_suppliers_version").execute().data
        nv = (int(v[0]["value"]) + 1) if v and v[0].get("value") else 1
        db.table("replenishment_config").upsert({"key": "_suppliers_version", "value": str(nv), "channel": "jd", "updated_at": datetime.now(timezone.utc).isoformat()}, conflict_col='key')
    except: pass
    db.table("suppliers").update(body).eq("id", sid).execute()
    return ok({})

@router.delete("/{sid}")
def delete_supplier(sid: int, db = get_db()):
    _suppliers_cache.clear()
    try:
        from datetime import datetime, timezone
        v = db.table("replenishment_config").select("*").eq("key", "_suppliers_version").execute().data
        nv = (int(v[0]["value"]) + 1) if v and v[0].get("value") else 1
        db.table("replenishment_config").upsert({"key": "_suppliers_version", "value": str(nv), "channel": "jd", "updated_at": datetime.now(timezone.utc).isoformat()}, conflict_col='key')
    except: pass
    db.table("suppliers").delete().eq("id", sid).execute()
    return ok({})

"""原生 suppliers / replenishment-config 路由(方案 B)"""
import json

from fastapi import APIRouter
from fastapi import Request

from db import query, one, execute
from routes.common import ok, fail, traced

router = APIRouter(tags=["suppliers"])

_FIELDS = "id, supplier_code, supplier_name, contact_person, contact_phone, score, status, channel, brand"


@router.get("/suppliers")
@traced
def list_suppliers(channel: str = "jd", search: str = ""):
    where = "1=1"
    params = []
    if search:
        where += " AND (supplier_name LIKE %s OR supplier_code LIKE %s)"
        params += ["%%%s%%" % search] * 2
    rows = query("SELECT %s FROM suppliers WHERE %s ORDER BY id ASC" % (_FIELDS, where), params)
    return ok(rows)


@router.post("/suppliers")
@traced
async def create_supplier(request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    code = d.get("supplier_code") or ""
    if not code:
        return fail("缺少 supplier_code")
    ch = d.get("channel", "jd")
    execute("INSERT INTO suppliers(supplier_code, supplier_name, contact_person, contact_phone, score, status, channel, brand) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (code, d.get("supplier_name", ""), d.get("contact_person", ""), d.get("contact_phone", ""),
             int(d.get("score") or 0), d.get("status", "active"), ch, d.get("brand", "")))
    return ok({"id": 0, "supplier_code": code})


@router.put("/suppliers/{sid}")
@traced
async def update_supplier(sid: int, request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    fields = ["supplier_name", "contact_person", "contact_phone", "status", "brand", "score"]
    sets = []
    params = []
    for f in fields:
        if f in d:
            sets.append("`%s` = %%s" % f)
            params.append(d[f])
    if not sets:
        return fail("无更新字段")
    params.append(sid)
    execute("UPDATE suppliers SET %s WHERE id=%%s" % ", ".join(sets), params)
    return ok({})


@router.delete("/suppliers/{sid}")
@traced
def delete_supplier(sid: int):
    execute("DELETE FROM suppliers WHERE id=%s", [sid])
    return ok({})


# ── replenishment-config ───────────────────────────────────────────────

@router.get("/replenishment-config")
@traced
def get_config(channel: str = "jd"):
    rows = query("SELECT `key`, value, channel, updated_at FROM replenishment_config "
                 "WHERE channel=%s OR channel=''", [channel])
    return ok({r.get("key"): r.get("value") for r in rows if r.get("key")})


def _log_cfg_history(channel, key, old_val, new_val, mode=""):
    """配置变更写入 history(规则页'变更历史'弹窗; 对齐 PA——保存时记录)"""
    try:
        if str(old_val) == str(new_val):
            return
        execute("INSERT INTO replenishment_config_history(`key`, old_value, new_value, channel, mode, created_at) "
                "VALUES(%s,%s,%s,%s,%s,NOW())", (key, str(old_val), str(new_val), channel, mode))
    except Exception as _e:
        try:
            execute("INSERT INTO quality_logs(log_type, level, message, details, source) "
                    "VALUES('config_history', 'error', %s, %s, 'config')",
                    ("history 写入失败", str(_e)[:300]))
        except Exception:
            pass


@router.put("/replenishment-config")
@traced
async def update_config(request: Request):
    """配置保存: 带 mode 参数时键加 mode_{mode}_ 前缀存储(对齐 PA——前端加载按 mode 前缀解析,
    平铺存储会被 seed 的 mode 前缀旧值覆盖导致保存不生效); 变更记录 history"""
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    mode = request.query_params.get("mode", "")
    channel = request.query_params.get("channel", "jd") or d.get("channel", "jd")
    data = d.get("data") or d
    n = 0
    for k, v in data.items():
        if k in ("channel", "data"):
            continue
        key = ("mode_%s_" % mode) + k if mode else k
        old = one("SELECT value FROM replenishment_config WHERE `key`=%s AND channel=%s", [key, channel])
        _log_cfg_history(channel, key, (old or {}).get("value", ""), v, mode)
        execute("INSERT INTO replenishment_config(`key`, value, channel) VALUES(%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE value=VALUES(value)", (key, str(v), channel))
        n += 1
    return ok({"updated": n})


@router.get("/replenishment-config/history")
@traced
def config_history(channel: str = "jd", limit: int = 50):
    """配置变更历史(规则页-变更历史弹窗); 列与表结构对齐(old_value/new_value/mode/created_at)"""
    rows = query("SELECT id, `key`, old_value, new_value, channel, mode, created_at "
                 "FROM replenishment_config_history "
                 "WHERE channel=%s OR channel='' ORDER BY id DESC LIMIT %s", [channel, limit])
    return ok(rows)


@router.get("/replenishment-config/slow-cats")
@traced
def get_slow_cats(channel: str = "jd"):
    row = one("SELECT value FROM replenishment_config WHERE `key`='slow_cats' AND channel=%s", [channel])
    try:
        return ok(json.loads((row or {}).get("value") or "[]"))
    except Exception:
        return ok([])


@router.put("/replenishment-config/slow-cats")
@traced
async def put_slow_cats(request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    channel = d.get("channel", "jd")
    items = d.get("items") or []
    old = one("SELECT value FROM replenishment_config WHERE `key`='slow_cats' AND channel=%s", [channel])
    _log_cfg_history(channel, "slow_cats", (old or {}).get("value", ""),
                     json.dumps(items, ensure_ascii=False))
    execute("INSERT INTO replenishment_config(`key`, value, channel) VALUES('slow_cats',%s,%s) "
            "ON DUPLICATE KEY UPDATE value=VALUES(value)",
            (json.dumps(items, ensure_ascii=False), channel))
    return ok({"updated": len(items)})


@router.get("/replenishment-config/seasons")
@traced
def get_seasons(channel: str = "jd", mode: str = "bbcc"):
    row = one("SELECT value FROM replenishment_config WHERE `key`=%s AND channel=%s",
              ("season_config_" + mode, channel))
    try:
        return ok(json.loads((row or {}).get("value") or "[]"))
    except Exception:
        return ok([])


@router.put("/replenishment-config/seasons")
@traced
async def put_seasons(request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    channel = d.get("channel", "jd")
    mode = d.get("mode", "bbcc")
    items = d.get("items") or []
    key = "season_config_%s" % mode
    old = one("SELECT value FROM replenishment_config WHERE `key`=%s AND channel=%s", [key, channel])
    _log_cfg_history(channel, key, (old or {}).get("value", ""),
                     json.dumps(items, ensure_ascii=False), mode)
    execute("INSERT INTO replenishment_config(`key`, value, channel) VALUES(%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE value=VALUES(value)",
            (key, json.dumps(items, ensure_ascii=False), channel))
    return ok({"updated": len(items)})

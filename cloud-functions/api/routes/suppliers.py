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
    from routes.analysis_cache import invalidate_all
    invalidate_all()
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
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok({})


@router.delete("/suppliers/{sid}")
@traced
def delete_supplier(sid: int):
    execute("DELETE FROM suppliers WHERE id=%s", [sid])
    from routes.analysis_cache import invalidate_all
    invalidate_all()
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
    from routes.analysis_cache import invalidate_all
    invalidate_all()
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
    from routes.analysis_cache import invalidate_all
    invalidate_all()  # 滞销参数变更 → 处置建议缓存即时失效
    return ok({"updated": len(items)})


# 内置默认活动系数(空库/未自定义时兜底): 开关默认关闭, 开启后才纳入日销计算
# (补货 bbcc/传统 与 采购建议 三处计算链路均读 season_config_{mode} 并只取 enabled 项)
_DEFAULT_SEASONS = [
    {"key": "618", "name": "618大促", "factor": 1.5, "enabled": False},
    {"key": "1111", "name": "双11大促", "factor": 1.5, "enabled": False},
    {"key": "nianhuo", "name": "年货节", "factor": 1.3, "enabled": False},
]


# ── 订单状态映射(渠道级: 平台状态文案 → 档位; 不区分补货模式, 影响补货/采购/断货销量池) ──
# 档位: sale=计入销量池(归一化为"已完成") / blocked=屏蔽(保留原值, 自然不进销量池)
_DEFAULT_STATUS_MAP = [
    {"name": "已完成", "group": "sale"}, {"name": "交易成功", "group": "sale"},
    {"name": "确认收货", "group": "sale"}, {"name": "已签收", "group": "sale"},
    {"name": "妥投", "group": "sale"}, {"name": "Closed", "group": "sale"}, {"name": "Completed", "group": "sale"},
    {"name": "待发货", "group": "blocked"}, {"name": "已发货", "group": "blocked"},
    {"name": "待确认", "group": "blocked"}, {"name": "待付款", "group": "blocked"},
    {"name": "已取消", "group": "blocked"}, {"name": "已退款", "group": "blocked"},
    {"name": "退款中", "group": "blocked"}, {"name": "申请退款", "group": "blocked"},
    {"name": "已退货", "group": "blocked"}, {"name": "运输中", "group": "blocked"}, {"name": "在途", "group": "blocked"},
]


def _status_map_items(channel):
    """读订单状态映射(自定义优先, 空则内置默认); 返回 {状态名: 档位}"""
    row = one("SELECT value FROM replenishment_config WHERE `key`='order_status_map' AND channel=%s", [channel])
    try:
        stored = json.loads((row or {}).get("value") or "[]")
        if isinstance(stored, list) and stored:
            return {x.get("name"): x.get("group") for x in stored if x.get("name")}
    except Exception:
        pass
    return {x.get("name"): x.get("group") for x in _DEFAULT_STATUS_MAP if x.get("name")}


def _norm_order_status(channel, raw):
    """订单状态归一化(导入写入时): sale → '已完成'(进销量池); blocked/未识别 → 保留原值(不进销量池)"""
    if not raw:
        return raw
    g = _status_map_items(channel).get(str(raw).strip(), "blocked")
    return "已完成" if g == "sale" else raw


@router.get("/replenishment-config/order-status-map")
@traced
def get_status_map(channel: str = "jd"):
    row = one("SELECT value FROM replenishment_config WHERE `key`='order_status_map' AND channel=%s", [channel])
    try:
        stored = json.loads((row or {}).get("value") or "[]")
        if isinstance(stored, list) and stored:
            return ok(stored)
    except Exception:
        pass
    return ok([dict(x) for x in _DEFAULT_STATUS_MAP])


@router.put("/replenishment-config/order-status-map")
@traced
async def put_status_map(request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    channel = d.get("channel", "jd")
    items = d.get("items") or []
    old = one("SELECT value FROM replenishment_config WHERE `key`='order_status_map' AND channel=%s", [channel])
    _log_cfg_history(channel, "order_status_map", (old or {}).get("value", ""),
                     json.dumps(items, ensure_ascii=False))
    execute("INSERT INTO replenishment_config(`key`, value, channel) VALUES('order_status_map',%s,%s) "
            "ON DUPLICATE KEY UPDATE value=VALUES(value)",
            (json.dumps(items, ensure_ascii=False), channel))
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok({"updated": len(items)})


@router.get("/replenishment-config/seasons")
@traced
def get_seasons(channel: str = "jd", mode: str = "bbcc"):
    row = one("SELECT value FROM replenishment_config WHERE `key`=%s AND channel=%s",
              ("season_config_" + mode, channel))
    try:
        stored = json.loads((row or {}).get("value") or "[]")
        if isinstance(stored, list) and stored:
            return ok(stored)  # 自定义优先: 用户保存的列表为权威
    except Exception:
        pass
    # 无自定义存储 → 内置默认兜底
    return ok([dict(s) for s in _DEFAULT_SEASONS])


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
    from routes.analysis_cache import invalidate_all
    invalidate_all()  # 季节参数变更 → 补货/采购缓存即时失效
    return ok({"updated": len(items)})

"""渠道注册表路由(2026-10-30 P0 可扩展性)

渠道从"代码常量"→"数据驱动": 新增渠道只需登记一行, 不再改代码发版。
- GET    /channels            列表(前端下拉/渠道名/排序都读它)
- POST   /channels            新建(code 唯一; 仅登记, 数据由导入填充)
- PUT    /channels/{code}     更新(name/enabled/supports_b/default_mode/sort)
- DELETE /channels/{code}     禁用(不物理删 —— 存量数据仍带该 channel, 删了会成孤儿)

supports_b  该渠道是否走 BBCC 体系(B 仓调拨链)   —— 原 `channel == "jd"`
default_mode 该渠道默认补货模式(bbcc/traditional) —— 原 `"bbcc" if channel=="jd" else "traditional"`
"""
import re

from fastapi import APIRouter, Request

from db import query, execute, one
from routes.common import ok, fail, traced, try_err, ch_list

router = APIRouter(tags=["channels"])

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,19}$")


@router.get("/channels")
@traced
def list_channels(enabled_only: int = 0):
    """渠道列表(默认含已禁用, 便于管理页展示)"""
    rows = query("SELECT code, name, enabled, supports_b, default_mode, sort, created_at "
                 "FROM channels ORDER BY sort, code") or []
    items = [{"code": r.get("code"), "name": r.get("name") or r.get("code"),
              "enabled": int(r.get("enabled") or 0),
              "supports_b": bool(r.get("supports_b")),
              "default_mode": r.get("default_mode") or "traditional",
              "sort": int(r.get("sort") or 0),
              "created_at": str(r.get("created_at") or "")[:19]} for r in rows]
    if enabled_only:
        items = [x for x in items if x["enabled"]]
    return ok(items)


@router.post("/channels")
@traced
async def create_channel(request: Request):
    """新建渠道(幂等: 已存在则报错, 避免误覆盖配置)"""
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
        try_err('channels', '请求体解析降级', _e)
    code = str((d or {}).get("code") or "").strip().lower()
    if not _CODE_RE.match(code):
        return fail("code 需为小写字母开头的 1-20 位小写字母/数字/下划线")
    if one("SELECT 1 FROM channels WHERE code=%s", [code]):
        return fail("渠道 %s 已存在" % code)
    name = str((d or {}).get("name") or code)[:64]
    supports_b = 1 if (d or {}).get("supports_b") else 0
    dm = str((d or {}).get("default_mode") or ("bbcc" if supports_b else "traditional"))
    if dm not in ("bbcc", "traditional"):
        dm = "traditional"
    try:
        sort = int((d or {}).get("sort") or 99)
    except Exception:
        sort = 99
    execute("INSERT INTO channels(code, name, enabled, supports_b, default_mode, sort) "
            "VALUES(%s,%s,1,%s,%s,%s)", [code, name, supports_b, dm, sort])
    # 2026-10-30 P3 渠道模板: copy_from=已有渠道 → 复制其 rules + replenishment_config,
    #   新渠道开箱即有完整规则与参数(否则要手工配 20+ 项)
    copied = {}
    _src = str((d or {}).get("copy_from") or "").strip()
    if _src and _src != code and one("SELECT 1 FROM channels WHERE code=%s", [_src]):
        try:
            _nr = execute("INSERT INTO rules(name, event, condition_json, alert_type, alert_title, "
                          "alert_desc, severity, mode, params, channel) "
                          "SELECT name, event, condition_json, alert_type, alert_title, alert_desc, "
                          "severity, mode, params, %s FROM rules WHERE channel=%s "
                          "AND (deleted_at IS NULL OR deleted_at='')", [code, _src])
            _nc = execute("INSERT IGNORE INTO replenishment_config(`key`, channel, value) "
                          "SELECT `key`, %s, value FROM replenishment_config WHERE channel=%s",
                          [code, _src])
            copied = {"rules": int(_nr or 0), "config": int(_nc or 0), "from": _src}
        except Exception as _ec:
            try_err('channels', '渠道模板复制降级(%s→%s)' % (_src, code), _ec)
            copied = {"error": str(_ec)[:120]}
    ch_list(force=True)
    return ok({"code": code, "name": name, "supports_b": bool(supports_b),
               "default_mode": dm, "sort": sort, "copied": copied})


@router.put("/channels/{code}")
@traced
async def update_channel(code: str, request: Request):
    """更新渠道配置(只改传入字段)"""
    row = one("SELECT code FROM channels WHERE code=%s", [code])
    if not row:
        return fail("渠道不存在: %s" % code)
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
        try_err('channels', '请求体解析降级', _e)
    sets, params = [], []
    if "name" in (d or {}):
        sets.append("name=%s")
        params.append(str(d.get("name") or code)[:64])
    if "enabled" in (d or {}):
        sets.append("enabled=%s")
        params.append(1 if d.get("enabled") else 0)
    if "supports_b" in (d or {}):
        sets.append("supports_b=%s")
        params.append(1 if d.get("supports_b") else 0)
    if "default_mode" in (d or {}):
        dm = str(d.get("default_mode") or "")
        if dm not in ("bbcc", "traditional"):
            return fail("default_mode 只能是 bbcc 或 traditional")
        sets.append("default_mode=%s")
        params.append(dm)
    if "sort" in (d or {}):
        try:
            sets.append("sort=%s")
            params.append(int(d.get("sort") or 99))
        except Exception:
            pass
    if not sets:
        return fail("无更新字段")
    execute("UPDATE channels SET " + ", ".join(sets) + " WHERE code=%s", params + [code])
    ch_list(force=True)
    return ok({"code": code, "updated": len(sets)})


@router.delete("/channels/{code}")
@traced
def disable_channel(code: str):
    """禁用渠道(不物理删: 存量 inventory/orders/rules 仍带该 channel, 删会成孤儿数据)"""
    if not one("SELECT 1 FROM channels WHERE code=%s", [code]):
        return fail("渠道不存在: %s" % code)
    execute("UPDATE channels SET enabled=0 WHERE code=%s", [code])
    ch_list(force=True)
    return ok({"code": code, "enabled": 0})

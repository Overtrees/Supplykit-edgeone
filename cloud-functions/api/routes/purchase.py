"""原生采购/处置路由(方案 B): purchase-orders CRUD + insights/purchase + 滞销处置建议"""

import json
import time as _time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi import Request

from db import query, one, execute, executemany
from routes.common import ok, fail, traced, try_err
router = APIRouter(tags=["purchase"])

from routes.analysis_cache import register as _register_cache, cache_get as _cache_get
from routes.purchase_calc import _build_purchase, disposal_suggestions
_register_cache(lambda: _purchase_cache.clear())


# ── 已下单标记(purchase-orders) ─────────────────────────────────────────
@router.get("/purchase-orders")
@traced
def list_purchase_orders(channel: str = "jd"):
    rows = query("SELECT id, sku, store, product_name, suggested_qty, actual_qty, arrival_date, status, channel "
                 "FROM purchase_orders WHERE channel=%s ORDER BY id ASC", [channel])
    return ok(rows)


@router.post("/purchase-orders")
@traced
async def create_purchase_order(request: Request):
    q = request.query_params
    sku = q.get("sku", "")
    store = q.get("store", "")
    channel = q.get("channel", "jd")
    if not sku:
        return fail("缺少 sku")
    product_name = q.get("product_name", "")
    suggested_qty = int(float(q.get("suggested_qty") or 0))
    execute("INSERT INTO purchase_orders(sku, store, product_name, suggested_qty, channel) "
            "VALUES(%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE product_name=VALUES(product_name), suggested_qty=VALUES(suggested_qty)",
            (sku, store, product_name, suggested_qty, channel))
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok({})


@router.delete("/purchase-orders")
@traced
async def delete_purchase_order(request: Request):
    q = request.query_params
    sku = q.get("sku", "")
    store = q.get("store", "")
    channel = q.get("channel", "jd")
    execute("DELETE FROM purchase_orders WHERE sku=%s AND store=%s AND channel=%s",
            (sku, store, channel))
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok({})


@router.put("/purchase-orders/{pid}")
@traced
async def update_purchase_order(pid: int, request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('purchase', '静默降级', _e)
    sets, params = [], []
    for f in ("arrival_date", "actual_qty", "status"):
        if f in d and d[f] is not None:
            sets.append("`%s` = %%s" % f)
            params.append(d[f])
    if not sets:
        return fail("无更新字段")
    params.append(pid)
    execute("UPDATE purchase_orders SET %s WHERE id=%%s" % ", ".join(sets), params)
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok({})


# ── 采购建议(insights/purchase) ─────────────────────────────────────────
_purchase_cache = {}
_PURCHASE_TTL = 300


@router.get("/insights/purchase")
@traced
def purchase_suggestions(days: int = 28, mode: str = "bbcc", channel: str = "jd",
                         search: str = "", need_only: int = 0):
    """采购建议(300s 共享表缓存——TiDB 表跨实例一致, 搜索/need_only 在缓存后过滤——降 RU): 系统总库存+供应商级参数+目标周转+采购告警"""
    _key = "purchase|%s|%s|%s" % (channel, mode, days)
    _all = _cache_get(_key, _PURCHASE_TTL, lambda: _build_purchase(channel, mode, days))
    if need_only:
        _all = [r for r in _all if (r.get("actual_purchase") or 0) > 0]
    if search:
        _sq = search.lower()
        _all = [r for r in _all if _sq in str(r.get("sku", "")).lower()
                or _sq in str(r.get("product_name", "")).lower()
                or _sq in str(r.get("barcode", "")).lower()]
    return ok({"suggestions": _all})




# ── 滞销处置建议(disposal-suggestions) ──────────────────────────────────
@router.get("/insights/disposal-suggestions")
@traced


# ── 批量处置 ─────────────────────────────────────────────────────────────
@router.post("/disposals/batch")
@traced
async def disposals_batch(request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('purchase', '静默降级', _e)
    channel = d.get("channel", "jd")
    action = d.get("action", "mark")
    note = d.get("note", "")
    items = d.get("items") or []
    n = 0
    for it in items:
        sku = it.get("sku")
        if not sku:
            continue
        execute("INSERT INTO disposal_records(sku, warehouse, warehouse_type, channel, level, turnover_days, reason, action, note) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (sku, it.get("warehouse", ""), it.get("warehouse_type", ""), channel,
                 it.get("level", ""), float(it.get("turnover_days") or 0),
                 it.get("reason") or "", action, note))
        n += 1
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok({"updated": n})
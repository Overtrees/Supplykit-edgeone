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
from routes.purchase_calc import _build_purchase
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
    from routes.analysis_cache import invalidate
    invalidate("purchases")
    # 2026-10-26 根治: 返回真实新行(含 id)——前端乐观项用后端行替换, 消除"无 id 项 → 日期更新 PUT /undefined 422"
    row = one("SELECT id, sku, store, product_name, suggested_qty, actual_qty, arrival_date, status, channel "
              "FROM purchase_orders WHERE sku=%s AND store=%s AND channel=%s",
              [sku, store, channel])
    return ok(row)


@router.delete("/purchase-orders")
@traced
async def delete_purchase_order(request: Request):
    q = request.query_params
    sku = q.get("sku", "")
    store = q.get("store", "")
    channel = q.get("channel", "jd")
    execute("DELETE FROM purchase_orders WHERE sku=%s AND store=%s AND channel=%s",
            (sku, store, channel))
    from routes.analysis_cache import invalidate
    invalidate("purchases")
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
    from routes.analysis_cache import invalidate
    invalidate("purchases")
    return ok({})


# ── 采购建议(insights/purchase) ─────────────────────────────────────────
_purchase_cache = {}
_PURCHASE_TTL = 300


@router.get("/insights/purchase")
@traced
def purchase_suggestions(days: int = 28, mode: str = "bbcc", channel: str = "jd",
                         search: str = "", need_only: int = 0,
                         page: int = 0, page_size: int = 0):
    """采购建议(300s 共享表缓存): 系统总库存+供应商级参数+目标周转+采购告警

    2026-11-06 分页(治队头阻塞): 原来一次返回 9,684 条 / 5.93MB(TiDB 单条上限 6MB → 缓存
    永远写不进去 → 每请求全量重算 → 同实例其它请求排队)。

    **缓存粒度 = 一页一条**(v2, 见 core/list_page.py 的演进说明):
      第一版把全量存进缓存、只切响应 —— 出网字节降了 140 倍, 但 `cache_put` 存的仍是
      5.93MB, 8025 照旧, 耗时纹丝不动。现在缓存键带 `need_only|search|page|page_size`,
      每条只是当前页(size×≈800B), 真正能落库。
    · 不传 page/page_size 时行为与旧版完全一致(导出/内部调用方零改动)
    · total 是**收窄后**的总数, 前端"已加载 N/total"与触底加载不受截断影响
    """
    from core.list_page import shape_cached as _shape_cached
    _base = "purchase|%s|%s|%s" % (channel, mode, days)
    _d = _shape_cached(_base,
                       lambda: _build_purchase(channel, mode, days),
                       _cache_get, _PURCHASE_TTL,
                       need_only=need_only, search=search,
                       page=page, page_size=page_size)
    return ok(_d)




# ── 滞销处置建议(disposal-suggestions) ──────────────────────────────────
@router.get("/insights/disposal-suggestions")
@traced
def disposal_suggestions(channel: str = "jd", page: int = 0, page_size: int = 0,
                         search: str = "", debug: int = 0):
    """滞销处置建议(2026-10-15 修复: 拆分时函数体丢失——GET 误挂 disposals_batch 返 updated:0——滞销 tab 无数据)"""
    from routes.purchase_calc import disposal_suggestions as _ds
    return _ds(channel, page, page_size, search, debug)


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
    from routes.analysis_cache import invalidate
    invalidate("purchases")
    return ok({"updated": n})
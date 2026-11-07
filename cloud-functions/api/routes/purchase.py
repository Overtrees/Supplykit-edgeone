"""原生采购/处置路由(方案 B): purchase-orders CRUD + insights/purchase + 滞销处置建议"""

import json
import time as _time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi import Request

from db import query, one, execute, executemany
from routes.common import ok, fail, traced, try_err
router = APIRouter(tags=["purchase"])

from routes.purchase_calc import _build_purchase
# 2026-11-07 P0: 采购建议改走 core/list_store 域指纹物化 —— 旧的 300s 表缓存注册已删除。


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
@router.get("/insights/purchase")
@traced
def purchase_suggestions(days: int = 28, mode: str = "bbcc", channel: str = "jd",
                         search: str = "", need_only: int = 0,
                         page: int = 0, page_size: int = 0,
                         legacy: int = 0, state: int = 0, force: int = 0):
    """采购建议(域指纹物化 + 后台重建, 见 core/list_store.py)

    2026-11-07 P0 根治「读时全量现算 + TTL 300s 表缓存」:
      旧路径 TTL 到点(无写操作)或 invalidate 打墓碑, 就由下一个请求在请求路径
      同步跑 `_build_purchase`(9,684 条 / 5.93MB) → 线上首次 5.015s。
    现在请求路径只读 list_snapshot 按 seq 的一页 + head 计数, 重建在后台由域计数
      fp 驱动(无 TTL), 发布前核对行数/need 数; fp 不命中才退回现算(慢但正确)。

    形状契约不变: 始终返回 {suggestions,total,page,page_size}; 无分页裸调返回全量
    (导出/内部调用方零改动)。`_build_purchase` 算法一个字没动。
    `legacy=1` 强制现算(线上对账用), `state=1` 返回物化四维诊断,
    `force=1` 同步重建并回四维状态(部署后预热/对账用, 属诊断口非常态读路径)。
    """
    from core import list_store as _LS
    _base = "purchase|%s|%s|%s" % (channel, mode, days)
    if state or force:
        if force:
            _LS.register_scope("purchase", _base, channel, mode, days, "")
            _rb = _LS.build("purchase", _base, force=True)
            return ok({"rebuild": _rb, "state": _LS.status("purchase", _base)})
        return ok(_LS.status("purchase", _base))
    _d = _LS.serve("purchase", _base, channel, mode, days, "",
                   lambda: _build_purchase(channel, mode, days),
                   need_only=need_only, search=search,
                   page=page, page_size=page_size, legacy=legacy)
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
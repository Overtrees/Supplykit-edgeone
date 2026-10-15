"""原生 orders 路由(方案 B): 分页+搜索+状态筛选+单条软删/恢复/永久删除(契约与旧 backend 一致)"""
from fastapi import APIRouter, Request

def _evaluate_restored(ids):
    """restore 联动(2026-10-15): 恢复订单→order.created 规则重评估(超卖/低库存——恢复后重新生成告警——对称于删单联动关)"""
    if not ids:
        return
    try:
        from core.rules import evaluate_many, load_rules_for
        _ph = ",".join(["%s"] * len(ids))
        rows = query("SELECT sku, SUM(quantity) AS q FROM orders WHERE id IN (%s) "
                     "AND (deleted_at IS NULL OR deleted_at='') GROUP BY sku" % _ph, ids)
        if not rows:
            return
        _skus = [r.get("sku") for r in rows if r.get("sku")]
        _qty = {r.get("sku"): r.get("q") or 0 for r in rows}
        _inv_map = {}
        for _i in range(0, len(_skus), 200):
            _b = _skus[_i:_i + 200]
            _ph2 = ",".join(["%s"] * len(_b))
            for r2 in query("SELECT sku, MAX(available_qty) AS avail FROM inventory "
                            "WHERE channel=%s AND sku IN (%s) GROUP BY sku" % ("%s", _ph2),
                            [channel] + _b):
                _inv_map[r2.get("sku")] = int(r2.get("avail") or 0)
        _o_ctxs = [{"sku": sk, "channel": channel,
                    "order": {"quantity": _qty.get(sk, 0)}, "order_qty": _qty.get(sk, 0),
                    "available_stock": _inv_map.get(sk, 0),
                    "inv": {"available_qty": _inv_map.get(sk, 0), "safety_qty": 0, "in_transit_qty": 0}}
                   for sk in _skus]
        if _o_ctxs:
            evaluate_many("order.created", _o_ctxs, channel, load_rules_for("order.created", channel))
    except Exception as _e:
        try_err('orders', 'restore 联动评估降级', _e)


def _skus_of(ids):
    if not ids:
        return []
    _ph = ",".join(["%s"] * len(ids))
    try:
        rows = query("SELECT DISTINCT sku FROM orders WHERE id IN (%s)" % _ph, ids)
        return [r.get("sku") for r in rows if r.get("sku")]
    except Exception:
        return []


def _close_orphan(skus):
    """删单联动(2026-10-15): SKU 级孤儿关闭(oversell + low_stock——核心在 core/rules.close_oversell_for_skus)"""
    try:
        from core.rules import close_oversell_for_skus
        close_oversell_for_skus(skus)
    except Exception as _e:
        try_err('orders', '删单联动 oversell 关闭降级', _e)




from db import query, one, execute
from routes.common import ok, fail, traced, try_err

router = APIRouter(tags=["orders"])

_FIELDS = "id, order_no, sku, barcode, product_name, store, warehouse, quantity, unit_price, " \
          "total_amount, order_status, ordered_at, paid_at, platform, channel, deleted_at, ext_json"


@router.get("/orders")
@traced
def list_orders(channel: str = "jd", page: int = 1, page_size: int = 30,
                search: str = "", status: str = "", include_deleted: int = 0):
    if include_deleted:
        where = "channel=%s"
    else:
        where = "channel=%s AND (deleted_at IS NULL OR deleted_at='')"
    params = [channel]
    if search:
        where += " AND (sku LIKE %s OR product_name LIKE %s OR order_no LIKE %s OR barcode LIKE %s)"
        params += ["%%%s%%" % search] * 4
    if status:
        where += " AND order_status=%s"
        params.append(status)
    total = one("SELECT COUNT(*) AS c FROM orders WHERE %s" % where, params) or {}
    total = int(total.get("c") or 0)
    if page > 0 and page_size > 0:
        rows = query("SELECT %s FROM orders WHERE %s ORDER BY id DESC LIMIT %s OFFSET %s"
                     % (_FIELDS, where, page_size, (page - 1) * page_size), params)
        return ok({"items": rows, "total": total, "page": page, "page_size": page_size})
    rows = query("SELECT %s FROM orders WHERE %s ORDER BY id DESC" % (_FIELDS, where), params)
    return ok(rows)


@router.delete("/orders/{oid}")
def soft_delete_order(oid: int):
    """软删除(前端 5s 撤销窗口, 回收站可恢复)"""
    _skus = _skus_of([oid])
    execute("UPDATE orders SET deleted_at=NOW() WHERE id=%s", [oid])
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    _close_orphan(_skus)
    return ok({})


@router.post("/orders/batch")
@traced
async def orders_batch(request: Request):
    """批量操作: {action: delete|restore|permanent-delete, ids: []}"""
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('orders', '静默降级', _e)
    action = d.get("action") or ""
    ids = [x for x in (d.get("ids") or []) if str(x).isdigit()]
    if action not in ("delete", "restore", "permanent-delete") or not ids:
        return fail("参数无效: 需 action 与 ids")
    _del_skus = _skus_of(ids) if action in ("delete", "permanent-delete") else []
    # 分批(5000/批)——防大批量单 SQL IN 过大/超时(10万级也稳定)
    for _i in range(0, len(ids), 5000):
        _batch = ids[_i:_i + 5000]
        _ph = ",".join(["%s"] * len(_batch))
        if action == "delete":
            execute("UPDATE orders SET deleted_at=NOW() WHERE id IN (%s)" % _ph, _batch)
        elif action == "restore":
            execute("UPDATE orders SET deleted_at='' WHERE id IN (%s)" % _ph, _batch)
        else:
            execute("DELETE FROM orders WHERE id IN (%s)" % _ph, _batch)
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    if action == "restore":
        _evaluate_restored(ids)
    elif _del_skus:
        _close_orphan(_del_skus)
    return ok({"updated": len(ids)})


@router.post("/orders/{oid}/restore")
@traced
def restore_order(oid: int):
    execute("UPDATE orders SET deleted_at='' WHERE id=%s", [oid])
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    _evaluate_restored([oid])
    return ok({})


@router.post("/orders/{oid}/permanent-delete")
@traced
def permanent_delete_order(oid: int):
    _skus = _skus_of([oid])
    execute("DELETE FROM orders WHERE id=%s", [oid])
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    _close_orphan(_skus)
    return ok({})

"""原生 orders 路由(方案 B): 分页+搜索+状态筛选(契约与旧 backend 一致)"""
from fastapi import APIRouter

from db import query, one
from routes.common import ok, traced

router = APIRouter(tags=["orders"])

_FIELDS = "id, order_no, sku, barcode, product_name, store, warehouse, quantity, unit_price, " \
          "total_amount, order_status, ordered_at, paid_at, platform, channel, deleted_at"


@router.get("/orders")
@traced
def list_orders(channel: str = "jd", page: int = 1, page_size: int = 30,
                search: str = "", status: str = ""):
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

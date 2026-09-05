"""原生 products 路由(方案 B): 分页+搜索(契约与旧 backend 一致)"""
from fastapi import APIRouter

from db import query, one
from routes.common import ok, traced

router = APIRouter(tags=["products"])

_FIELDS = "id, sku, barcode, product_name, brand, store, category, price, box_qty, unit, status, channel"


@router.get("/products")
@traced
def list_products(channel: str = "jd", page: int = 1, page_size: int = 30,
                  search: str = ""):
    where = "channel=%s AND (deleted_at IS NULL OR deleted_at='')"
    params = [channel]
    if search:
        where += " AND (sku LIKE %s OR product_name LIKE %s OR barcode LIKE %s)"
        params += ["%%%s%%" % search] * 3
    total = one("SELECT COUNT(*) AS c FROM products WHERE %s" % where, params) or {}
    total = int(total.get("c") or 0)
    if page > 0 and page_size > 0:
        rows = query("SELECT %s FROM products WHERE %s ORDER BY id ASC LIMIT %s OFFSET %s"
                     % (_FIELDS, where, page_size, (page - 1) * page_size), params)
        return ok({"items": rows, "total": total, "page": page, "page_size": page_size})
    rows = query("SELECT %s FROM products WHERE %s ORDER BY id ASC" % (_FIELDS, where), params)
    return ok(rows)

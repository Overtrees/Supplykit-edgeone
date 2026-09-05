"""原生 products 路由(方案 B): 分页+搜索+批量操作(契约与旧 backend 一致)"""
from fastapi import APIRouter
from fastapi import Request

from db import query, one, execute
from routes.common import ok, fail, traced

router = APIRouter(tags=["products"])

_FIELDS = "id, sku, barcode, product_name, brand, store, category, price, box_qty, unit, status, channel, weight, volume, best_before, deleted_at"


@router.get("/products")
@traced
def list_products(channel: str = "jd", page: int = 1, page_size: int = 30,
                  search: str = "", include_deleted: int = 0):
    if include_deleted:
        where = "channel=%s"
    else:
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


@router.post("/products/batch")
@traced
async def products_batch(request: Request):
    """批量操作: {action: active|inactive|delete|restore, ids: []}"""
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    action = d.get("action", "")
    ids = d.get("ids") or []
    if not ids:
        return fail("缺少 ids")
    ph = ",".join(["%s"] * len(ids))
    if action == "active":
        execute("UPDATE products SET status='active' WHERE id IN (%s)" % ph, ids)
    elif action == "inactive":
        execute("UPDATE products SET status='inactive' WHERE id IN (%s)" % ph, ids)
    elif action == "delete":
        execute("UPDATE products SET deleted_at=NOW() WHERE id IN (%s)" % ph, ids)
    elif action == "restore":
        execute("UPDATE products SET deleted_at='', status='active' WHERE id IN (%s)" % ph, ids)
    else:
        return fail("未知操作: " + str(action))
    return ok({"updated": len(ids)})

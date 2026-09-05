"""原生 batches 路由(方案 B): 库存批次明细(进销存行展开用)"""
from fastapi import APIRouter

from db import query
from routes.common import ok, traced

router = APIRouter(tags=["batches"])


@router.get("/batches")
@traced
def list_batches(channel: str = "jd", sku: str = "", warehouse: str = "",
                 warehouse_type: str = ""):
    where = "channel=%s"
    params = [channel]
    if sku:
        where += " AND sku=%s"
        params.append(sku)
    if warehouse:
        where += " AND warehouse=%s"
        params.append(warehouse)
    if warehouse_type:
        where += " AND warehouse_type=%s"
        params.append(warehouse_type)
    rows = query("SELECT id, sku, warehouse, warehouse_type, prod_date, exp_date, qty "
                 "FROM batches WHERE %s ORDER BY exp_date ASC, id ASC" % where, params)
    return ok(rows)

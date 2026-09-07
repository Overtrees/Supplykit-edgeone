"""原生 inventory 路由(方案 B): 缺货清单 + 单条删除(契约与旧 backend 一致)"""
from fastapi import APIRouter

from db import query, execute
from routes.common import ok, traced

router = APIRouter(tags=["inventory"])


@router.get("/inventory/out-of-stock")
@traced
def out_of_stock(channel: str = "jd", wh: str = "own"):
    """缺货清单(维度与看板健康卡一致): wh=own/platform/platform_b/bc
    bc = platform + platform_b 按 SKU 合计 <=0(与 dashboard aux bcOutOfStock 同口径)"""
    if wh == "bc":
        # bc = B仓+全国C仓按 SKU 合计(bbcc 一盘棋口径); warehouse 列给实际缺货仓集合
        # (前端弹窗/预览按数据 warehouse 列显示实际仓名, 非硬编码 "BC")
        rows = query(
            "SELECT sku, MAX(product_name) AS product_name, 'bc' AS warehouse_type, "
            "SUM(available_qty) AS available_qty, "
            "GROUP_CONCAT(DISTINCT IF(available_qty<=0, warehouse, NULL) "
            "ORDER BY warehouse SEPARATOR ',') AS warehouse "
            "FROM inventory "
            "WHERE channel=%s AND warehouse_type IN ('platform','platform_b') "
            "GROUP BY sku HAVING SUM(available_qty) <= 0 ORDER BY sku", [channel])
        for _r in rows:
            _r["warehouse"] = str(_r.get("warehouse") or "") or "BC"
    else:
        rows = query(
            "SELECT sku, product_name, warehouse, warehouse_type, available_qty "
            "FROM inventory WHERE channel=%s AND warehouse_type=%s AND available_qty<=0 "
            "ORDER BY id DESC", [channel, wh])
    return ok(rows)


@router.delete("/inventory/{iid}")
@traced
def delete_inventory(iid: int):
    execute("DELETE FROM inventory WHERE id=%s", [iid])
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok({})

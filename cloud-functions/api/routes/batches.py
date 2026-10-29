"""原生 batches 路由(方案 B): 库存批次明细(进销存行展开用)"""
from fastapi import APIRouter

from db import query
from routes.common import ok, traced, try_err

router = APIRouter(tags=["batches"])


@router.get("/batches")
@traced
def list_batches(channel: str = "jd", sku: str = "", warehouse: str = "",
                 warehouse_type: str = "", limit: int = 0):
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
    # 2026-10-29 保护(实测: 无过滤裸调 17109 行 → 序列化 3.05s, 且逼近 EdgeOne 网关体积上限):
    #   前端只在行展开时按 sku+仓+仓型查询(单 SKU 26ms), 裸调属越界用法 → 无 sku 时默认截断 2000
    #   limit 显式传入可覆盖(上限 5000); 需要全量请按 sku 分批
    try:
        _lim = int(limit or 0)
    except Exception as _e:
        try_err('batches', 'limit 解析降级', _e)
        _lim = 0
    if _lim <= 0:
        _lim = 0 if sku else 2000
    # 2026-10-29 按码: 批次唯一键本就含 69码(uq 四维), 展示层必须带出
    _sql = ("SELECT id, sku, COALESCE(barcode,'') AS barcode, warehouse, warehouse_type, "
            "prod_date, exp_date, qty FROM batches WHERE %s ORDER BY exp_date ASC, id ASC" % where)
    _p = list(params)
    if _lim > 0:
        _sql += " LIMIT %s"
        _p.append(min(_lim, 5000))
    rows = query(_sql, _p)
    # TiDB DATETIME 读出 datetime 对象 → 格式化为 YYYY-MM-DD(修复前端显示 T00:00:00)
    for r in rows:
        r["prod_date"] = str(r.get("prod_date") or "")[:10]
        r["exp_date"] = str(r.get("exp_date") or "")[:10]
    return ok(rows)

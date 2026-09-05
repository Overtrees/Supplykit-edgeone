"""批次效期管理 — 多批次明细查询（进销存页展开用）"""
from fastapi import APIRouter
from app.core.database import get_db
from app.core.response import ok

router = APIRouter(prefix="/api/batches", tags=["batches"])


@router.get("")
def get_batches(sku: str = '', warehouse: str = '', warehouse_type: str = '', channel: str = 'jd', limit: int = 50, db = get_db()):
    """按 SKU×仓库×主体 查询批次明细（按截止日升序，最早/最危险排最前）

    warehouse_type: 主体隔离(own/platform/platform_b)——进销存各维度只显示本主体批次,
    避免展开态混入其他主体(如自有仓展开看到平台/B仓批次)
    """
    from app.core.database import get_conn
    conn = get_conn()
    q = "SELECT sku, warehouse, warehouse_type, channel, prod_date, exp_date, qty, created_at FROM batches WHERE channel=?"
    params = [channel]
    if sku:
        q += " AND sku=?"
        params.append(sku)
    if warehouse:
        q += " AND warehouse=?"
        params.append(warehouse)
    if warehouse_type:
        q += " AND warehouse_type=?"
        params.append(warehouse_type)
    q += " ORDER BY exp_date ASC, prod_date ASC LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(q, params).fetchall()
        # 批量加载出入库统计（按 SKU+仓库+批次聚合）
        _in = {}
        _out = {}
        try:
            for _r in conn.execute("SELECT sku, warehouse, prod_date, exp_date, SUM(quantity) FROM inbound_records WHERE channel=? AND prod_date!='' AND exp_date!='' GROUP BY sku, warehouse, prod_date, exp_date", (channel,)).fetchall():
                _in[(str(_r[0]), str(_r[1] or ''), str(_r[2] or '')[:10], str(_r[3] or '')[:10])] = int(_r[4] or 0)
            for _r in conn.execute("SELECT sku, warehouse, prod_date, exp_date, SUM(quantity) FROM outbound_records WHERE channel=? AND prod_date!='' AND exp_date!='' GROUP BY sku, warehouse, prod_date, exp_date", (channel,)).fetchall():
                _out[(str(_r[0]), str(_r[1] or ''), str(_r[2] or '')[:10], str(_r[3] or '')[:10])] = int(_r[4] or 0)
        except Exception:
            pass
        items = []
        for r in rows:
            _key = (str(r[0]), str(r[1] or ''), str(r[4] or '')[:10], str(r[5] or '')[:10])
            _is_own = str(r[2] or '') == 'own' if len(r) > 2 else False
            _inq = _in.get(_key, 0) if _is_own else 0
            _outq = _out.get(_key, 0) if _is_own else 0
            items.append({"sku": str(r[0]), "warehouse": str(r[1] or ''), "warehouse_type": str(r[2] or ''),
                          "channel": str(r[3] or 'jd'), "prod_date": str(r[4] or '')[:10],
                          "exp_date": str(r[5] or '')[:10], "qty": int(r[6] or 0),
                          "inbound_qty": _inq, "outbound_qty": _outq,
                          "created_at": str(r[7] or '')[:19]})
        return ok(items)
    except Exception as e:
        import logging; logging.warning(f"[batches] query: {e}")
        return ok([])
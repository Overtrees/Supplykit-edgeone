"""处置记录 — 滞销处置闭环（批量标记已处置，避免重复建议）"""
from fastapi import APIRouter
from app.core.database import get_db
from app.core.response import ok

router = APIRouter(prefix="/api/disposals", tags=["disposals"])


@router.post("/batch")
def batch_dispose(body: dict, db = get_db()):
    """批量标记处置: {channel, action: 'clearance'|'return'|'promo'|'observe', note, items: [{sku, warehouse, warehouse_type, level, turnover_days, reason}]}"""
    channel = body.get("channel", "jd")
    action = body.get("action", "clearance")
    note = (body.get("note") or "")[:200]
    items = body.get("items") or []
    if not items:
        return ok({"recorded": 0})
    count = 0
    try:
        from app.core.database import get_conn
        conn = get_conn()
        for it in items:
            sku = it.get("sku", "")
            wh = it.get("warehouse", "")
            if not sku:
                continue
            conn.execute(
                "INSERT INTO disposal_records(sku, warehouse, warehouse_type, channel, level, turnover_days, reason, action, note) VALUES(?,?,?,?,?,?,?,?,?)",
                (sku, wh, it.get("warehouse_type", ""), channel, it.get("level", ""),
                 float(it.get("turnover_days") or 0),
                 (it.get("reason") or ""),  # list 转文本
                 action, note)
            )
            count += 1
        conn.commit()
    except Exception as e:
        import logging; logging.warning(f"[disposals] batch: {e}")
        from app.core.response import fail
        return fail(str(e))
    return ok({"recorded": count})


@router.get("")
def list_disposals(channel: str = 'jd', limit: int = 100, db = get_db()):
    """处置记录列表"""
    try:
        from app.core.database import get_conn
        conn = get_conn()
        rows = conn.execute(
            "SELECT sku, warehouse, warehouse_type, level, turnover_days, reason, action, note, created_at FROM disposal_records "
            "WHERE channel=? ORDER BY id DESC LIMIT ?", (channel, limit)).fetchall()
        items = [dict(zip(['sku','warehouse','warehouse_type','level','turnover_days','reason','action','note','created_at'], r)) for r in rows]
        return ok(items)
    except Exception as e:
        from app.core.response import fail
        return fail(str(e))


@router.delete("/{rid}")
def delete_disposal(rid: int, db = get_db()):
    """删除一条处置记录（误标记可撤销）"""
    try:
        from app.core.database import get_conn
        conn = get_conn()
        conn.execute("DELETE FROM disposal_records WHERE id=?", (rid,))
        conn.commit()
    except Exception as e:
        import logging; logging.warning(f"[disposals] delete: {e}")
    return ok({})
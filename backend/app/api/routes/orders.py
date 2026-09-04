from fastapi import APIRouter
from app.core.database import get_db, QueryBuilder
from app.core.response import ok, fail

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("")
def list_orders(db = get_db(), page: int = 1, page_size: int = 50,
                search: str = '', status: str = '', store: str = '',
                sort_by: str = 'id', sort_order: str = 'desc', channel: str = 'jd'):
    """订单列表 — 数据库级过滤+排序+分页"""
    # 白名单排序字段防注入
    allowed_sort = {'id','order_no','ordered_at','total_amount','quantity','order_status','store','sku'}
    sort_col = sort_by if sort_by in allowed_sort else 'id'

    # 构建查询
    q = db.table("orders").select("*")
    # 软删除过滤：deleted_at 为空或 ''（与 products 一致，修复删单后仍在列表）
    q._where.append("(deleted_at='')")
    # 渠道过滤：jd → platform=京东或空, other → 非京东
    if channel == 'jd':
        q = q.in_("platform", ["京东", ""])
    else:
        q = q.neq("platform", "京东").neq("platform", "")

    # 多字段搜索（OR）
    if search:
        s = search.strip()
        q1 = QueryBuilder("orders", db.table("orders").conn).ilike("order_no", f"%{s}%")
        q2 = QueryBuilder("orders", db.table("orders").conn).ilike("product_name", f"%{s}%")
        q3 = QueryBuilder("orders", db.table("orders").conn).ilike("sku", f"%{s}%")
        q_or = q1.or_(q2).or_(q3)
        q._where = q_or._where
        q._params = q_or._params

    if status:
        q = q.eq("order_status", status)
    if store:
        q = q.eq("store", store)

    # 总条数
    count_q = db.table("orders").select("count(*)")
    count_q._where = list(q._where)
    count_q._params = list(q._params)
    count_result = count_q.execute()
    total = count_result.count if hasattr(count_result, 'count') else len(count_result.data or [])

    # 分页 + 排序
    desc = sort_order == 'desc'
    q = q.order(sort_col, desc)
    q = q.limit(page_size).offset((page - 1) * page_size)

    items = q.execute().data or []

    return ok({
        'total': total,
        'page': page,
        'page_size': page_size,
        'total_pages': max(1, (total + page_size - 1) // page_size),
        'items': items,
    })


@router.post('/batch-delete')
def batch_delete_orders(ids: str = '', db = get_db()):
    if not ids or ids == 'auto':
        data = db.table("orders").delete().ilike("order_no", "AUTO-%").execute().data
        deleted = len(data)
    else:
        id_list = [int(x.strip()) for x in ids.split(',') if x.strip().isdigit()]
        data = db.table("orders").delete().in_("id", id_list).execute().data
        deleted = len(data)
    return {'ok': True, 'deleted': deleted}


def _invalidate_sales_caches():
    """订单删除/恢复 → 补货/采购缓存失效（看板已由 adjust_dashboard_for_order 增量修正，无需重建覆盖）"""
    try:
        from app.core.replenishment_cache import invalidate_cache
        db = get_db()
        invalidate_cache(db)
        # 不再 invalidate_dashboard：看板已被增量更新，异步重建会覆盖修正值
        # 通知前端数据变更
        from app.api.routes.ws import broadcast_sync; broadcast_sync("data.updated")
    except Exception as e:
        import logging; logging.warning(f"[orders] invalidate caches: {e}")


@router.delete('/{oid}')
def delete_order(oid: int, db = get_db()):
    from datetime import datetime, timezone
    # 先取订单用于快照调整（删除后行还在但 deleted_at 变了，先查）
    _o = db.table("orders").select("*").eq("id", oid).execute().data
    db.table("orders").update({"deleted_at": datetime.now(timezone.utc).isoformat()}).eq("id", oid).execute()
    # 删历史订单 → 日销快照即时扣减（修复: 否则快照含已删单到次日重建）
    if _o:
        from app.core.sales_utils import adjust_snapshot_for_order, adjust_dashboard_for_order
        adjust_snapshot_for_order(_o[0], -1)
        adjust_dashboard_for_order(_o[0], -1)
    _invalidate_sales_caches()
    return {"ok": True, "id": oid}

@router.post('/{oid}/restore')
def restore_order(oid: int, db = get_db()):
    _o = db.table("orders").select("*").eq("id", oid).execute().data
    db.table("orders").update({"deleted_at": ""}).eq("id", oid).execute()
    # 恢复历史订单 → 日销快照即时加回
    if _o:
        from app.core.sales_utils import adjust_snapshot_for_order, adjust_dashboard_for_order
        adjust_snapshot_for_order(_o[0], 1)
        adjust_dashboard_for_order(_o[0], 1)
    _invalidate_sales_caches()
    return {"ok": True, "id": oid}

@router.post('/{oid}/permanent-delete')
def permanent_delete_order(oid: int, db = get_db()):
    db.table("orders").delete().eq("id", oid).execute()
    return {"ok": True, "id": oid}

"""原生 products 路由(方案 B): 分页+搜索+批量操作(契约与旧 backend 一致)"""
from fastapi import APIRouter
from fastapi import Request

from db import query, one, execute
from routes.common import ok, fail, traced, try_err

router = APIRouter(tags=["products"])

_FIELDS = ("id, sku, barcode, product_name, brand, store, category, price, box_qty, unit, status, "
           "channel, weight, volume, best_before, expiry_managed, deleted_at, ext_json")


@router.get("/products")
@traced
def list_products(channel: str = "jd", page: int = 1, page_size: int = 30,
                  search: str = "", include_deleted: int = 0):
    # 接口缓存(30s——商品停用/启用/编辑 invalidate("products") 清, 实时性不损; 缓解偶发 RU 慢)
    from routes.analysis_cache import cache_get as _cg
    _key = "products|v2|%s|%s|%s|%s|%s" % (channel, page, page_size, search, include_deleted)
    return ok(_cg(_key, 30, lambda: _list_products_inner(channel, page, page_size, search, include_deleted)))


def _list_products_inner(channel, page, page_size, search, include_deleted):
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
    else:
        rows = query("SELECT %s FROM products WHERE %s ORDER BY id ASC" % (_FIELDS, where), params)

    # 注入批次总效期 batch_days(只查当前页 SKU)
    # 2026-11-04 修正两处错(用户定案"按导入值, 不反推"):
    #   1) 原 MIN(prod_date)/MIN(exp_date) 未排除空日期批次 —— 空串在 MIN 里排最前,
    #      只要有一条无日期批次, 整条算不出 → 前端显示空白(线上 819/874 个做效期管理的 SKU 中招)
    #   2) 原两个 MIN 各自跨批次取最小 —— 生产日期来自 A 批、到期日来自 B 批, 凑出不存在的保质期
    #   现在: 只在**日期完整**的批次里选到期最早的那一批, 用它自己的两个导入日期相减(同一批, 不拼凑)
    _skus = [str(r.get("sku") or "") for r in rows if r.get("sku")]
    if _skus:
        from datetime import datetime as _dt
        _ph = ",".join(["%s"] * len(_skus))
        _earliest = {}
        for b in query("SELECT sku, prod_date, exp_date FROM batches "
                       "WHERE channel=%s AND sku IN (" + _ph + ") "
                       "AND COALESCE(prod_date,'')<>'' AND COALESCE(exp_date,'')<>''",
                       [channel] + _skus):
            _sku = str(b.get("sku") or "")
            _pd = str(b.get("prod_date") or "")[:10]
            _ed = str(b.get("exp_date") or "")[:10]
            if not _sku or not _pd or not _ed:
                continue
            if _sku not in _earliest or _ed < _earliest[_sku][1]:
                _earliest[_sku] = (_pd, _ed)
        _bmap = {}
        for _sku, (_pd, _ed) in _earliest.items():
            try:
                # 2026-11-04 去兜底: 到期日早于生产日 = 数据错误, **如实给负值**(页面一眼可见),
                #   原 max(..., 0) 把错误抹成"保质期 0 天", 看起来像正常数据。
                _bmap[_sku] = (_dt.strptime(_ed, "%Y-%m-%d")
                               - _dt.strptime(_pd, "%Y-%m-%d")).days
            except Exception as _e:
                    try_err('products', '静默降级', _e)
        for r in rows:
            # 无完整日期批次 → None(前端显示 '-' 占位), 不再用 0 混同"保质期 0 天"
            r["batch_days"] = _bmap.get(str(r.get("sku") or ""))

    # is_active 注入(前端批量面板按钮状态: status==='active' → 1; 原缺失致批量停用按钮恒禁用)
    for r in rows:
        r.setdefault("is_active", 1 if (r.get("status") or "active") == "active" else 0)

    if page > 0 and page_size > 0:
        return {"items": rows, "total": total, "page": page, "page_size": page_size}
    return rows


@router.post("/products/batch")
@traced
async def products_batch(request: Request):
    """批量操作: {action: active|inactive|delete|restore, ids: []}"""
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('products', '静默降级', _e)
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
    from routes.analysis_cache import invalidate
    invalidate("products")
    return ok({"updated": len(ids)})

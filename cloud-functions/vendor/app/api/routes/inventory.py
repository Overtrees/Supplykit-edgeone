from fastapi import APIRouter
from app.core.database import get_db
from app.core.response import ok, fail

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


@router.get("/stock-overview")
@router.get("/out-of-stock")
def out_of_stock_dim(channel: str = 'jd', wh: str = ''):
    """按健康卡视图维度返回全量缺货列表(完整性):
    wh=own/platform/platform_b → 该主体 available_qty<=0 的行; wh=bc → B+C 按 SKU 合计 avail=0
    (健康卡缺货弹窗用——曾用全渠道 LIMIT100 的 stockOverview 过滤, 缺货 SKU 在100条窗口外时漏显)
    """
    from app.core.database import get_conn
    try:
        conn = get_conn()
        if wh == 'bc':
            rows = conn.execute(
                "SELECT sku, MAX(product_name), 'bc' FROM inventory "
                "WHERE channel=? AND warehouse_type IN ('platform','platform_b') "
                "GROUP BY sku HAVING SUM(available_qty) <= 0 ORDER BY sku", (channel,)).fetchall()
            items = [{"sku": str(r[0]), "product_name": str(r[1] or r[0]), "warehouse_type": "bc"} for r in rows]
        elif wh:
            rows = conn.execute(
                "SELECT sku, product_name, warehouse_type FROM inventory "
                "WHERE channel=? AND warehouse_type=? AND available_qty<=0 ORDER BY sku",
                (channel, wh)).fetchall()
            items = [{"sku": str(r[0]), "product_name": str(r[1] or r[0]), "warehouse_type": str(r[2] or '')} for r in rows]
        else:
            items = []
        conn.close()
        return ok({"items": items, "count": len(items)})
    except Exception as e:
        import logging; logging.warning(f"[inventory] out-of-stock: {e}")
        return ok({"items": [], "count": 0})


def stock_overview(channel: str = 'jd', db = get_db()):
    """看板缺货/低库存轻量聚合（SQL 一次查, 替代全量 inventory 前端过滤）

    返回缺货 SKU 列表 + 低库存/总数（轻量, 避免全量 17000 行传输+前端遍历）
    """
    from app.core.database import get_conn
    conn = get_conn()
    try:
        # 缺货 SKU 列表（avail<=0）+ 主体/仓库（渲染标签用）
        rows = conn.execute(
            "SELECT sku, product_name, warehouse_type, warehouse FROM inventory WHERE channel=? AND available_qty<=0 ORDER BY sku LIMIT 100",
            (channel,)).fetchall()
        items = [{"sku": str(r[0]), "product_name": str(r[1] or r[0]), "warehouse_type": str(r[2] or ''), "warehouse": str(r[3] or '')} for r in rows]
        # 低库存数 + 总 SKU 数（SQL 聚合）
        low_cnt = conn.execute("SELECT COUNT(*) FROM inventory WHERE channel=? AND available_qty < safety_qty", (channel,)).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM inventory WHERE channel=?", (channel,)).fetchone()[0]
        return ok({"items": items, "out_of_stock_count": len(items), "low_stock_count": low_cnt, "total": total})
    except Exception as e:
        import logging; logging.warning(f"[inventory] stock-overview: {e}")
        return ok({"items": [], "out_of_stock_count": 0, "low_stock_count": 0, "total": 0})


@router.get("")
def list_inventory(db = get_db(), channel: str = 'jd', store: str = '', warehouse_type: str = '',
                   page: int = 0, page_size: int = 0):
    """库存列表 — 支持分页、渠道过滤、店铺过滤、仓库类型过滤"""
    q = db.table("inventory").select("*").eq("channel", channel)
    # B 仓（platform_b）是京东主体专属概念，其他渠道强制排除
    if channel != 'jd':
        q = q.neq("warehouse_type", "platform_b")
    # 联表查询商品价格
    products = {p['sku']: p for p in (db.table("products").select("*").eq("channel", channel).eq("deleted_at", "").execute().data or [])}
    if store:
        q = q.eq("store", store)
    if warehouse_type:
        q = q.eq("warehouse_type", warehouse_type)

    if page > 0 and page_size > 0:
        count_q = db.table("inventory").select("count(*)")
        if store:
            count_q = count_q.eq("store", store)
        if warehouse_type:
            count_q = count_q.eq("warehouse_type", warehouse_type)
        cr = count_q.execute()
        total = cr.count if hasattr(cr, 'count') else len(cr.data or [])
        q = q.order("id", desc=True).limit(page_size).offset((page - 1) * page_size)
        data = q.execute().data or []
        # 注入商品价格
        for item in data:
            p = products.get(item.get('sku', ''))
            if p: item['price'] = p.get('price', 0)
            if p: item['brand'] = p.get('brand', '')
        # 批量注入批次摘要
        _batch_summary = _get_batch_summary(channel, warehouse_type)
        for item in data:
            _key = (item.get('sku',''), item.get('channel','jd'))
            _bs = _batch_summary.get(_key)
            if _bs:
                item['batch_prod_date'] = _bs[0]
                item['batch_exp_date'] = _bs[1]
                item['batch_status'] = _bs[2]
                item['batch_pct'] = _bs[3]
                item['batch_days'] = _bs[5]
                item['batch_count'] = _bs[6]
            else:
                item['batch_prod_date'] = item['batch_exp_date'] = item['batch_status'] = ''
                item['batch_pct'] = 0
        return ok({
            'items': data,
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': max(1, (total + page_size - 1) // page_size),
        })

    data = q.order("id", desc=True).execute().data or []
    for item in data:
        p = products.get(item.get('sku', ''))
        if p: item['price'] = p.get('price', 0)
        if p: item['brand'] = p.get('brand', '')
    # 批量注入批次摘要（最早生产日/截止日/效期状态）
    _batch_summary = _get_batch_summary(channel, warehouse_type)
    for item in data:
        _key = (item.get('sku',''), item.get('channel','jd'))
        _bs = _batch_summary.get(_key)
        if _bs:
            item['batch_prod_date'] = _bs[0]
            item['batch_exp_date'] = _bs[1]
            item['batch_status'] = _bs[2]
            item['batch_pct'] = _bs[3]
            item['batch_days'] = _bs[5]
            item['batch_count'] = _bs[6]
        else:
            item['batch_prod_date'] = item['batch_exp_date'] = item['batch_status'] = ''
            item['batch_pct'] = 0
    return ok(data)


def _get_batch_summary(channel='jd', warehouse_type=''):
    """返回 {(sku, channel): (prod_date, exp_date, status, pct, transit_days, total_days)} 批次摘要

    按 sku 聚合取最早过期批次，JOIN inventory 按 warehouse_type 隔离（自有仓只看自有仓批次）
    """
    try:
        from app.core.database import get_conn
        from datetime import datetime, timedelta, timezone
        conn = get_conn()
        if warehouse_type:
            # 取每个 SKU 最早过期的完整批次（prod/exp 同批次）＋ 该主体下的批次数
            rows = conn.execute("""
                SELECT b.sku, b.prod_date, b.exp_date, cc.cnt FROM (
                    SELECT sku, prod_date, exp_date,
                           ROW_NUMBER() OVER (PARTITION BY sku ORDER BY exp_date ASC) as rn FROM batches
                           WHERE channel=? AND warehouse_type=? AND exp_date != ''
                ) b JOIN (
                    SELECT sku, COUNT(*) as cnt FROM batches WHERE channel=? AND warehouse_type=? GROUP BY sku
                ) cc ON b.sku = cc.sku
                WHERE b.rn = 1
            """, (channel, warehouse_type, channel, warehouse_type)).fetchall()
        else:
            rows = conn.execute("""
                SELECT b.sku, b.prod_date, b.exp_date, cc.cnt FROM (
                    SELECT sku, prod_date, exp_date,
                           ROW_NUMBER() OVER (PARTITION BY sku ORDER BY exp_date ASC) as rn FROM batches
                           WHERE channel=? AND exp_date != ''
                ) b JOIN (
                    SELECT sku, COUNT(*) as cnt FROM batches WHERE channel=? GROUP BY sku
                ) cc ON b.sku = cc.sku
                WHERE b.rn = 1
            """, (channel, channel)).fetchall()
        # 读物流在途天数（默认 3）
        transit = 3
        try:
            _rt = conn.execute("SELECT value FROM replenishment_config WHERE key='transit_days' AND channel=?", (channel,)).fetchone()
            if _rt and _rt[0]: transit = int(_rt[0])
        except Exception: pass
        today = datetime.now(timezone.utc).replace(tzinfo=None)
        out = {}
        for r in rows:
            sku = str(r[0] or '')
            prod = str(r[1] or '')[:10]
            exp = str(r[2] or '')[:10]
            _cnt = int(r[3] or 0)
            status = ''; pct = 0; total_days = 0
            if prod and exp:
                try:
                    prod_dt = datetime.strptime(prod, '%Y-%m-%d')
                    exp_dt = datetime.strptime(exp, '%Y-%m-%d')
                    total_days = (exp_dt - prod_dt).days
                    consumed = (today - prod_dt).days
                    third = max(total_days // 3, 1)
                    if total_days > 0:
                        pct = round(consumed / total_days * 100, 0)
                    # 已消耗 ≥ 拒收线(1/3) → ✗ 否
                    if consumed >= third:
                        status = 'no'
                    # 入仓时已消耗 = 当前 + transit, 入仓时超拒收线 → ⚠️ 临近
                    elif consumed + transit > third:
                        status = 'warn'
                    else:
                        status = 'ok'
                    if consumed >= total_days:
                        status = 'expired'
                except Exception: pass
            out[(sku, channel)] = (prod, exp, status, pct, transit, total_days if total_days > 0 else 0, _cnt)
        return out
    except Exception:
        return {}


@router.post("")
def create_inventory(body: dict, db = get_db()):
    data = db.table("inventory").insert({
        "sku": body.get("sku"),
        "product_name": body.get("product_name"),
        "store": body.get("store", ""),
        "warehouse": body.get("warehouse", ""),
        "warehouse_type": body.get("warehouse_type", "platform"),
        "available_qty": int(body.get("available_qty", 0)),
        "locked_qty": int(body.get("locked_qty", 0)),
        "in_transit_qty": int(body.get("in_transit_qty", 0)),
        "safety_qty": int(body.get("safety_qty", 10)),
    }).execute().data
    inv = data[0] if data else None
    if inv:
        try:
            from app.core.events import bus
            bus.emit('inventory.changed', {
                'inventory': inv,
                'action': 'create',
                'quantity': inv.get('available_qty'),
            })
        except Exception as e:
            import logging; logging.warning(f"[inventory] 事件触发失败: {e}")
    return inv or {"ok": True}


@router.put("/{iid}")
def update_inventory(iid: int, body: dict, db = get_db()):
    db.table("inventory").update(body).eq("id", iid).execute()
    inv = db.table("inventory").select("*").eq("id", iid).execute().data
    inv = inv[0] if inv else None
    if inv:
        try:
            from app.core.events import bus
            bus.emit('inventory.changed', {
                'inventory': inv,
                'action': 'update',
                'quantity': inv.get('available_qty'),
            })
        except Exception:
            pass
        try:
            from app.api.routes.events import create_event
            create_event(db, 'stock.changed', 'inventory', str(inv['id']),
                         f"库存变动: {inv.get('product_name', inv.get('sku',''))}",
                         {'available_qty': inv.get('available_qty'), 'action': 'update'})
        except Exception:
            pass
        try:
            from app.core.rules import evaluate
            evaluate('inventory.changed', {'inv': inv, 'db': db, 'sku': inv.get('sku',''), 'channel': inv.get('channel', 'jd')})
        except Exception:
            pass
    return ok({})


@router.delete("/{iid}")
def delete_inventory(iid: int, db = get_db()):
    db.table("inventory").delete().eq("id", iid).execute()
    return ok({})


@router.post('/batch-type')
def batch_set_warehouse_type(ids: str = '', warehouse: str = '', warehouse_type: str = 'own', db = get_db()):
    """批量设置仓库类型，ids逗号分隔 / warehouse名 / 'all'全部"""
    if warehouse:
        db.table("inventory").update({"warehouse_type": warehouse_type}).eq("warehouse", warehouse).execute()
        return ok({"updated": warehouse, "warehouse": warehouse})
    if ids == 'all':
        db.table("inventory").update({"warehouse_type": warehouse_type}).eq("warehouse_type", "platform").execute()
        return ok({"updated": "all"})
    id_list = [int(x.strip()) for x in ids.split(',') if x.strip().isdigit()]
    if id_list:
        db.table("inventory").update({"warehouse_type": warehouse_type}).in_("id", id_list).execute()
    return ok({"updated": len(id_list)})


@router.post("/adjust")
def adjust_inventory(body: dict, db = get_db()):
    iid = body.get("id")
    action = body.get("action")
    qty = int(body.get("quantity", 0))
    inv = db.table("inventory").select("*").eq("id", iid).execute().data
    inv = inv[0] if inv else None
    if not inv:
        return fail("not found")
    avail = int(inv.get("available_qty") or 0)
    new_avail = avail
    if action == "in":
        new_avail = avail + qty
        db.table("inventory").update({"available_qty": new_avail}).eq("id", iid).execute()
    elif action == "out":
        new_avail = max(0, avail - qty)
        db.table("inventory").update({"available_qty": new_avail}).eq("id", iid).execute()
    elif action == "set":
        new_avail = qty
        db.table("inventory").update({"available_qty": new_avail}).eq("id", iid).execute()
    inv["available_qty"] = new_avail
    return ok({})

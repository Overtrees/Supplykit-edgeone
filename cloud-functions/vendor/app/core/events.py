from collections import defaultdict
from app.core.database import get_db

class EventBus:
    def __init__(self):
        self.listeners = defaultdict(list)

    def on(self, event_name, handler):
        self.listeners[event_name].append(handler)

    def emit(self, event_name, payload=None):
        for handler in self.listeners.get(event_name, []):
            handler(payload or {})

bus = EventBus()


def _invalidate_replenish(_):
    """库存变化 → 补货缓存失效（下次请求重新计算）"""
    try:
        from app.core.replenishment_cache import invalidate_cache
        from app.core.database import get_db
        invalidate_cache(get_db())
    except Exception:
        pass


def _invalidate_all_caches(_):
    """商品变化 → 补货/采购/看板缓存全部失效（建议页联动去除已删商品）"""
    try:
        from app.core.replenishment_cache import invalidate_cache
        from app.core.dashboard_cache import invalidate as invalidate_dashboard
        from app.core.database import get_db
        db = get_db()
        invalidate_cache(db)
        invalidate_dashboard()
    except Exception:
        pass


def register_core_handlers():
    """Register core event handlers at startup.
    Called once from main.py. Handlers use lazy imports to avoid circular deps.
    """
    from app.core.dashboard_cache import invalidate as invalidate_dashboard

    # ─── order.created ──────────────────────────────────────────────
    def _handle_inventory_adjust(data):
        from app.api.routes.insights import auto_adjust_inventory
        db = get_db()
        items = data.get('items', [])
        order_type = data.get('order_type', 'sales')
        for item in items:
            auto_adjust_inventory(item, order_type, db)

    def _handle_event_log(data):
        from app.api.routes.events import create_event
        db = get_db()
        try:
            create_event(db,
                         event_type=data.get('event_type', 'unknown'),
                         entity_type=data.get('entity_type', ''),
                         entity_id=data.get('entity_id'),
                         title=data.get('title', ''),
                         payload=data.get('payload', {}))
        except Exception:
            pass

    def _handle_broadcast(data):
        from app.api.routes.ws import broadcast
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(broadcast(data.get('ws_message', {})))
        except Exception:
            pass

    def _handle_order_rules(data):
        from app.core.rules import evaluate
        from app.core.sales_utils import sku_to_channel
        db = get_db()
        for item in data.get('items', []):
            sku = item.get('sku', '')
            inv_list = db.table("inventory").select("*").eq("sku", sku).execute().data if sku else []
            inv = inv_list[0] if inv_list else {}
            evaluate('order.created', {
                'order_qty': int(item.get('quantity',0)),
                'sku': sku,
                'order': item,
                'inv': inv,
                'db': db,
                'channel': item.get('channel') or sku_to_channel(sku, db) or 'jd',
            })

    bus.on('order.created', _handle_inventory_adjust)
    bus.on('order.created', _handle_event_log)
    bus.on('order.created', _handle_broadcast)
    bus.on('order.created', lambda _: invalidate_dashboard())
    bus.on('order.created', _invalidate_replenish)

    # ─── inventory.changed ──────────────────────────────────────────
    def _handle_inventory_event(data):
        from app.api.routes.events import create_event
        db = get_db()
        inv = data.get('inventory', {})
        try:
            create_event(db, 'stock.changed', 'inventory', str(inv.get('id')),
                         f"库存变动: {inv.get('product_name', inv.get('sku', ''))}",
                         {'available_qty': inv.get('available_qty'),
                          'safety_qty': inv.get('safety_qty'),
                          'action': data.get('action')})
        except Exception:
            pass

    bus.on('inventory.changed', _handle_inventory_event)
    bus.on('inventory.changed', lambda _: invalidate_dashboard())
    bus.on('inventory.changed', _invalidate_replenish)

    # ─── products.changed ──────────────────────────────────────────────
    # 商品删除/停用/修改 → 补货/采购/看板缓存全失效，建议页联动去除
    bus.on('products.changed', _invalidate_all_caches)

    def _handle_products_changed_broadcast(data):
        from app.api.routes.ws import broadcast_sync
        broadcast_sync("data.updated")
    bus.on('products.changed', _handle_products_changed_broadcast)

    # ─── dashboard.updated ─────────────────────────────────────────────
    # dashboard 缓存异步重建完成 → 前端可拉取新数据
    def _handle_dashboard_updated(data):
        from app.api.routes.ws import broadcast_sync
        broadcast_sync("dashboard.updated", data.get('channel', 'jd'))
    bus.on('dashboard.updated', _handle_dashboard_updated)

    # ─── data.cleaned ───────────────────────────────────────────────
    def _handle_cleansed_event(data):
        from app.api.routes.events import create_event
        db = get_db()
        try:
            create_event(db, f"{data.get('target', 'data')}.cleansed", 'data', None,
                         f"清洗导入 {data.get('success', 0)} 条", data)
        except Exception:
            pass

    bus.on('data.cleaned', _handle_cleansed_event)
    bus.on('data.cleaned', _handle_broadcast)
    bus.on('data.cleaned', lambda _: invalidate_dashboard())
    bus.on('data.cleaned', _invalidate_replenish)  # 清洗导入后补货/进销存缓存失效

    # ─── 规则引擎 ──────────────────────────────────────────────────────
    def _handle_rules_engine(data):
        from app.core.rules import evaluate
        evaluate('inventory.changed', {'inv': data.get('inventory', {}), 'db': get_db(), 'sku': data.get('inventory', {}).get('sku',''), 'channel': data.get('inventory', {}).get('channel', 'jd')})
    bus.on('inventory.changed', _handle_rules_engine)

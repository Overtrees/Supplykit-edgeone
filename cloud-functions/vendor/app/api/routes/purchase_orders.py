"""采购记录（BBCC 模式「已下单」标记）持久化

业务含义：京东 BBCC 补货模式下，给 SKU 打上「已下单到 B 仓」的入库批次标记，
配合 arrival_date（到 B 仓日期）监控在库天数，避免超储被京东收取仓储费。
- 创建（POST）：标记 SKU 已下单（upsert，sku+store 维度）
- 更新（PUT）：设置 arrival_date（到 B 仓日期，在库天数计算的起点）
- 删除（DELETE）：取消已下单标记
仅京东 BBCC 模式使用（前端 replenMode==='bbcc' 控制），按 channel 隔离。
"""
from fastapi import APIRouter
from app.core.database import get_db
from app.core.response import ok, fail
from app.core.schemas import PurchaseOrderUpdate
from datetime import datetime

router = APIRouter(prefix="/api/purchase-orders", tags=["purchase_orders"])


@router.get("")
def list_purchase_orders(channel: str = 'jd', db = get_db()):
    items = db.table("purchase_orders").select("*").eq("channel", channel).order("id", desc=True).execute().data or []
    return ok(items)


@router.post("")
def create_purchase_order(sku: str, store: str = '', product_name: str = '',
                          suggested_qty: int = 0, actual_qty: int = 0,
                          arrival_date: str = '', channel: str = 'jd', db = get_db()):
    try:
        db.table("purchase_orders").upsert({
            "sku": sku,
            "store": store,
            "product_name": product_name[:200],
            "suggested_qty": suggested_qty,
            "actual_qty": actual_qty,
            "arrival_date": arrival_date,
            "status": "pending",
            "channel": channel,
        })
        return ok({"sku": sku, "store": store})
    except Exception as e:
        return fail(str(e))


@router.put("/{iid}")
def update_purchase_order(iid: int, body: PurchaseOrderUpdate, db = get_db()):
    data = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if data:
        db.table("purchase_orders").update(data).eq("id", iid).execute()
    return ok({})


@router.delete("")
def delete_purchase_order(sku: str, store: str = '', channel: str = 'jd', db = get_db()):
    try:
        db.table("purchase_orders").delete().eq("sku", sku).eq("store", store).eq("channel", channel).execute()
        return ok({"sku": sku, "store": store})
    except Exception as e:
        return fail(str(e))

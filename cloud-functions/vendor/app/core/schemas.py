"""Pydantic Schema 定义 — API 请求/响应校验"""
from pydantic import BaseModel, Field
from typing import Optional, Any

class RuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    event: str = ""
    condition: dict = {}
    alert_type: str = ""
    alert_title: str = ""
    alert_desc: str = ""
    severity: str = "warning"
    channel: str = "jd"
    mode: str = ""
    is_active: bool = True

class RuleUpdate(BaseModel):
    name: Optional[str] = None
    event: Optional[str] = None
    condition: Optional[dict] = None
    alert_type: Optional[str] = None
    alert_title: Optional[str] = None
    alert_desc: Optional[str] = None
    severity: Optional[str] = None
    mode: Optional[str] = None
    is_active: Optional[bool] = None

class InventoryCreate(BaseModel):
    sku: str = Field(..., min_length=1, max_length=50)
    warehouse: str = ""
    warehouse_type: str = "own"
    available_qty: int = 0
    safety_qty: int = 0
    in_transit_qty: int = 0
    channel: str = "jd"
    store: str = ""

class InventoryUpdate(BaseModel):
    available_qty: Optional[int] = None
    safety_qty: Optional[int] = None
    in_transit_qty: Optional[int] = None
    warehouse: Optional[str] = None
    warehouse_type: Optional[str] = None

class CleansingTemplate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    target: str = "order"
    mapping: dict = {}
    channel: str = "jd"

class CustomField(BaseModel):
    key: str = Field(..., min_length=1, max_length=50)
    label: str = ""
    type: str = "string"

class InboundRecord(BaseModel):
    sku: str = Field(..., min_length=1, max_length=50)
    product_name: str = ""
    quantity: int = Field(default=0, ge=0)
    supplier: str = ""
    inbound_date: str = ""

class OutboundRecord(BaseModel):
    sku: str = Field(..., min_length=1, max_length=50)
    product_name: str = ""
    quantity: int = Field(default=0, ge=0)
    target_warehouse: str = ""
    outbound_date: str = ""

class PurchaseOrderUpdate(BaseModel):
    actual_qty: Optional[int] = None
    arrival_date: Optional[str] = None
    status: Optional[str] = None
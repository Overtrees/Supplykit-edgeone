from sqlalchemy import Column, Integer, String, Float, Text, DateTime, func
from app.models.base import Base

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    order_no = Column(String(100), unique=True)
    store = Column(String(100), default='')
    warehouse = Column(String(100), default='')
    sku = Column(String(100), default='')
    product_name = Column(String(200), default='')
    quantity = Column(Integer, default=0)
    unit_price = Column(Float, default=0)
    total_amount = Column(Float, default=0)
    order_status = Column(String(50), default='已完成')
    ordered_at = Column(String(50), default='')
    platform = Column(String(50), default='')
    supplier = Column(String(100), default='')
    remark = Column(Text, default='')
    parent_order_no = Column(String(100), default='')
    raw_data = Column(Text, default='')
    source = Column(String(50), default='')
    owner_id = Column(String(50), default='')
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class Inventory(Base):
    __tablename__ = "inventory"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sku = Column(String(100))
    product_name = Column(String(200), default='')
    store = Column(String(100), default='')
    warehouse = Column(String(100), default='')
    available_qty = Column(Integer, default=0)
    locked_qty = Column(Integer, default=0)
    in_transit_qty = Column(Integer, default=0)
    safety_qty = Column(Integer, default=0)
    owner_id = Column(String(50), default='')
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sku = Column(String(100), unique=True)
    product_name = Column(String(200), default='')
    store = Column(String(100), default='')
    category = Column(String(100), default='')
    price = Column(Float, default=0)
    status = Column(String(20), default='active')
    owner_id = Column(String(50), default='')
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    supplier_code = Column(String(100), unique=True)
    supplier_name = Column(String(200))
    contact_person = Column(String(100), default='')
    contact_phone = Column(String(50), default='')
    score = Column(Integer, default=0)
    status = Column(String(20), default='active')
    owner_id = Column(String(50), default='')
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_type = Column(String(50))
    title = Column(String(200))
    description = Column(Text, default='')
    severity = Column(String(20), default='warning')
    status = Column(String(20), default='active')
    source = Column(String(50), default='')
    related_sku = Column(String(100), default='')
    owner_id = Column(String(50), default='')
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class QualityLog(Base):
    __tablename__ = "quality_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    log_type = Column(String(50))
    level = Column(String(20), default='error')
    message = Column(Text, default='')
    details = Column(Text, default='')
    source = Column(String(50), default='')
    entity_type = Column(String(50), default='')
    entity_id = Column(String(50), default='')
    field_name = Column(String(100), default='')
    owner_id = Column(String(50), default='')
    created_at = Column(DateTime, default=func.now())

class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50))
    entity_type = Column(String(50), default='')
    entity_id = Column(String(50), default='')
    title = Column(String(200), default='')
    payload = Column(Text, default='')
    level = Column(String(20), default='info')
    owner_id = Column(String(50), default='')
    created_at = Column(DateTime, default=func.now())

class SyncTask(Base):
    __tablename__ = "sync_tasks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_type = Column(String(50))
    status = Column(String(20), default='pending')
    params = Column(Text, default='')
    result = Column(Text, default='')
    owner_id = Column(String(50), default='')
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

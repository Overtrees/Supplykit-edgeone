from pydantic import BaseModel
from typing import Any, List

class DashboardSummary(BaseModel):
    gmv: float
    pending_count: int
    refund_count: int
    low_stock_count: int

class DashboardResponse(BaseModel):
    summary: DashboardSummary
    trend: List[Any]
    stores: List[Any]

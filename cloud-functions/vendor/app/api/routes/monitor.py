"""监控统计接口"""
from fastapi import APIRouter
from app.core.monitor import get_stats

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.get("")
def monitor_stats():
    """返回请求统计、慢接口 TOP10、错误率"""
    return get_stats()
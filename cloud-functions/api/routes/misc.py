"""原生辅助路由(方案 B): quality-logs / monitor"""
from fastapi import APIRouter

from db import query, one
from routes.common import ok, traced

router = APIRouter(tags=["misc"])


@router.get("/quality-logs")
@traced
def list_quality_logs(channel: str = "", limit: int = 200):
    rows = query("SELECT id, log_type, level, message, details, source, created_at FROM quality_logs "
                 "ORDER BY id DESC LIMIT %s", [limit])
    return ok(rows)


@router.get("/monitor")
@traced
def monitor():
    """APM 简化: 最近接口错误统计(内存级统计从简)"""
    err = one("SELECT COUNT(*) AS c FROM quality_logs WHERE level='error'") or {}
    slow = one("SELECT COUNT(*) AS c FROM quality_logs WHERE log_type='slow_request'") or {}
    return ok({"uptime": 0, "total_requests": 0, "avg_response_ms": 0,
               "error_count": int(err.get("c") or 0), "error_rate": 0.0,
               "slow_count": int(slow.get("c") or 0), "slowest_paths": []})

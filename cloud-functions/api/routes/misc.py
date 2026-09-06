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


@router.post("/db/diag")
@traced
async def db_diag(request: Request):
    """RU/执行计划测算(admin 专用, 只读): 免费额度数据驱动优化——EXPLAIN ANALYZE 拿真实扫描行数/耗时"""
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    from routes.common import verify_token
    user = verify_token(token)
    if user != "admin":
        return fail("仅 admin 可用", 403)
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    sql = (d.get("sql") or "").strip()
    if not sql or len(sql) > 2000:
        return fail("sql 缺失或超长")
    if not sql.upper().lstrip().startswith(("EXPLAIN", "SELECT", "SHOW")):
        return fail("仅允许 EXPLAIN/SELECT/SHOW")
    import time as _t
    from db import query as _q
    t0 = _t.time()
    try:
        rows = _q(sql)
        return ok({"sql": sql[:200], "rows": rows[:40],
                   "elapsed_ms": round((_t.time() - t0) * 1000, 1)})
    except Exception as e:
        return fail("执行失败: %s" % str(e)[:200])

"""原生辅助路由(方案 B): quality-logs / monitor"""
from fastapi import APIRouter
from fastapi import Request

from db import query, one
from routes.common import ok, fail, traced

router = APIRouter(tags=["misc"])


@router.get("/quality-logs")
@traced
def list_quality_logs(channel: str = "", limit: int = 200, page: int = 0, page_size: int = 0):
    """质量日志: 默认返回最近 limit 条(兼容全局 loadAll 数组消费); 带 page/page_size 时分页 {items,total}"""
    if page > 0 and page_size > 0:
        r = one("SELECT COUNT(*) AS c FROM quality_logs") or {}
        total = int(r.get("c") or 0)
        rows = query("SELECT id, log_type, level, message, details, source, created_at FROM quality_logs "
                     "ORDER BY id DESC LIMIT %s OFFSET %s", [page_size, (page - 1) * page_size])
        return ok({"items": rows, "total": total, "page": page, "page_size": page_size})
    rows = query("SELECT id, log_type, level, message, details, source, created_at FROM quality_logs "
                 "ORDER BY id DESC LIMIT %s", [limit])
    return ok(rows)


@router.get("/monitor")
@traced
def monitor():
    """APM(轻量实化, 2026-09-11 体检): quality_logs 实时聚合——错误/慢请求留痕即观测面
    (Makers 无状态请求环境, 无进程级 uptime/rps metrics; 慢路径与最新错误可下钻)"""
    from datetime import datetime, timezone as _tz
    err = one("SELECT COUNT(*) AS c FROM quality_logs WHERE level='error'") or {}
    slow = one("SELECT COUNT(*) AS c FROM quality_logs WHERE log_type='slow_request'") or {}
    today = datetime.now(_tz.utc).strftime("%Y-%m-%d")
    err_today = one("SELECT COUNT(*) AS c FROM quality_logs "
                    "WHERE level='error' AND DATE(created_at)=%s", [today]) or {}
    slow_today = one("SELECT COUNT(*) AS c FROM quality_logs "
                     "WHERE log_type='slow_request' AND DATE(created_at)=%s", [today]) or {}
    slow_rows = query("SELECT message, created_at FROM quality_logs "
                      "WHERE log_type='slow_request' ORDER BY id DESC LIMIT 20") or []
    slowest = [{"path": (r.get("message") or "")[:120],
                "at": str(r.get("created_at") or "")[:19]} for r in slow_rows]
    latest_err = query("SELECT message, details, created_at FROM quality_logs "
                       "WHERE level='error' ORDER BY id DESC LIMIT 5") or []
    errs = [{"msg": (r.get("message") or "")[:150],
             "detail": (r.get("details") or "")[:200],
             "at": str(r.get("created_at") or "")[:19]} for r in latest_err]
    return ok({"totals": {"error": int(err.get("c") or 0), "slow": int(slow.get("c") or 0)},
               "today": {"error": int(err_today.get("c") or 0), "slow": int(slow_today.get("c") or 0)},
               "slowest_paths": slowest, "latest_errors": errs,
               "note": "无进程级 metrics(Makers 无状态), 以 quality_logs 留痕为观测面"})


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

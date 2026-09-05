"""Makers 原生后端入口(方案 B: 无 SQLite 适配, 直写 TiDB 方言)

构建器要求: 模块级行首 app = (正则 /^app\\s*=/m)
Makers FastAPI 框架模式: 路由无 /api 前缀(框架剥离后转发, root_path=/api)
"""
import os
import sys

# 函数包运行时 sys.path 只有函数根; 入口目录(api/)需自行加入
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse

from db import one
from routes.auth import router as auth_router
from routes.dashboard import router as dashboard_router
from routes.replenishment import router as replenishment_router
from routes.orders import router as orders_router
from routes.products import router as products_router
from routes.insights import router as insights_router
from routes.common import verify_token

app = FastAPI()


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    import traceback as _tb
    return JSONResponse({"ok": False, "error": "服务器内部错误",
                         "detail": str(exc)[:400],
                         "tb": _tb.format_exc(limit=10)[-1200:]}, status_code=500)


@app.middleware("http")
async def auth_middleware(request: Request, next):
    """鉴权: auth/health/debug 放行, 其余需 Bearer; demo 只读"""
    path = request.url.path
    if (path.startswith("/auth") or path == "/health" or path.startswith("/debug")
            or path.startswith("/docs") or path.startswith("/openapi")):
        return await next(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"detail": "未登录，请先登录"}, status_code=401)
    user = verify_token(auth[7:])
    if not user:
        return JSONResponse({"detail": "Token 无效或已过期"}, status_code=401)
    if request.method in ("POST", "PUT", "DELETE", "PATCH") and user == "demo":
        return JSONResponse({"detail": "访客模式仅可查看，不可修改数据"}, status_code=403)
    return await next(request)


@app.get("/health")
def health():
    from datetime import datetime, timezone
    out = {"status": "ok", "db_backend": "tidb", "timestamp": datetime.now(timezone.utc).isoformat()}
    try:
        r = one("SELECT 1 AS ok")
        out["db"] = "ok" if r else "unknown"
    except Exception as e:
        out["db"] = "error: %s" % str(e)[:150]
        out["status"] = "degraded"
    try:
        r = one("SELECT COALESCE(MAX(date),'') AS m FROM daily_sales_snapshot")
        out["snapshot_max"] = (r or {}).get("m") or ""
    except Exception:
        pass
    return out


app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(replenishment_router)
app.include_router(orders_router)
app.include_router(products_router)
app.include_router(insights_router)

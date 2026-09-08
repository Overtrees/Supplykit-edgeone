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
from routes.alerts import router as alerts_router
from routes.misc import router as misc_router
from routes.suppliers import router as suppliers_router
from routes.rules import router as rules_router
from routes.inventory import router as inventory_router
from routes.batches import router as batches_router
from routes.tasks import router as tasks_router
from routes.cleansing import router as cleansing_router
from routes.purchase import router as purchase_router
from routes.cron import router as cron_router
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
            or path.startswith("/docs") or path.startswith("/openapi")
            or path == "/insights/ping" or path.startswith("/cron")):
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
    # 数据版本指纹: 关键表 MAX(id)/MAX(date) 拼接, 任何数据变更即变化
    # (前端每 15s 轮询 /health 取 version, 变化时 clearCache+loadAll 绕过 30s 前端缓存)
    try:
        r = one(
            "SELECT CONCAT_WS('-',"
            "(SELECT COALESCE(MAX(id),0) FROM orders),"
            "(SELECT COALESCE(MAX(id),0) FROM inventory),"
            "(SELECT COALESCE(MAX(id),0) FROM products),"
            "(SELECT COALESCE(MAX(id),0) FROM alerts),"
            "(SELECT COALESCE(MAX(date),'') FROM daily_sales_snapshot)) AS v")
        out["version"] = str((r or {}).get("v") or "0")
    except Exception:
        out["version"] = "0"
    return out


app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(replenishment_router)
app.include_router(orders_router)
app.include_router(products_router)
app.include_router(insights_router)
app.include_router(alerts_router)
app.include_router(misc_router)
app.include_router(suppliers_router)
app.include_router(rules_router)
app.include_router(inventory_router)
app.include_router(batches_router)
app.include_router(tasks_router)
app.include_router(cleansing_router)
app.include_router(purchase_router)
app.include_router(cron_router)

# ── 启动自动补索引(幂等, 免费额度 RU 优化: 看板 60 天范围查询走 (channel, ordered_at) 区间) ──
_INDEXES = [
    ("idx_orders_channel_ordered", "orders", "channel, ordered_at"),
]
if os.environ.get("DB_BACKEND", "tidb") == "tidb":
    try:
        from db import execute as _exec
        for _iname, _tbl, _cols in _INDEXES:
            try:
                _exec("CREATE INDEX IF NOT EXISTS `%s` ON `%s` (%s)" % (_iname, _tbl, _cols))
            except Exception:
                pass
        # 启动补列(幂等): alerts.warehouse —— 告警逐仓化(规则引擎去重+seed 生成+展示均按 SKU×仓)
        try:
            from db import query as _qry
            _cols = {str(r.get("Field") or "") for r in _qry("SHOW COLUMNS FROM alerts")}
            if "warehouse" not in _cols:
                _exec("ALTER TABLE alerts ADD COLUMN warehouse VARCHAR(64) DEFAULT ''")
        except Exception:
            pass
        # 启动补列(幂等): rules.params —— 规则携带业务计算参数(断货/健康看板逻辑融合进规则配置)
        try:
            from db import query as _qry2
            _rcols = {str(r.get("Field") or "") for r in _qry2("SHOW COLUMNS FROM rules")}
            if "params" not in _rcols:
                _exec("ALTER TABLE rules ADD COLUMN params TEXT")
        except Exception:
            pass
        # 内置"濒临断货预警"/"库存健康监控"规则退役(幂等): 断货卡/健康卡为系统级实时计算
        # 承载, 规则告警与之重叠(粒度粗/频率低/数字不一致) → 退役后孤儿清理自动清存量告警;
        # 规则页仍可自建 stockout/health 类型规则(自定义告警进'其他'分组)
        try:
            from db import execute as _exec5
            _exec5("UPDATE rules SET is_active=0, deleted_at=NOW() "
                   "WHERE name IN ('濒临断货预警','库存健康监控') AND is_active=1 "
                   "AND (deleted_at IS NULL OR deleted_at='')")
        except Exception:
            pass
        # 内置"滞销识别"规则退役(幂等, 存量库): 滞销由处置建议页(品类多因素分级)单一承载,
        # 规则版(仅 days>30)粗糙且与处置页重复 → 退役后孤儿清理自动关存量 slow_moving 告警
        try:
            from db import execute as _exec3
            _exec3("UPDATE rules SET is_active=0, deleted_at=NOW() "
                   "WHERE name='滞销识别' AND alert_type='slow_moving' AND is_active=1 "
                   "AND (deleted_at IS NULL OR deleted_at='')")
        except Exception:
            pass
        # 内置"紧急补货"规则退役(幂等, 存量库): 看板补货告警卡改读补货建议接口(动态缺口),
        # 静态 30%*安全线 阈值告警与低库存 100% 重叠且口径与补货建议脱节 → 退役后每日孤儿清理
        # 自动关闭存量 replenish 告警(用户自定义 replenish 规则不受影响)
        try:
            from db import execute as _exec2
            _exec2("UPDATE rules SET is_active=0, deleted_at=NOW() "
                   "WHERE name='紧急补货' AND alert_type='replenish' AND is_active=1 "
                   "AND (deleted_at IS NULL OR deleted_at='')")
        except Exception:
            pass
    except Exception:
        pass

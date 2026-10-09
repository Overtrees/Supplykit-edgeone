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
from fastapi.exceptions import RequestValidationError
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
from routes.common import verify_token, try_err

app = FastAPI()


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    import traceback as _tb
    return JSONResponse({"ok": False, "error": "服务器内部错误",
                         "detail": str(exc)[:400],
                         "tb": _tb.format_exc(limit=10)[-1200:]}, status_code=500)


@app.exception_handler(RequestValidationError)
async def _validation(request: Request, exc: RequestValidationError):
    """校验错误(422)收口: FastAPI 校验层异常不走路由 traced——显式 handler 记 quality_logs 留痕"""
    try:
        from db import execute as _e
        _e("INSERT INTO quality_logs(log_type, level, message, details, source) "
           "VALUES(%s,%s,%s,%s,%s)",
           ("api_error", "warning", ("422 %s %s" % (request.method, request.url.path)),
            str(exc.errors())[:300], "api"))
    except Exception:
        pass
    return JSONResponse({"detail": exc.errors()}, status_code=422)


@app.middleware("http")
async def slow_log_middleware(request: Request, next):
    """慢请求监控(>3s 写 quality_logs —— monitor slow_count 此前恒 0 的缺口)"""
    import time as _t
    _t0 = _t.time()
    _resp = await next(request)
    _el = _t.time() - _t0
    if _el > 3:
        try:
            from db import execute as _e
            _e("INSERT INTO quality_logs(log_type, level, message, source) "
               "VALUES(%s,%s,%s,%s)", ("slow_request","warning",
               ("%s %s %.1fs" % (request.method, request.url.path, _el)), "api"))
        except Exception as _e:
                try_err('index', '静默降级', _e)
    return _resp


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
    except Exception as _e:
            try_err('index', '静默降级', _e)
    # 数据版本指纹: 关键表 MAX(id)/MAX(date) 拼接, 任何数据变更即变化
    # (前端每 15s 轮询 /health 取 version, 变化时 clearCache+loadAll 绕过 30s 前端缓存)
    try:
        r = one(
            "SELECT CONCAT_WS('-',"
            "(SELECT COALESCE(MAX(id),0) FROM orders),"
            "(SELECT COALESCE(MAX(id),0) FROM inventory),"
            "(SELECT COALESCE(MAX(id),0) FROM products),"
            "(SELECT COALESCE(MAX(id),0) FROM alerts),"
            "(SELECT COALESCE(MAX(date),'') FROM daily_sales_snapshot),"
            "(SELECT COALESCE(MAX(id),0) FROM rules),"
            "(SELECT COALESCE(MAX(id),0) FROM suppliers),"
            "(SELECT COALESCE(MAX(id),0) FROM replenishment_config)) AS v")
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
    ("idx_alerts_ch_status_created", "alerts", "channel, status, created_at"),  # 告警分组配额/列表查询(2026-09-11 体检)
    ("idx_rules_ch_active", "rules", "channel, is_active"),  # 规则列表/评估孤儿查询(2026-09-11 体检)
    ("idx_snapshot_ch_date", "daily_sales_snapshot", "channel, date"),  # 日销窗口查询(2026-10-08 建议页 10s→索引)
]
# 冗余索引删除(被复合索引前缀覆盖, 写放大; 幂等 IF EXISTS)
_DROP_INDEXES = [
    ("idx_orders_sku", "orders"),  # 被 idx_orders_sku_ordered_at(sku,ordered_at,channel) 前缀覆盖
]
if os.environ.get("DB_BACKEND", "tidb") == "tidb":
    try:
        from db import execute as _exec
        for _iname, _tbl, _cols in _INDEXES:
            try:
                _exec("CREATE INDEX IF NOT EXISTS `%s` ON `%s` (%s)" % (_iname, _tbl, _cols))
            except Exception as _e:
                    try_err('index', '静默降级', _e)
        for _iname, _tbl in _DROP_INDEXES:
            try:
                _exec("DROP INDEX IF EXISTS `%s` ON `%s`" % (_iname, _tbl))
            except Exception as _e:
                    try_err('index', '静默降级', _e)
        # 共享表缓存(2026-09-09 治本: Makers 请求模式多实例, 内存缓存命中率≈0 → 聚合缓存落 TiDB 表)
        try:
            _exec("CREATE TABLE IF NOT EXISTS maintenance_log (`date` DATE NOT NULL, task VARCHAR(32) NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(`date`, task))")
            _exec("CREATE TABLE IF NOT EXISTS health_snapshot (`date` DATE NOT NULL, channel VARCHAR(20) NOT NULL, score INT DEFAULT 0, own_score INT DEFAULT -1, platform_score INT DEFAULT -1, bc_score INT DEFAULT -1, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(date, channel))")
            _exec("CREATE TABLE IF NOT EXISTS analysis_cache ("
                  "`key` VARCHAR(160) PRIMARY KEY, "
                  "value MEDIUMTEXT, "
                  "created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6))")
            # 规则变更即时重算告警(2026-09-11): 脏标记单行, 线程 DELETE 抢占串行评估
            _exec("CREATE TABLE IF NOT EXISTS eval_pending ("
                  "`task` VARCHAR(32) PRIMARY KEY, "
                  "updated_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6))")
            # 一次性数据迁移登记表(2026-09-15: barcode 补齐 / 时间仿真随机化 —— INSERT IGNORE 抢注幂等)
            _exec("CREATE TABLE IF NOT EXISTS migration_log (name VARCHAR(64) PRIMARY KEY, done_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
            _exec("CREATE TABLE IF NOT EXISTS log_archives ("
                  "`date` CHAR(10) NOT NULL, `scope` VARCHAR(16) NOT NULL DEFAULT 'all', "
                  "markdown MEDIUMTEXT, `count` INT DEFAULT 0, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                  "PRIMARY KEY(`date`, `scope`))")
            # 看板提速物化表(2026-09-15): 日级订单汇总(历史区间 summary 读此替代 19 万行直查)
            _exec("CREATE TABLE IF NOT EXISTS orders_day_agg ("
                  "`date` DATE NOT NULL, channel VARCHAR(20) NOT NULL, order_status VARCHAR(20) NOT NULL, "
                  "store VARCHAR(60) NOT NULL, gmv DECIMAL(14,2) DEFAULT 0, subsidy DECIMAL(14,2) DEFAULT 0, cnt INT DEFAULT 0, "
                  "PRIMARY KEY(`date`, channel, order_status, store))")
        except Exception as _e:
                try_err('index', '静默降级', _e)
        # ── 启动数据迁移(2026-09-15: barcode 补齐 / 时间仿真随机化)
        # 设计: 不抢注, 靠 WHERE 条件幂等(空值/0点) + 分批(5万) → 每次启动续跑, 中断下次续跑, 完成自然零行
        # 注意: SQL 内 DATE_FORMAT 的 % 必须 %% 转义(pymysql 参数化时 % 被当占位符 → 曾致迁移假成功)
        try:
            import threading as _thM
            def _run_migrations():
                try:
                    from db import one as _oneM
                    # M0: 物化日级汇总初始化(首次构建 90 天历史; 此后每日 _daily_maintenance 增量)
                    try:
                        if _exec("INSERT IGNORE INTO migration_log(name) VALUES('day_agg_init')"):
                            from routes.dashboard import _rebuild_day_agg
                            _rebuild_day_agg(90)
                    except Exception as _e:
                            try_err('index', '静默降级', _e)
                    # M1: 订单 69 码补齐(products 全有 barcode, sku 全关联; 分批按 id, WHERE 空值幂等)
                    try:
                        _mx = _oneM("SELECT COALESCE(MAX(id),0) AS m FROM orders") or {}
                        _mxn = int(_mx.get("m") or 0)
                        _m1 = 0
                        for _lo in range(0, _mxn + 1, 50000):
                            _m1 += _exec("UPDATE orders o JOIN products p ON o.sku=p.sku SET o.barcode=p.barcode "
                                         "WHERE o.id BETWEEN %s AND %s AND (o.barcode IS NULL OR o.barcode='') "
                                         "AND p.barcode IS NOT NULL AND p.barcode != ''", [_lo, _lo + 49999])
                        _exec("INSERT INTO quality_logs(log_type, level, message, source) "
                              "VALUES('migration','info',%s,'index')",
                              ("M1 订单69码补齐完成(补 %d 行)" % _m1,))
                    except Exception as _e:
                            try_err('index', '静默降级', _e)
                    # M2: 订单时间仿真随机化(WHERE 0点幂等, 分批)
                    try:
                        _mx2 = _oneM("SELECT COALESCE(MAX(id),0) AS m FROM orders") or {}
                        _mxn2 = int(_mx2.get("m") or 0)
                        _m2 = 0
                        for _lo in range(0, _mxn2 + 1, 50000):
                            _m2 += _exec("UPDATE orders SET "
                                         "ordered_at = CONCAT(DATE_FORMAT(ordered_at,'%%Y-%%m-%%d'),' ',"
                                         "LPAD(FLOOR(RAND()*24),2,'0'),':',LPAD(FLOOR(RAND()*60),2,'0'),':',LPAD(FLOOR(RAND()*60),2,'0')), "
                                         "paid_at = CONCAT(DATE_FORMAT(paid_at,'%%Y-%%m-%%d'),' ',"
                                         "LPAD(FLOOR(RAND()*24),2,'0'),':',LPAD(FLOOR(RAND()*60),2,'0'),':',LPAD(FLOOR(RAND()*60),2,'0')) "
                                         "WHERE id BETWEEN %s AND %s AND RIGHT(ordered_at,8)='00:00:00'", [_lo, _lo + 49999])
                        _exec("INSERT INTO quality_logs(log_type, level, message, source) "
                              "VALUES('migration','info',%s,'index')",
                              ("M2 订单时间随机化完成(%d 行)" % _m2,))
                    except Exception as _e:
                            try_err('index', '静默降级', _e)
                    # M3: 出入库时间仿真随机化(同理; 2026-10-07 修复: Python % 格式化把 %%Y 变 %Y 后
                    # pymysql 参数化再当占位符 → TypeError 迁移从未成功 —— 改 f-string 拼表名 + %% 交 pymysql 转义)
                    try:
                        _m3 = 0
                        for _tbl, _col in (('inbound_records', 'inbound_date'), ('outbound_records', 'outbound_date')):
                            _m3 += _exec(f"UPDATE `{_tbl}` SET `{_col}` = CONCAT(DATE_FORMAT(`{_col}`,'%%Y-%%m-%%d'),' ',"
                                         "LPAD(FLOOR(RAND()*24),2,'0'),':',LPAD(FLOOR(RAND()*60),2,'0'),':',LPAD(FLOOR(RAND()*60),2,'0')) "
                                         f"WHERE RIGHT(`{_col}`,8)='00:00:00'", [])
                        _exec("INSERT INTO quality_logs(log_type, level, message, source) "
                              "VALUES('migration','info',%s,'index')",
                              ("M3 出入库时间随机化完成(%d 行)" % _m3,))
                    except Exception as _e:
                            try_err('index', '静默降级', _e)
                except Exception as _e:
                        try_err('index', '静默降级', _e)
            _thM.Thread(target=_run_migrations, daemon=True).start()
        except Exception as _e:
                try_err('index', '静默降级', _e)
        # 启动补列(幂等): 自定义扩展列 ext_json(用户动态新增列数据存放, 方案 B 2026-09-10)
        try:
            from db import query as _qryX
            for _t in ("orders", "inventory", "products", "suppliers",
                       "inbound_records", "outbound_records"):
                try:
                    _hx = {str(r.get("Field") or "") for r in _qryX("SHOW COLUMNS FROM `%s`" % _t)}
                    if "ext_json" not in _hx:
                        _exec("ALTER TABLE `%s` ADD COLUMN ext_json TEXT" % _t)
                except Exception as _e:
                        try_err('index', '静默降级', _e)
        except Exception as _e:
                try_err('index', '静默降级', _e)
        # 启动补列(幂等): 入库/出库记录带商品属性列(69码/平台/品牌/店铺/分类/单价/箱规/单位/重量/体积/状态)
        # —— 出入库明细展示与导出需要商品属性, 原 schema 仅 10 列(用户需求 2026-09-10)
        try:
            from db import query as _qryC
            for _t in ("inbound_records", "outbound_records"):
                try:
                    _have = {str(r.get("Field") or "") for r in _qryC("SHOW COLUMNS FROM `%s`" % _t)}
                except Exception:
                    continue
                for _col, _ddl in (("barcode", "VARCHAR(64) DEFAULT ''"),
                                   ("platform", "VARCHAR(32) DEFAULT ''"),
                                   ("brand", "VARCHAR(64) DEFAULT ''"),
                                   ("store", "VARCHAR(64) DEFAULT ''"),
                                   ("category", "VARCHAR(64) DEFAULT ''"),
                                   ("price", "DECIMAL(12,2) DEFAULT 0"),
                                   ("box_qty", "INT DEFAULT 0"),
                                   ("unit", "VARCHAR(16) DEFAULT ''"),
                                   ("weight", "DECIMAL(10,2) DEFAULT 0"),
                                   ("volume", "DECIMAL(10,4) DEFAULT 0"),
                                   ("status", "VARCHAR(16) DEFAULT ''")):
                    if _col not in _have:
                        try:
                            _exec("ALTER TABLE `%s` ADD COLUMN `%s` %s" % (_t, _col, _ddl))
                        except Exception as _e:
                                try_err('index', '静默降级', _e)
        except Exception as _e:
                try_err('index', '静默降级', _e)
        # 启动补列(幂等): alerts.warehouse —— 告警逐仓化(规则引擎去重+seed 生成+展示均按 SKU×仓)
        try:
            from db import query as _qry
            _cols = {str(r.get("Field") or "") for r in _qry("SHOW COLUMNS FROM alerts")}
            if "warehouse" not in _cols:
                _exec("ALTER TABLE alerts ADD COLUMN warehouse VARCHAR(64) DEFAULT ''")
        except Exception as _e:
                try_err('index', '静默降级', _e)
        # 启动补列(幂等): rules.params —— 规则携带业务计算参数(断货/健康看板逻辑融合进规则配置)
        try:
            from db import query as _qry2
            _rcols = {str(r.get("Field") or "") for r in _qry2("SHOW COLUMNS FROM rules")}
            if "params" not in _rcols:
                _exec("ALTER TABLE rules ADD COLUMN params TEXT")
        except Exception as _e:
                try_err('index', '静默降级', _e)
        # 内置"濒临断货预警"/"库存健康监控"规则 = 计算参数载体(软删恢复 + alert_enabled=0 不告警;
        # 断货卡/健康卡同源读取 params)。永久删除不补(尊重用户删除); 用户自改 params 保留合并
        try:
            import json as _json2
            from db import query as _qry5, execute as _exec6
            for _ch in ("jd", "other"):
                for _nm, _at, _defp in (("濒临断货预警", "stockout",
                                         {"lit": 3 if _ch == "jd" else 10, "otif_min": 0.6, "ss_z": 1.65,
                                          "accel_ratio": 1.3, "accel_min_qty": 10, "buffer_orange": 1.2,
                                          "buffer_yellow": 1.0, "orange_slack_days": 1,
                                          "include_avail_zero": 1, "log": 0, "alert_enabled": 0}),
                                        ("库存健康监控", "health",
                                         {"health_good": 85, "health_warning": 60, "log": 0, "alert_enabled": 0})):
                    _row = (_qry5("SELECT id, params FROM rules WHERE name=%s AND channel=%s "
                                  "AND (deleted_at IS NOT NULL AND deleted_at != '' OR is_active=0) "
                                  "LIMIT 1", [_nm, _ch]) or [None])[0]
                    if _row:  # 软删/停用 → 恢复为参数载体(合并用户改的 params, 强制 alert_enabled=0)
                        _p2 = {}
                        try:
                            _p2 = _json2.loads(_row.get("params") or "{}")
                        except Exception as _e:
                                try_err('index', '静默降级', _e)
                        _p2 = {**(_p2 if isinstance(_p2, dict) else {}), "alert_enabled": 0}
                        _exec6("UPDATE rules SET deleted_at='', is_active=1, params=%s WHERE id=%s",
                               (_json2.dumps(_p2, ensure_ascii=False), _row.get("id")))
        except Exception as _e:
                try_err('index', '静默降级', _e)
        # 内置"滞销识别"规则退役(幂等, 存量库): 滞销由处置建议页(品类多因素分级)单一承载,
        # 规则版(仅 days>30)粗糙且与处置页重复 → 退役后孤儿清理自动关存量 slow_moving 告警
        try:
            from db import execute as _exec3
            _exec3("UPDATE rules SET is_active=0, deleted_at=NOW() "
                   "WHERE name='滞销识别' AND alert_type='slow_moving' AND is_active=1 "
                   "AND (deleted_at IS NULL OR deleted_at='')")
        except Exception as _e:
                try_err('index', '静默降级', _e)
        # 内置"紧急补货"规则退役(幂等, 存量库): 看板补货告警卡改读补货建议接口(动态缺口),
        # 静态 30%*安全线 阈值告警与低库存 100% 重叠且口径与补货建议脱节 → 退役后每日孤儿清理
        # 自动关闭存量 replenish 告警(用户自定义 replenish 规则不受影响)
        try:
            from db import execute as _exec2
            _exec2("UPDATE rules SET is_active=0, deleted_at=NOW() "
                   "WHERE name='紧急补货' AND alert_type='replenish' AND is_active=1 "
                   "AND (deleted_at IS NULL OR deleted_at='')")
        except Exception as _e:
                try_err('index', '静默降级', _e)
    except Exception as _e:
            try_err('index', '静默降级', _e)

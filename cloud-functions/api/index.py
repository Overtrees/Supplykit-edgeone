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
from routes.channels import router as channels_router
from routes.history import router as history_router  # 2026-11-02 S2-4/A: daily_stats 读出口
from routes.common import verify_token, try_err

app = FastAPI()


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """未捕获异常: 只回 request_id, 堆栈写 quality_logs。

    2026-11-02 S0: 原实现把 traceback(限 10 帧/1200 字符)+ str(exc) 直接回传浏览器 ——
    等于对外公开文件路径、表名、SQL 片段、依赖版本。改后用户凭 request_id 报障,
    运维在 quality_logs 里按 rid 精确检索(比让用户截图堆栈更准)。
    """
    import traceback as _tb
    import uuid as _uuid
    _rid = _uuid.uuid4().hex[:12]
    _stack = _tb.format_exc(limit=12)
    try:
        import asyncio as _aio
        from db import execute as _e

        _msg = ("%s %s rid=%s" % (request.method, request.url.path, _rid))[:190]

        def _write():
            _e("INSERT INTO quality_logs(log_type, level, message, details, source) "
               "VALUES(%s,%s,%s,%s,%s)", ("api_unhandled", "error", _msg, _stack[-1800:], "api"))

        # 必须卸载到线程: 直接 await 同步写库会阻塞事件循环(同 slow_log 的教训)
        await _aio.to_thread(_write)
    except Exception:
        pass
    return JSONResponse({"ok": False, "error": "服务器内部错误", "request_id": _rid},
                        status_code=500)


@app.exception_handler(RequestValidationError)
async def _validation(request: Request, exc: RequestValidationError):
    """校验错误(422)收口: FastAPI 校验层异常不走路由 traced——显式 handler 记 quality_logs 留痕"""
    try:
        from db import execute as _e
        _ct = (request.headers.get("content-type") or "")[:120]
        _body_preview = ""
        try:
            _raw = await request.body()
            _body_preview = _raw[:300].decode("utf-8", errors="replace")
        except Exception:
            pass
        # 422 诊断(2026-10-14): 记录 Content-Type/multipart boundary + body 前 300 字节——定位前端 FormData 构造 vs 网关
        _e("INSERT INTO quality_logs(log_type, level, message, details, source) "
           "VALUES(%s,%s,%s,%s,%s)",
           ("api_error", "warning", ("422 %s %s CT=%s" % (request.method, request.url.path, _ct)),
            ("body: " + _body_preview[:280] + " | err: " + str(exc.errors())[:100]), "api"))
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
        # 2026-10-28 根治实例传染: 原同步 _e(...) 直接阻塞事件循环 —— 一次 12s 组装后
        # 同实例所有请求(slow_log 自身写库)串行排队 → orders/alerts/quality-logs 齐 30s 超时
        # (07:40:48 三请求同秒超时实证); 改 to_thread 卸载, 事件循环不被 DB 写阻塞(数据写入语义不变)
        import asyncio as _aio
        try:
            from db import execute as _e

            def _write_slow():
                _e("INSERT INTO quality_logs(log_type, level, message, source) "
                   "VALUES(%s,%s,%s,%s)", ("slow_request", "warning",
                   ("%s %s %.1fs" % (request.method, request.url.path, _el)), "api"))
                if _el > 10:
                    # 严重慢请求(>10s)升级 error + 可选 webhook 告警(env SLOW_WEBHOOK_URL)
                    _e("INSERT INTO quality_logs(log_type, level, message, source) "
                       "VALUES('slow_alert','error',%s,%s)",
                       ("严重慢请求: %s %s %.1fs" % (request.method, request.url.path, _el), "api"))

            await _aio.to_thread(_write_slow)
        except Exception as _e:
            try_err('index', '静默降级', _e)
        if _el > 10:
            try:
                import os as _os
                _wh = _os.environ.get("SLOW_WEBHOOK_URL", "")
                if _wh:
                    import threading as _thw
                    def _notify():
                        try:
                            import urllib.request as _ur
                            _req = _ur.Request(_wh, data=("严重慢请求: %s %s %.1fs" % (
                                request.method, request.url.path, _el)).encode(),
                                headers={"Content-Type": "text/plain"})
                            _ur.urlopen(_req, timeout=5)
                        except Exception:
                            pass
                    _thw.Thread(target=_notify, daemon=True).start()
            except Exception:
                pass
    return _resp


# 2026-10-14 请求驱动续跑: 任意 API 请求顺带推进 running 导入/填充/导出任务(限频 60s 进程内标记)
# ——免费版调度 1 天限制 + Makers 实例冻结——不能挂页面时, 有请求即推进(用户在线任务不卡)
import time as _ctime
_LAST_CONT = 0.0


@app.middleware("http")
async def auth_middleware(request: Request, next):
    """鉴权: auth/health/debug 放行, 其余需 Bearer; demo 只读"""
    path = request.url.path
    if (path.startswith("/auth") or path == "/health" or path.startswith("/debug")
            or path.startswith("/docs") or path.startswith("/openapi")
            or path == "/insights/ping" or path.startswith("/cron")
            or path.endswith("/logs/frontend")):
        return await next(request)
    # 2026-10-13: 网关将 Authorization 改写为 q-sign——Bearer 被拦截(401 根因)
    # 方案: token 统一走 X-Auth-Token(网关不改写)——只认此头(Bearer 回退去除——非冗余; 本地/冒烟同步改)
    token = request.headers.get("X-Auth-Token", "")
    if not token:
        return JSONResponse({"detail": "未登录，请先登录"}, status_code=401)
    user = verify_token(token)
    if not user:
        return JSONResponse({"detail": "Token 无效或已过期"}, status_code=401)
    if request.method in ("POST", "PUT", "DELETE", "PATCH") and user == "demo":
        return JSONResponse({"detail": "访客模式仅可查看，不可修改数据"}, status_code=403)
    # 请求驱动续跑(限频 60s——轻量推进 running 任务——不阻塞主请求)
    try:
        global _LAST_CONT
        _nowc = _ctime.time()
        # 2026-10-29 修复: 原条件 `request.url.path.startswith("/api/")` —— Makers 转发时 path 已剥离
        # root_path(/api), 实际为 "/dashboard/summary" → 条件恒假 → **请求驱动续跑从未触发**
        # (实证: cleansing_eval 任务 created 后 10 分钟 updated_at 不变, 只能等每天 05:05 的平台 cron)
        if _nowc - _LAST_CONT > 60:
            _LAST_CONT = _nowc
            import threading as _thc
            def _cont():
                try:
                    from routes.cron import continue_tasks
                    continue_tasks()
                except Exception as _e:
                    try_err('index', '请求驱动续跑线程异常', _e)
            _thc.Thread(target=_cont, daemon=True).start()
    except Exception as _e:
        try_err('index', '请求驱动续跑调度异常', _e)
    return await next(request)


@app.get("/health")
def health(detail: int = 0):
    """健康探针。detail=1 追加链路运行流水(前端 15s 轮询不带此参数, 零额外查询/RU)"""
    from datetime import datetime, timezone
    out = {"status": "ok", "db_backend": "tidb", "timestamp": datetime.now(timezone.utc).isoformat()}
    # 2026-10-15 保活顺带续跑(UptimeRobot 5min ping——有 running 任务才调 continue——无任务零开销——RU 不浪费)
    try:
        _run = one("SELECT COUNT(*) AS c FROM sync_tasks WHERE status='running' AND updated_at > DATE_SUB(NOW(), INTERVAL 30 MINUTE)")
        if _run and int(_run.get("c") or 0) > 0:
            from routes.cron import continue_tasks
            continue_tasks()
            out["continue"] = "ran"
    except Exception as _e:
        try_err('index', '保活顺带续跑降级', _e)
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
    # 数据版本指纹: 关键表 MAX(id)/MAX(date)/MAX(updated_at) 拼接, 任何数据变更即变化
    # (前端每 15s 轮询 /health 取 version, 变化时 clearCache+loadAll 绕过 30s 前端缓存)
    # 2026-10-29 修复: replenishment_config 主键是 (key, channel), **无 id 列** →
    #   原 SQL `MAX(id)` 抛 Unknown column 被吞 → version 恒 "0" → 前端指纹链路全失效。
    #   该表以 updated_at 表征变更(值改写会刷新时间戳)。
    try:
        r = one(
            "SELECT CONCAT_WS('-',"
            "(SELECT COALESCE(MAX(id),0) FROM orders),"
            "(SELECT COALESCE(MAX(id),0) FROM inventory),"  # no-barcode-ok: 仅版本指纹
            "(SELECT COALESCE(MAX(id),0) FROM products),"
            "(SELECT COALESCE(MAX(id),0) FROM alerts),"
            "(SELECT COALESCE(MAX(date),'') FROM daily_sales_snapshot),"
            "(SELECT COALESCE(MAX(id),0) FROM rules),"
            "(SELECT COALESCE(MAX(id),0) FROM suppliers),"
            "(SELECT COALESCE(MAX(updated_at),'') FROM replenishment_config)) AS v")
        # 计数版(2026-10-29): 任何走应用层的写操作都会递增 → UPDATE 也能被前端感知;
        # 后段保留表指纹(覆盖绕过应用层直接改库的"新增"场景)。前端只做字符串不等比较。
        _dvz = 0
        try:
            _dvr = one("SELECT v FROM data_version WHERE id=1") or {}
            _dvz = int(_dvr.get("v") or 0)
        except Exception as _edv:
            try_err('index', '数据版本读取降级', _edv)
        out["data_version"] = _dvz
        out["version"] = "%s|%s" % (_dvz, str((r or {}).get("v") or "0"))
    except Exception as _ev:
        # 静默降级=指纹失效且无人知(本次 bug 正是如此) → 必须留痕
        try_err('index', 'health 版本指纹降级', _ev)
        out["version"] = "0"
    # 2026-11-02 S1-2: 链路运行流水(仅 detail=1 时查 —— 默认路径保持轻量)
    # 只给 pipeline/status/耗时/行数, 不给 error 正文(本端点匿名可读, 防泄漏表名与 SQL 片段)
    if detail:
        try:
            from routes.common import last_runs
            out["last_runs"] = last_runs()
        except Exception as _el:
            out["last_runs"] = []
            try_err('index', 'health 运行流水降级', _el)
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
app.include_router(channels_router)
app.include_router(history_router)

# ── 启动 schema 初始化(2026-11-02 S2-8): 表/索引声明全部收敛到 schema_create.py, 版本门禁热路径零 DDL ──
if os.environ.get("DB_BACKEND", "tidb") == "tidb":
    try:
        # 2026-11-02 S2-8 版本门禁: schema_state 版本一致 → 热路径一条 SELECT、零 DDL;
        # 版本落后/首启 → 幂等全量(建表+索引+迁移), 全部成功才写版本。声明见 schema_create.py。
        from schema_create import ensure_schema
        ensure_schema()
    except Exception as _e:
            try_err('index', 'schema 初始化降级', _e)

if os.environ.get("DB_BACKEND", "tidb") == "tidb":
    try:
        # _exec: 迁移/补列仍走直连(2026-11-02 S2-8 拆块后补回)
        from db import execute as _exec
# ── 启动数据迁移(2026-09-15: barcode 补齐; 2026-10-10 移除 M2/M3 时间随机化——演示专用且实际场景有害)
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
                    # M1: 订单 69 码补齐(源头已解决: 导入时自动查补, 2026-09-15)
                    # 设计意图兼容: 原"不抢注+分批续跑"→ 补齐(或确认无空值)后才写标记;
                    # 标记后实例启动零扫描(不再每次全表 JOIN UPDATE 浪费 RU/竞争冷启动首请求)
                    if not _oneM("SELECT 1 FROM migration_log WHERE name='m1_69code'"):
                        _miss = _oneM("SELECT COUNT(*) AS c FROM orders o JOIN products p ON o.sku=p.sku "
                                      "WHERE (o.barcode IS NULL OR o.barcode='') "
                                      "AND p.barcode IS NOT NULL AND p.barcode != ''") or {}
                        if int(_miss.get("c") or 0) > 0:
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
                        _exec("INSERT INTO migration_log(name) VALUES('m1_69code')")
                except Exception as _e:  # 迁移整体降级(M0/M1 已各自兜底)
                        try_err('index', '迁移降级', _e)
            _thM.Thread(target=_run_migrations, daemon=True).start()
        except Exception as _e:  # 启动迁移整体降级(不阻塞后续补列/启动)
                try_err('index', '启动迁移降级', _e)
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
            # 2026-10-29 告警按 69码 去重(SKU×码 各自独立)
            if "barcode" not in _cols:
                _exec("ALTER TABLE alerts ADD COLUMN barcode VARCHAR(64) DEFAULT ''")
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

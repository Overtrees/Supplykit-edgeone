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
from routes.dashboard_bundle import router as dashboard_boot_router
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
from routes.ledger import router as ledger_router   # 2026-11-04 账期(月结): 状态/结账/补结/预演
from routes.common import verify_token, try_err
from core.clock import now_cn

app = FastAPI()

# 2026-11-07 P2: 响应压缩(JSON 体积大的是列表页: 自有仓一页 80KB / 断货一页 114KB)。
#   **实测这环境的瓶颈是出网带宽, 不是算力**: 同一接口 limit=1 只要 0.42s(纯往返),
#   limit=200 变 1.04s —— 差的 0.6s 全花在 80KB 明文 JSON 上(约 130KB/s)。
#   为什么不能用标准 GZipMiddleware: 线上实测边缘网关把 `Accept-Encoding` 整个剥掉再转发
#   (identity/gzip/none 三种请求, 响应头逐字节相同, 连 Vary 都恒等于 2 条) —— 应用层永远
#   看不到客户端是否接受压缩。所以约定一个显式头 `X-Accept-Encoding: gzip`(X- 自定义头
#   可透传, X-Auth-Token 就是这么过来的): 前端 axios 默认带上; 没带的调用方照旧收明文。
#   已压缩的文件类型(xlsx/csv/zip)直接跳过 —— 再压一遍只烧 CPU 不省字节。
#   回滚: GZIP_RESP=0 环境变量(按启动时读到的值注册, 不必发版)。
if str(os.environ.get("GZIP_RESP", "1")).strip().lower() not in ("0", "false", "off", "no"):
    _GZ_EXCLUDE = ("text/event-stream", "application/zip", "application/octet-stream",
                   "application/vnd.ms-excel",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    _GZ_MIN = 1024  # 小于 1KB 压缩反而更慢(头开销 > 省下的字节)

    class _XGZipMiddleware:
        """触发条件 = Accept-Encoding 或显式 X-Accept-Encoding 含 gzip。

        已压缩类型(content-encoding 已设 / xlsx / zip / SSE)与 <1KB 的响应跳过。
        JSONResponse 是单消息响应, 缓冲一整包再判大小, 无流式风险(SSE 已排除)。
        """

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            from starlette.datastructures import Headers as _H
            _h = _H(scope=scope)
            _acc = "%s,%s" % (_h.get("Accept-Encoding", ""), _h.get("X-Accept-Encoding", ""))
            if "gzip" not in _acc:
                return await self.app(scope, receive, send)
            return await self.app(scope, receive, self._make_send(send))

        def _make_send(self, send):
            import gzip as _gzip
            import io as _io
            _buf = _io.BytesIO()
            _gz = _gzip.GzipFile(mode="wb", fileobj=_buf, compresslevel=6)
            _state = {"start": None, "skip": False}

            async def _send(message):
                _t = message.get("type")
                if _t == "http.response.start":
                    from starlette.datastructures import Headers as _H2
                    _rh = _H2(raw=message.get("headers") or [])
                    _ct = _rh.get("content-type", "").split(";")[0].strip()
                    _state["skip"] = (_ct in _GZ_EXCLUDE or "content-encoding" in _rh)
                    _state["start"] = message
                    if _state["skip"]:
                        _gz.close()
                        await send(message)
                    return
                if _t != "http.response.body":
                    await send(message)
                    return
                if _state["skip"]:
                    await send(message)
                    return
                _body = message.get("body", b"") or b""
                _more = message.get("more_body", False)
                _gz.write(_body)
                if _more:
                    return          # 继续缓冲(SSE 已被排除, 正常 JSON 一包到底)
                _gz.close()
                _out = _buf.getvalue()
                if len(_body) < _GZ_MIN or len(_out) >= len(_body):
                    await send(_state["start"])
                    await send(message)
                    return
                from starlette.datastructures import MutableHeaders as _MH
                _msg = dict(_state["start"])
                _mh = _MH(raw=_msg["headers"])
                _mh["Content-Encoding"] = "gzip"
                _mh["Content-Length"] = str(len(_out))
                _mh.add_vary_header("Accept-Encoding")
                _body_msg = dict(message, body=_out)
                await send(_msg)
                await send(_body_msg)

            return _send

    app.add_middleware(_XGZipMiddleware)


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
    # 2026-11-06 S7/P1-24: /debug 只有显式打开调试(DEBUG_ENDPOINTS=1)时才放行 ——
    #   路由本身也只在那种情况下注册, 两处同一条开关, 避免"路由没了但前缀还放行"的错觉。
    _dbg = path.startswith("/debug") and os.environ.get("DEBUG_ENDPOINTS", "") == "1"
    if (path.startswith("/auth") or path == "/health" or _dbg
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
    """健康探针。detail=1 追加链路运行流水(默认路径零额外查询)

    2026-11-07 P0(固定税): 原来这个"只回 status:ok"的探针要发 **5 条 SQL** ——
      sync_tasks 续跑检查 / SELECT 1 / snapshot 最新日期 / 7 段版本指纹 / data_version。
      线上实测 1.0~1.9s, 而 Makers 每次请求都要重新建连(单次往返 0.2s 量级),
      这 5 趟就是整站每次请求的地板; 它还是前端每 30s 的轮询基座 → 白烧 RU 与延迟。
      合并成**一条** SQL(标量子查询): 一次建连、一次往返, 语义逐项等价。
    """
    from datetime import datetime, timezone
    out = {"status": "ok", "db_backend": "tidb", "timestamp": now_cn().isoformat()}
    try:
        r = one(
            "SELECT 1 AS ok,"
            "(SELECT COUNT(*) FROM sync_tasks WHERE status='running' "
            " AND updated_at > DATE_SUB(NOW(), INTERVAL 30 MINUTE)) AS run_c,"
            "(SELECT COALESCE(MAX(`date`),'') FROM daily_sales_snapshot) AS snap,"
            "(SELECT v FROM data_version WHERE id=1) AS dv,"
            "CONCAT_WS('-',"
            "(SELECT COALESCE(MAX(id),0) FROM orders),"
            "(SELECT COALESCE(MAX(id),0) FROM inventory),"
            "(SELECT COALESCE(MAX(id),0) FROM products),"
            "(SELECT COALESCE(MAX(id),0) FROM alerts),"
            "(SELECT COALESCE(MAX(`date`),'') FROM daily_sales_snapshot),"
            "(SELECT COALESCE(MAX(id),0) FROM rules),"
            "(SELECT COALESCE(MAX(id),0) FROM suppliers),"
            "(SELECT COALESCE(MAX(updated_at),'') FROM replenishment_config)) AS v") or {}
        out["db"] = "ok"
        out["snapshot_max"] = r.get("snap") or ""
        try:
            _dvz = int(r.get("dv") or 0)
        except Exception:
            _dvz = 0
        out["data_version"] = _dvz
        out["version"] = "%s|%s" % (_dvz, str(r.get("v") or "0"))
        # 保活顺带续跑(UptimeRobot 5min ping——有 running 任务才调 continue——无任务零开销)
        if int(r.get("run_c") or 0) > 0:
            from routes.cron import continue_tasks
            continue_tasks()
            out["continue"] = "ran"
    except Exception as _e:
        # 降级必须留痕(静默吞异常曾让 version 恒 0 而无人知)
        out["db"] = "error: %s" % str(_e)[:150]
        out["status"] = "degraded"
        out["version"] = "0"
        try_err("index", "health 探针降级", _e)
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
app.include_router(dashboard_boot_router)
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
app.include_router(ledger_router)

# ── 启动 schema 初始化(2026-11-02 S2-8): 表/索引声明全部收敛到 schema_create.py, 版本门禁热路径零 DDL ──
if os.environ.get("DB_BACKEND", "tidb") == "tidb":
    try:
        # 2026-11-02 S2-8 版本门禁: schema_state 版本一致 → 热路径一条 SELECT、零 DDL;
        # 版本落后/首启 → 幂等全量(建表+索引+迁移), 全部成功才写版本。声明见 schema_create.py。
        from schema_create import ensure_schema
        ensure_schema()
    except Exception as _e:
            try_err('index', 'schema 初始化降级', _e)

# 2026-11-02 S2-9(用户定案 方案B): 启动数据迁移(90天物化首建/订单69码补齐)与规则自愈
# (内置参数载体规则软删恢复、已退役规则存量清理)四段整体删除。理由: 线上实证(2026-11-02)
# 迁移标记均已写入、规则均处目标态 —— 四段使命完成, 每次冷启动 7 条探测 SQL 纯空转。
# 复用方法(新库/备份恢复/物化缺失)见 docs/DEVELOPMENT.md §15.123 第三节。

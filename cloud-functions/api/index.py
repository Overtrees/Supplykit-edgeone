"""Makers 入口 —— 挂载完整 backend(Phase2)

- vendor/app = backend/app 的构建时同步副本(edgeone.json build.command 生成)
- Makers 框架模式剥离 /api 前缀 → _ApiPrefixProxy 恢复后转发给 backend app
- backend 加载失败时 fallback 到最小 app(health/tidb-test 保持可用)
"""
import os
import sys

_vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
if _vendor not in sys.path:
    sys.path.insert(0, _vendor)

from fastapi import FastAPI

# Phase2 迁移工具(migrate 端点不经过 backend)
try:
    from migrate_tool import build as _mig_build, seed_small as _mig_seed, ru_test as _mig_ru, tables as _mig_tables
    _MIGRATE_OK = True
except Exception:
    _MIGRATE_OK = False

_migrate_app = FastAPI()


@_migrate_app.get("/migrate/build")
def _mb():
    try:
        return _mig_build()
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}


@_migrate_app.get("/migrate/seed")
def _ms(n_orders: int = 5000):
    try:
        return _mig_seed(n_orders=n_orders)
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}


@_migrate_app.get("/migrate/ru-test")
def _mr():
    try:
        return _mig_ru()
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}


@_migrate_app.get("/migrate/tables")
def _mt():
    try:
        return {"tables": _mig_tables()}
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}


class _ApiPrefixProxy:
    """恢复 /api 前缀后转发给真实 ASGI app(幂等: 已带 /api 的不再加); /migrate 走迁移工具"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") or ""
        if _MIGRATE_OK and path.startswith("/migrate"):
            await _migrate_app(scope, receive, send)
            return
        if not path.startswith("/api"):
            scope["path"] = "/api" + path
        await self.app(scope, receive, send)


try:
    # 完整 backend(main.py 内部含 JWT/自愈/init_db/scheduler)
    from app.main import app as _supplykit_app
    app = _ApiPrefixProxy(_supplykit_app)
except Exception as _be:
    # backend 加载失败 → fallback 最小 app, 保留诊断端点
    import logging
    logging.error("[entry] backend 加载失败: %s %s", type(_be).__name__, str(_be)[:300])

    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "degraded", "msg": "supplykit-edgeone (backend failed)", "error": str(_be)[:200]}

    @app.get("/tidb-test")
    def tidb_test():
        import pymysql
        out = {"env": {"host": bool(os.environ.get("TIDB_HOST")), "port": True,
                       "user": bool(os.environ.get("TIDB_USER")), "password": bool(os.environ.get("TIDB_PASSWORD"))}}
        try:
            conn = pymysql.connect(host=os.environ.get("TIDB_HOST"),
                                   port=int(os.environ.get("TIDB_PORT", "4000")),
                                   user=os.environ.get("TIDB_USER"),
                                   password=os.environ.get("TIDB_PASSWORD"),
                                   database=os.environ.get("TIDB_DB", "supplykit"),
                                   ssl={"ca": None}, connect_timeout=10)
            cur = conn.cursor()
            cur.execute("SELECT VERSION()")
            out["version"] = cur.fetchone()[0]
            cur.close(); conn.close()
            out["status"] = "OK"
        except Exception as e:
            out["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        return out

"""Makers 入口 —— 挂载完整 backend(Phase2)

- vendor/app = backend/app 的构建时同步副本(edgeone.json build.command 生成)
- Makers 框架模式剥离 /api 前缀 → _ApiPrefixProxy 恢复后转发给 backend app
- backend 加载失败时 fallback 到最小 app(health/tidb-test/migrate 保持可用)
- 注意: app 赋值必须在模块级行首(构建器正则 /^app\\s*=/m 检测函数入口)
"""
import os
import sys

_vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vendor")
# 多候选路径(不同运行环境 __file__/cwd 解析不同)
_cwd = os.getcwd()
_cands = [_vendor,
          os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"),
          os.path.join(_cwd, "vendor"),
          os.path.join(_cwd, "..", "vendor"),
          os.path.join(_cwd, "api", "..", "vendor")]
_vendor = next((p for p in _cands if os.path.isdir(os.path.join(p, "app"))), _vendor)
if _vendor not in sys.path:
    sys.path.insert(0, _vendor)
_VENDOR_INFO = {"path": _vendor, "has_app": os.path.isdir(os.path.join(_vendor, "app")),
                "app_files": sorted(os.listdir(os.path.join(_vendor, "app")))[:8] if os.path.isdir(os.path.join(_vendor, "app")) else []}

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
    """恢复 /api 前缀后转发给真实 ASGI app(幂等); /migrate 走迁移工具"""

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


def _make_fallback(error):
    """backend 加载失败时的最小 app(诊断用)"""
    import logging
    logging.error("[entry] backend 加载失败: %s %s", type(error).__name__, str(error)[:300])
    f = FastAPI()

    @f.get("/health")
    def health():
        return {"status": "degraded", "msg": "supplykit-edgeone (backend failed)",
                "error": str(error)[:200], "vendor": _VENDOR_INFO,
                "sys_path": [p for p in sys.path if 'vendor' in p or 'api' in p][:5]}

    @f.get("/tidb-test")
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
            cur.close()
            conn.close()
            out["status"] = "OK"
        except Exception as e:
            out["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        return out

    if _MIGRATE_OK:
        # migrate 端点并入 fallback app
        for r in _migrate_app.routes:
            f.routes.append(r)
    return f


# 组装 app(backend 优先, fallback 兜底)
try:
    from app.main import app as _supplykit_app
    _final_app = _ApiPrefixProxy(_supplykit_app)
except Exception as _be:
    _final_app = _make_fallback(_be)

app = _final_app

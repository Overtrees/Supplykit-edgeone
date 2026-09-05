"""EdgeOne Makers Cloud Functions 入口 —— SupplyKit 后端适配层

Makers 框架模式会剥离文件系统路由前缀(如 /api)后转发给框架。
后端路由均为 /api/*, 需在入口恢复前缀后再交给真实应用。
"""
import os, sys, pathlib

# 确保 backend 可导入
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_BACKEND_DIR = _PROJECT_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fastapi import FastAPI
from app.main import app as _supplykit_app


class _PrefixProxy:
    """ASGI 代理: 给请求路径加回 /api 前缀后转发给后端应用"""
    def __init__(self, target, prefix):
        self.target = target
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope)
            scope["path"] = self.prefix + scope["path"]
            scope["raw_path"] = scope["path"].encode()
        elif scope["type"] == "websocket":
            scope = dict(scope)
            scope["path"] = self.prefix + scope["path"]
        await self.target(scope, receive, send)


# 行首 app = 满足 Makers 入口检测(isFramework)
app = FastAPI()
app.mount("/", _PrefixProxy(_supplykit_app, "/api"))

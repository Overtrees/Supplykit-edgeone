"""EdgeOne Makers Cloud Functions 入口 —— SupplyKit 后端适配层

Makers 框架模式对前缀的处理有不确定性(剥离/不剥离/带root_path), 此入口做幂等适配:
- 路径若已带 /api 前缀则原样转发
- 若被剥离则补回 /api
"""
import os, sys, pathlib

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_BACKEND_DIR = _PROJECT_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fastapi import FastAPI
from app.main import app as _supplykit_app


class _ApiPrefixProxy:
    """幂等前缀代理: 保证转发给后端时路径带 /api"""
    def __init__(self, target):
        self.target = target

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            path = scope.get("path", "")
            # 从 root_path 判断 Makers 是否已挂载前缀
            rp = scope.get("root_path") or ""
            if not path.startswith("/api") and not rp.endswith("/api"):
                path = "/api" + path
            scope["path"] = path
            scope["raw_path"] = path.encode()
            # 让后端不要重复加 root_path
            if rp and rp.endswith("/api"):
                scope["root_path"] = rp[:-4]
        await self.target(scope, receive, send)


app = FastAPI()
app.mount("/", _ApiPrefixProxy(_supplykit_app))

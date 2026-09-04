"""EdgeOne Makers Cloud Functions 入口 —— SupplyKit 后端适配层

Makers 入口标识要求（官方文档 + 构建器正则）: 文件中必须存在行首 `app =` 赋值。
此入口导入 backend 真实应用并重新赋值给 app，满足检测同时复用完整后端。
"""
import os, sys, pathlib

# 确保 backend 可导入: Makers 构建会把 includeFiles 指定的 backend/app/** 放进工作区
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_BACKEND_DIR = _PROJECT_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# 导入真实后端应用（行首 app = 满足 Makers 入口检测）
from app.main import app as _supplykit_app

app = _supplykit_app

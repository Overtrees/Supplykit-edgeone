"""EdgeOne Makers Cloud Functions 入口 —— SupplyKit 后端适配层
Makers 约定: cloud-functions/ 目录下含 app=FastAPI(...) 的文件自动注册为路由。
此入口从 backend 包导入真实应用。构建时通过 edgeone.json 的 includeFiles 携带 backend/。
"""
import os, sys, pathlib

# 确保 backend 可导入: Makers 构建会把 includeFiles 指定的目录放进工作区
# 项目根 = cloud-functions/api/index.py 向上 3 级
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_BACKEND_DIR = _PROJECT_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# FastAPI 应用(Makers 识别 app = FastAPI(...) 入口标识)
from app.main import app  # noqa: E402

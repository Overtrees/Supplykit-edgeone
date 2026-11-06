"""原生 auth 路由: setup/login/check(契约与旧 backend 一致)"""
import os

from fastapi import APIRouter
from fastapi import Request

from db import one, execute, table
from routes.common import fail, create_token, verify_token, hash_password, check_password, traced, try_err

router = APIRouter(tags=["auth"])


@router.post("/auth/setup")
@traced
async def setup(request: Request):
    """首次设置管理员密码(兼容 query/body 两种传参)"""
    data = {}
    try:
        data = await request.json()
    except Exception as _e:
            try_err('auth', '静默降级', _e)
    username = data.get("username") or request.query_params.get("username", "")
    password = data.get("password") or request.query_params.get("password", "")
    # 2026-11-06 S7/P1-24: 本端点在中间件里是放行的(/auth 前缀), 不校验的话谁先调谁就能
    #   建一个 admin 账号 —— 首次部署的空窗等于"公开抢注"。改为必须带 SETUP_TOKEN:
    #   · 配了环境变量 → 校验 X-Setup-Token(或 body/query 的 setup_token), 用 compare_digest
    #   · 没配 → 生产(DB_BACKEND=tidb)一律拒绝; 只有本地 sqlite 允许(引导用, 无外部暴露)
    import hmac as _hm
    _st = os.environ.get("SETUP_TOKEN", "")
    if _st:
        _got = (request.headers.get("X-Setup-Token", "")
                or str(data.get("setup_token") or "")
                or request.query_params.get("setup_token", ""))
        if not (_got and _hm.compare_digest(_got.encode(), _st.encode())):
            return fail("初始化令牌缺失或错误", 403)
    elif os.environ.get("DB_BACKEND", "tidb") == "tidb":
        return fail("初始化已禁用(未配置 SETUP_TOKEN)", 403)
    if not username or not password:
        return fail("请输入用户名和密码")
    if len(password) < 6:
        return fail("密码至少 6 位")
    existing = table("users").all("username=%s", [username], limit=1)
    if existing:
        return fail("用户已存在")
    execute("INSERT INTO users(username, password_hash, role) VALUES(%s,%s,%s)",
            (username, hash_password(password), "admin"))
    token = create_token(username)
    return {"ok": True, "token": token, "user": username, "role": "admin"}


@router.post("/auth/login")
@traced
async def login(request: Request):
    data = {}
    try:
        data = await request.json()
    except Exception as _e:
            try_err('auth', '静默降级', _e)
    username = data.get("username") or request.query_params.get("username", "")
    password = data.get("password") or request.query_params.get("password", "")
    if not username or not password:
        return fail("请输入用户名和密码")
    # demo 访客(只读)内置
    if username == "demo" and password == "demo123":
        return {"ok": True, "token": create_token("demo"), "user": "demo", "role": "viewer"}
    row = one("SELECT username, password_hash, role FROM users WHERE username=%s", [username])
    if not row or not check_password(password, row.get("password_hash") or ""):
        return fail("用户名或密码错误", 401)
    return {"ok": True, "token": create_token(username), "user": username, "role": row.get("role") or "user"}


@router.get("/auth/check")
@traced
def check_auth(request: Request):
    token = request.headers.get("X-Auth-Token", "")
    user = verify_token(token) if token else None
    if not user:
        return fail("未登录", 401)
    return {"ok": True, "user": user}


def require_user(request: Request):
    """中间件用: 返回 username 或 None"""
    token = request.headers.get("X-Auth-Token", "")
    return verify_token(token) if token else None

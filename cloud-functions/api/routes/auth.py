"""原生 auth 路由: setup/login/check(契约与旧 backend 一致)"""
from fastapi import APIRouter
from fastapi import Request

from db import query, one, execute, table
from routes.common import ok, fail, create_token, verify_token, hash_password, check_password, traced

router = APIRouter(tags=["auth"])


@router.post("/auth/setup")
@traced
async def setup(request: Request):
    """首次设置管理员密码(兼容 query/body 两种传参)"""
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    username = data.get("username") or request.query_params.get("username", "")
    password = data.get("password") or request.query_params.get("password", "")
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
    return ok({"token": token, "user": username, "role": "admin"})


@router.post("/auth/login")
@traced
async def login(request: Request):
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    username = data.get("username") or request.query_params.get("username", "")
    password = data.get("password") or request.query_params.get("password", "")
    if not username or not password:
        return fail("请输入用户名和密码")
    # demo 访客(只读)内置
    if username == "demo" and password == "demo123":
        return ok({"token": create_token("demo"), "user": "demo", "role": "viewer"})
    row = one("SELECT username, password_hash, role FROM users WHERE username=%s", [username])
    if not row or not check_password(password, row.get("password_hash") or ""):
        return fail("用户名或密码错误", 401)
    return ok({"token": create_token(username), "user": username, "role": row.get("role") or "user"})


@router.get("/auth/check")
@traced
def check_auth(request: Request):
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    user = verify_token(token) if token else None
    if not user:
        return fail("未登录", 401)
    return ok({"user": user})


def require_user(request: Request):
    """中间件用: 返回 username 或 None"""
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    return verify_token(token) if token else None

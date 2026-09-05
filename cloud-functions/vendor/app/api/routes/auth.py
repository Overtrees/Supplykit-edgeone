"""认证路由 — 登录/注册/验证"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from app.core.auth import create_token, verify_token, check_password, hash_password, _verify_hash, PASSWORD_HASH
from app.core.database import get_db
import os

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(body: dict):
    """登录：校验密码，返回 JWT token"""
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    if not username or not password:
        raise HTTPException(400, "用户名和密码不能为空")
    # 环境变量模式（单用户）
    if check_password(password):
        token = create_token(username)
        return {"ok": True, "token": token, "user": username}
    # 数据库模式（多用户，未来扩展）
    try:
        db = get_db()
        users = db.table("users").select("*").eq("username", username).execute().data
        if users and _verify_hash(password, users[0].get("password_hash", "")):
            token = create_token(username)
            return {"ok": True, "token": token, "user": username}
    except Exception:
        pass
    raise HTTPException(401, "用户名或密码错误")


@router.post("/setup")
def setup(body: dict):
    """首次设置密码（存 users 表，立即生效）"""
    password = body.get("password", "").strip()
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    # 检查是否已设置（环境变量或数据库）
    if PASSWORD_HASH:
        raise HTTPException(400, "环境变量已设密码，请直接登录或清除 APP_PASSWORD_HASH 后重试")
    db = get_db()
    existing = db.table("users").select("*").execute().data
    if existing:
        raise HTTPException(400, "已有用户，请直接登录")
    # 创建管理员用户
    pwd_hash = hash_password(password)
    db.table("users").insert({"username": body.get("username", "admin"), "password_hash": pwd_hash, "role": "admin"}).execute()
    # 创建演示账号（只读，用于在线体验）
    demo_pwd = hash_password("demo123")
    try:
        db.table("users").insert({"username": "demo", "password_hash": demo_pwd, "role": "demo"}).execute()
    except Exception:
        pass
    token = create_token(body.get("username", "admin"))
    return {"ok": True, "token": token, "user": body.get("username", "admin"), "demo": "访客模式: demo / demo123"}


@router.get("/check")
def check_auth(request: Request):
    """验证当前 token 是否有效"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401)
    user = verify_token(auth[7:])
    if not user:
        raise HTTPException(401)
    return {"ok": True, "user": user}


async def require_auth(request: Request):
    """FastAPI Depends：校验请求身份，返回用户信息"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, detail="未登录")
    token = auth[7:]
    user = verify_token(token)
    if not user:
        raise HTTPException(401, detail="Token 无效或已过期")
    # 查用户角色（从数据库）
    role = "user"
    try:
        db = get_db()
        u = db.table("users").select("*").eq("username", user).execute().data
        if u:
            role = u[0].get("role", "user")
    except Exception:
        pass
    request.state.user = user
    request.state.role = role
    return user


async def require_write(request: Request):
    """FastAPI Depends：写操作权限（demo 账号只读）"""
    role = getattr(request.state, "role", "user")
    if role == "demo":
        raise HTTPException(403, detail="访客模式仅可查看，不可修改数据")
    return user
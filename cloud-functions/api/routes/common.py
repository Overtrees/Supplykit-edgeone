"""通用工具: 统一响应 + JWT + 密码哈希(与旧 backend 兼容)"""
import base64
import hashlib
import hmac
import json
import os
import time

PAID_STATUSES = ("待发货", "已发货", "已完成", "申请退款")


def ok(data):
    return {"ok": True, "data": data}


def fail(msg, status=400):
    return {"ok": False, "error": msg}


# ── JWT (HS256, 零依赖) ──────────────────────────────────────────────
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def jwt_secret():
    return os.environ.get("JWT_SECRET", "")


def create_token(username: str, expire_hours: int = 720) -> str:
    secret = jwt_secret()
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({
        "sub": username, "iat": int(time.time()),
        "exp": int(time.time()) + expire_hours * 3600}).encode())
    sig = _b64(hmac.new(secret.encode(), ("%s.%s" % (header, payload)).encode(), hashlib.sha256).digest())
    return "%s.%s.%s" % (header, payload, sig)


def verify_token(token: str):
    try:
        secret = jwt_secret()
        if not secret:
            return None
        parts = token.split(".")
        if len(parts) != 3:
            return None
        expected = _b64(hmac.new(secret.encode(), ("%s.%s" % (parts[0], parts[1])).encode(),
                                 hashlib.sha256).digest())
        if parts[2] != expected:
            return None
        payload = json.loads(_b64decode(parts[1]))
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("sub")
    except Exception:
        return None


# ── 密码哈希 (PBKDF2-HMAC-SHA256, salt:key, 100k 迭代——兼容旧 backend) ──
def hash_password(password: str) -> str:
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return salt.hex() + ":" + key.hex()


def check_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    if ":" not in stored:
        return hashlib.sha256(password.encode("utf-8")).hexdigest() == stored
    try:
        salt_hex, key_hex = stored.split(":")
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                  bytes.fromhex(salt_hex), 100000)
        return hmac.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


# ── 统一异常追踪装饰器(所有路由自动捕获返回 detail+tb, 状态 200 防 Makers 转页) ──
def traced(handler):
    """包装 FastAPI 路由: 异常返回 {ok:False, error, detail, tb}(200 状态避免 Makers 500 转页)"""
    from functools import wraps
    import traceback as _tb
    import asyncio as _ai

    def _err(e):
        return {"ok": False, "error": "handler-error",
                "detail": "%s: %s" % (type(e).__name__, str(e)[:400]),
                "tb": _tb.format_exc(limit=15)[-2000:]}

    if _ai.iscoroutinefunction(handler):
        @wraps(handler)
        async def _awrap(*args, **kwargs):
            try:
                return await handler(*args, **kwargs)
            except Exception as e:
                return _err(e)
        return _awrap

    @wraps(handler)
    def _wrap(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except Exception as e:
            return _err(e)
    return _wrap

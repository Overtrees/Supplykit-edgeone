"""通用工具: 统一响应 + JWT + 密码哈希(与旧 backend 兼容)"""
import base64
import hashlib
import hmac
import json
import os
import time

# GMV 总收入口径(含申请退款流水, net 再扣)——看板 GMV/趋势/漏斗
PAID_STATUSES = ("待发货", "已发货", "已完成", "申请退款")
# 销量池(采购补货的日均实销分母): 仅"已完成"(钱货两清且物理离开仓库=真实消耗库存)
# (待发货锁定扣减/ERP 级联动暂不做——库存/在途以清洗页导入数据为准, 2026-09-09 用户决策)
SALES_STATUSES = ("已完成",)


def ok(data):
    return {"ok": True, "data": data}


def fail(msg, status=400):
    return {"ok": False, "error": msg}


_TE_STATE = {}
_TE_WINDOW = 10.0


def try_err(src, what, exc=None, details=""):
    """静默吞异常纪律(2026-09-11 体检): except 分支统一自记 quality_logs 留痕(不阻断降级流程)
    —— 2026-10-07: details 缺省带 traceback 堆栈(limit 3); 同 source 10s 窗口限频+计数合并:
      窗口内首条写库(留痕), 窗口结束补写计数条(×N 次)——防风暴刷屏且不丢异常规模; 自身 except 必须 pass(防递归)"""
    def _write(_w, _c, _e, _d):
        try:
            from db import execute as _e2
            _msg = _w
            if _e is not None:
                _msg += ": %s: %s" % (type(_e).__name__, str(_e)[:200])
            if _c > 1:
                _msg += " · 同源10s内 ×%d 次" % _c
            _det = _d or ""
            if not _det:
                try:
                    import traceback as _tb
                    _det = _tb.format_exc(limit=3)[:300]
                except Exception:
                    _det = ""
            _e2("INSERT INTO quality_logs(log_type, level, message, details, source) "
                "VALUES('quiet_error','warning',%s,%s,%s)",
                (_msg[:200], _det[:300], src))
        except Exception:
            pass  # 写日志失败自吞(防递归)——设计保留

    try:
        import time as _t
        _now = _t.time()
        _st = _TE_STATE.setdefault(src, {"ts": 0.0, "count": 0, "what": "", "exc": None, "det": ""})
        if _now - _st["ts"] >= _TE_WINDOW and _st["count"] > 0:
            # 上窗口未补计数 → 先补写计数条(风暴规模留痕)
            _write(_st["what"], _st["count"] + 1, _st["exc"], _st["det"])
            _st["count"] = 0
        if _now - _st["ts"] < _TE_WINDOW:
            # 窗口内: 合并计数(首条信息保留, 不逐条写)
            _st["count"] += 1
            if not _st["what"]:
                _st["what"], _st["exc"], _st["det"] = what, exc, details or ""
            return
        # 窗口首条: 写库留痕
        _write(what, 1, exc, details)
        _st.update(ts=_now, count=0, what="", exc=None, det="")
    except Exception:
        pass  # 自身异常自吞(防递归)


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
        try:
            from db import execute as _e
            _e("INSERT INTO quality_logs(log_type, level, message, details, source) "
               "VALUES(%s,%s,%s,%s,%s)",
               ("api_error", "error", ("%s: %s" % (type(e).__name__, str(e)[:300])),
                _tb.format_exc(limit=15)[-1800:], "api"))
        except Exception as _e:
                try_err('common', '静默降级', _e)
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

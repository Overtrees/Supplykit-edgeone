"""JWT 认证工具 — 纯标准库 HMAC-SHA256，零第三方依赖"""
import hmac, hashlib, base64, json, time, os
from typing import Optional

# 环境变量配置
SECRET = os.getenv("JWT_SECRET", "")
USERNAME = os.getenv("APP_USERNAME", "admin")
# 密码 sha256 哈希（如未设环境变量，默认空字符串不允许登录）
PASSWORD_HASH = os.getenv("APP_PASSWORD_HASH", "")

# 未来多用户：从数据库 users 表读取
# 当前单用户：从环境变量校验


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _b64decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += '=' * padding
    return base64.urlsafe_b64decode(s)


def _current_secret():
    """动态获取 JWT secret：优先环境变量（main.py 启动时从数据库加载/生成后设置）"""
    s = os.getenv("JWT_SECRET", "")
    if s:
        return s
    return SECRET


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 哈希：salt:key 格式（32 字节随机 salt，100k 迭代）

    与 README 声明的"PBKDF2 + 100k 迭代"一致，防彩虹表攻击。
    """
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt.hex() + ':' + key.hex()


def _verify_hash(password: str, stored: str) -> bool:
    """校验密码哈希，兼容两种格式：
    - 含 ':' → PBKDF2 (salt:key)
    - 无 ':' → 旧版裸 SHA256（历史数据兼容）
    """
    if not stored:
        return False
    if ':' not in stored:
        return hashlib.sha256(password.encode('utf-8')).hexdigest() == stored
    try:
        salt_hex, key_hex = stored.split(':')
        salt = bytes.fromhex(salt_hex)
        key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        return key.hex() == key_hex
    except Exception:
        return False


def create_token(username: str, expire_hours: int = 720) -> str:
    """生成 HS256 JWT，默认 30 天过期"""
    secret = _current_secret() or (os.urandom(32).hex() if not _current_secret() else _current_secret())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({
        "sub": username, "iat": int(time.time()),
        "exp": int(time.time()) + expire_hours * 3600
    }).encode())
    sig = _b64(hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def verify_token(token: str) -> Optional[str]:
    """验证 JWT，返回 username 或 None"""
    try:
        secret = _current_secret()
        if not secret:
            return None
        parts = token.split('.')
        if len(parts) != 3:
            return None
        # 验证签名
        expected = _b64(hmac.new(secret.encode(), f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest())
        if parts[2] != expected:
            return None
        # 解析 payload
        payload = json.loads(_b64decode(parts[1]))
        if payload.get('exp', 0) < time.time():
            return None  # 过期
        return payload.get('sub')
    except Exception:
        return None


def check_password(password: str) -> bool:
    """校验密码（环境变量模式，兼容新旧哈希格式）"""
    if not PASSWORD_HASH:
        return False
    return _verify_hash(password, PASSWORD_HASH)
"""统一调试模块(2026-10-13 收口): /debug/* 前缀放行(鉴权白名单)——集中项目调试/探测端点
用法: 临时排查加端点到此文件(带 @traced + 注释说明用途); /debug/list 列出全部
注意: 生产请勿新增敏感信息回显端点; 排查完成保留(调试价值)
"""
from fastapi import APIRouter, Request

from routes.common import ok, traced

router = APIRouter(tags=["debug"])


@router.get("/debug/list")
@traced
def debug_list():
    """调试端点清单(统一收口——定位入口)"""
    items = []
    for r in router.routes:
        methods = ",".join(sorted(r.methods or []))
        p = getattr(r, "path", "")
        doc = (r.endpoint.__doc__ or "").strip().split("\n")[0] if getattr(r, "endpoint", None) else ""
        items.append({"path": p, "methods": methods, "desc": doc[:60]})
    return ok({"debug_endpoints": items})


@router.get("/debug/jwt-check")
@traced
def jwt_check(token: str = ""):
    """JWT 校验探测: 给定 token 回显 verify_token 结果(401 排查用)"""
    from routes.common import verify_token, jwt_secret
    if not token:
        return ok({"fail": "no-token"})
    try:
        user = verify_token(token)
        # 2026-11-06 S7/P1-24: 不再回显 secret 长度/指纹 —— 那是给爆破者做"对不对"的
        #   判定信号(长度对了说明配了密钥、指纹一致说明多实例共用同一份)。只回结论。
        return ok({"verify_user": user, "secret_set": bool(jwt_secret())})
    except Exception as e:
        return ok({"fail": "err:" + str(e)[:60]})


@router.get("/debug/auth-sim")
@traced
def auth_sim(request: Request):
    """模拟 auth 中间件: 从 Authorization header 取 Bearer → auth[7:] → verify_token(网关改写排查)"""
    from routes.common import verify_token
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return ok({"fail": "no-bearer", "auth_head": auth[:12]})
    user = verify_token(auth[7:])
    return ok({"user": user, "auth_len": len(auth)})


@router.get("/debug/jwt-fingerprint")
@traced
def jwt_fingerprint():
    """当前实例是否配了 JWT_SECRET(2026-11-06 S7: 去掉指纹与长度回显)

    指纹虽然不可逆, 但它对"多实例是否共用同一份密钥"给出的是**可被外部观测的等价判断**,
    对排查的收益远小于泄给攻击者的信息。真需要比对时, 直接看两个实例各自环境变量即可。
    """
    from routes.common import jwt_secret as _js
    return ok({"secret_set": bool(_js())})

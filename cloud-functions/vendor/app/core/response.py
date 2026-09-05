"""统一 API 响应格式

所有接口返回 {ok, data, error} 三字段结构：
  {"ok": true, "data": [...]}       # 成功
  {"ok": false, "error": "消息"}     # 失败
"""
from fastapi.responses import JSONResponse
from typing import Any, Optional


def ok(data: Any = None, message: str = "") -> dict:
    """成功响应"""
    r = {"ok": True, "data": data}
    if message:
        r["message"] = message
    return r


def fail(error: str, status: int = 400) -> JSONResponse:
    """失败响应"""
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": error},
    )


def server_error(error: str = "服务器内部错误") -> JSONResponse:
    """500 服务器错误"""
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": error},
    )
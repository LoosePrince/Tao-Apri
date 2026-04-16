from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Response

from app.core.admin_auth_service import admin_auth_service
from app.core.config import settings
from app.core.qq_qr_login_service import QQQRCodeLoginService


router = APIRouter()


@router.get("/admin/auth/qq/start")
def qq_start() -> Dict[str, Any]:
    """
    返回 QQ 二维码登录所需：
    - code：轮询用的 ticket code
    - qrUrl：二维码图片/页面地址（前端直接渲染 img）
    - allowedUin：后端校验允许的 debug 账号（必须一致）
    """
    try:
        payload = QQQRCodeLoginService.request_login_code()
        return {
            "ok": True,
            "code": payload.get("code", ""),
            "qrUrl": payload.get("qrUrl", ""),
            "allowedUin": str(settings.onebot.debug_only_user_id).strip(),
        }
    except Exception as exc:
        return {"ok": False, "msg": str(exc)}


@router.get("/admin/auth/qq/status")
def qq_status(code: str, response: Response) -> Dict[str, Any]:
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    status = QQQRCodeLoginService.query_status(code)
    state = status.get("state")
    if state == "ok":
        uin = status.get("uin", "")
        token = admin_auth_service.issue_token_for_uin(uin)
        if not token:
            return {"state": "error", "msg": "uin mismatch"}

        # 登录成功：设置 cookie，后续 /admin/* 通过鉴权
        response.set_cookie(
            key=admin_auth_service.COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            max_age=int(600),
        )
        return {"state": "ok", "uin": uin}

    return status


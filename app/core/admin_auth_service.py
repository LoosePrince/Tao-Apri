from __future__ import annotations

from dataclasses import dataclass
from secrets import token_urlsafe
import threading
import time

from app.core.config import settings


@dataclass(frozen=True, slots=True)
class AdminToken:
    token: str
    uin: str
    expires_at: float


class AdminAuthService:
    """
    管理后台简单鉴权：
    - 成功登录后发放短期 cookie（HttpOnly）
    - 仅允许使用 settings.onebot.debug_only_user_id 对应的 QQ 账号
    """

    COOKIE_NAME = "admin_session"

    def __init__(self, *, ttl_seconds: float = 600.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._tokens: dict[str, AdminToken] = {}

    def allowed_uin(self) -> str:
        return str(settings.onebot.debug_only_user_id).strip()

    def issue_token_for_uin(self, uin: str) -> str | None:
        uin = str(uin).strip()
        if not uin:
            return None
        if uin != self.allowed_uin():
            return None

        token = token_urlsafe(32)
        expires_at = time.time() + self._ttl_seconds
        admin_token = AdminToken(token=token, uin=uin, expires_at=expires_at)
        with self._lock:
            self._tokens[token] = admin_token
        return token

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        token = str(token).strip()
        now = time.time()
        with self._lock:
            admin_token = self._tokens.get(token)
            if not admin_token:
                return False
            if admin_token.expires_at <= now:
                self._tokens.pop(token, None)
                return False
            return True

    def cleanup_expired(self) -> None:
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._tokens.items() if v.expires_at <= now]
            for k in expired:
                self._tokens.pop(k, None)


admin_auth_service = AdminAuthService()


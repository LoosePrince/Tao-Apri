from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.core.admin_auth_service import admin_auth_service
from app.core.container import container
from app.main import app


def _snapshot_settings() -> dict:
    return settings.model_dump()


def _restore_settings(snapshot: dict) -> None:
    original = Settings.model_validate(snapshot)
    container.apply_runtime_settings(original)


client = TestClient(app)

def _ensure_admin_cookie() -> None:
    token = admin_auth_service.issue_token_for_uin(str(settings.onebot.debug_only_user_id))
    assert token, "failed to issue admin token for debug account"
    client.cookies.set(admin_auth_service.COOKIE_NAME, token)


def test_admin_runtime_config_read() -> None:
    snap = _snapshot_settings()
    try:
        _ensure_admin_cookie()
        r = client.get("/admin/runtime-config")
        assert r.status_code == 200
        d = r.json()
        assert "config" in d
        assert "fields" in d
    finally:
        _restore_settings(snap)


def test_admin_runtime_config_validate_rejects_storage_update() -> None:
    snap = _snapshot_settings()
    try:
        _ensure_admin_cookie()
        r = client.post("/admin/runtime-config/validate", json={"updates": {"storage": {"sqlite_db_path": "x.db"}}})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False
        assert d["errors"]
    finally:
        _restore_settings(snap)


def test_admin_runtime_config_apply_emotion_decay_rebuilds() -> None:
    snap = _snapshot_settings()
    old_engine_id = id(container.emotion_engine)
    try:
        _ensure_admin_cookie()
        cur = settings.emotion.decay
        new_decay = cur + 0.01 if cur <= 0.99 else max(0.0, cur - 0.01)
        r = client.post("/admin/runtime-config/apply", json={"updates": {"emotion": {"decay": new_decay}}})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert id(container.emotion_engine) != old_engine_id
    finally:
        _restore_settings(snap)


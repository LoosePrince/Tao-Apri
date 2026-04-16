from __future__ import annotations

from app.core.config import Settings, settings
from app.core.container import container
from app.core.runtime_config import RuntimeConfigManager


def _snapshot_settings() -> dict:
    return settings.model_dump()


def _restore_settings(snapshot: dict) -> None:
    original = Settings.model_validate(snapshot)
    container.apply_runtime_settings(original)


def test_runtime_config_export_env_contains_expected_keys() -> None:
    mgr = RuntimeConfigManager()
    snap = _snapshot_settings()
    try:
        env_text = mgr.export_env_text(Settings.model_validate(snap))
        assert "APP__NAME=" in env_text
        assert "RHYTHM__ENABLED=" in env_text
    finally:
        _restore_settings(snap)


def test_runtime_config_validate_rejects_storage_update() -> None:
    mgr = RuntimeConfigManager()
    new_settings, errors = mgr.validate_update({"storage": {"sqlite_db_path": "x.db"}})
    del new_settings
    assert errors
    assert any("storage.sqlite_db_path" in e for e in errors)


def test_container_apply_emotion_rebuilds_emotion_engine() -> None:
    snap = _snapshot_settings()
    old_engine_id = id(container.emotion_engine)
    try:
        cur = settings.emotion.decay
        new_decay = cur + 0.01 if cur <= 0.99 else max(0.0, cur - 0.01)
        new_settings = Settings.model_validate({**snap, "emotion": {**snap["emotion"], "decay": new_decay}})
        container.apply_runtime_settings(new_settings)
        assert id(container.emotion_engine) != old_engine_id
    finally:
        _restore_settings(snap)


def test_container_apply_llm_rebuilds_llm_client() -> None:
    snap = _snapshot_settings()
    old_llm_id = id(container.llm_client)
    try:
        new_api_key = (settings.llm.api_key or "") + "_test"
        new_settings = Settings.model_validate({**snap, "llm": {**snap["llm"], "api_key": new_api_key}})
        container.apply_runtime_settings(new_settings)
        assert id(container.llm_client) != old_llm_id
    finally:
        _restore_settings(snap)


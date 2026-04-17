from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.container import container
from app.core.config import settings
from app.core.admin_auth_service import admin_auth_service
from app.core.runtime_config import RuntimeConfigManager
from app.core.onebot_service import OneBotService


router = APIRouter()
runtime_config = RuntimeConfigManager()


class RuntimeConfigUpdatePayload(BaseModel):
    updates: dict[str, Any]


def _require_admin_access(request: Request) -> None:
    token = request.cookies.get(admin_auth_service.COOKIE_NAME)
    if not admin_auth_service.validate(token):
        raise HTTPException(status_code=403, detail="Admin auth required")


def _extract_updated_paths(updates: dict[str, Any]) -> set[str]:
    paths: set[str] = set()

    def walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{prefix}.{k}" if prefix else k)
        else:
            paths.add(prefix)

    walk(updates, "")
    return paths


@router.get("/admin/runtime-config")
def get_runtime_config(request: Request) -> dict[str, Any]:
    _require_admin_access(request)
    return runtime_config.get_runtime_config()


@router.post("/admin/runtime-config/validate")
def validate_runtime_config(request: Request, payload: RuntimeConfigUpdatePayload) -> dict[str, Any]:
    _require_admin_access(request)
    new_settings, errors = runtime_config.validate_update(payload.updates)
    return {
        "ok": not errors,
        "errors": errors,
        "applied_preview": new_settings.model_dump(),
    }


@router.post("/admin/runtime-config/apply")
async def apply_runtime_config(request: Request, payload: RuntimeConfigUpdatePayload) -> dict[str, Any]:
    _require_admin_access(request)
    new_settings, errors = runtime_config.validate_update(payload.updates)
    if errors:
        return {"ok": False, "errors": errors}

    # Apply internal components.
    result = container.apply_runtime_settings(new_settings)

    # Restart OneBot if connection fields changed.
    updated_paths = _extract_updated_paths(payload.updates)
    need_onebot_restart = any(p.startswith("onebot.") for p in updated_paths)

    onebot_service = getattr(request.app.state, "onebot_service", None)
    if isinstance(onebot_service, OneBotService) and need_onebot_restart:
        reply_lookup = getattr(container.message_repo, "get_latest_text_by_source_message_id", None)
        await onebot_service.restart(
            window_manager=container.window_manager,
            reply_message_lookup=reply_lookup if callable(reply_lookup) else None,
        )

    return {
        "ok": True,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "need_onebot_restart": need_onebot_restart,
        "updated_paths": sorted(updated_paths),
    }


@router.post("/admin/runtime-config/export")
def export_runtime_config(request: Request) -> dict[str, Any]:
    _require_admin_access(request)
    env_text = runtime_config.export_env_text()
    return {"ok": True, "content": env_text}


@router.get("/admin/runtime-status")
def runtime_status(request: Request) -> dict[str, Any]:
    _require_admin_access(request)

    models = container.llm_client.list_available_models()
    return {
        "ok": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "health": "ok",
        "metrics": container.metrics.snapshot(),
        "llm": {
            "provider": settings.llm.provider,
            "base_url": settings.llm.base_url,
            "configured_model": settings.llm.model,
            "api_key_configured": bool(settings.llm.api_key),
            "models": models,
        },
        "onebot": {
            "enabled": settings.onebot.enabled,
            "ws_url": settings.onebot.ws_url,
            "debug_only_user_id": settings.onebot.debug_only_user_id,
        },
    }


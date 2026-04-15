from fastapi import APIRouter

from app.core.container import container
from app.core.config import settings

router = APIRouter()


@router.get("/models")
def list_models() -> dict[str, object]:
    models = container.llm_client.list_available_models()
    return {
        "provider": settings.llm.provider,
        "base_url": settings.llm.base_url,
        "configured_model": settings.llm.model,
        "debug_mode": settings.app.debug,
        "api_key_configured": bool(settings.llm.api_key),
        "models": models,
    }

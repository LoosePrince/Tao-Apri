from fastapi import APIRouter

from app.core.container import container

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/metrics")
def metrics() -> dict[str, float | int]:
    return container.metrics.snapshot()

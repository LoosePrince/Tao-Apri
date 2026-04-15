from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging

from fastapi import FastAPI

from app.api.routes.chat import router as chat_router
from app.api.routes.health import router as health_router
from app.api.routes.llm import router as llm_router
from app.api.routes.session import router as session_router
from app.core.container import container
from app.core.clock import now_local_with_source
from app.core.config import settings
from app.core.logging import setup_logging
from app.integrations.onebot_ws_client import OneBotWSClient

setup_logging()
logger = logging.getLogger(__name__)


def _mask_secret(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 6:
        return "***"
    return value[:3] + "***" + value[-3:]


def _log_startup_diagnostics() -> None:
    local_now, time_source = now_local_with_source()
    utc_now = datetime.now(timezone.utc)
    logger.info("=== Startup Diagnostics ===")
    logger.info("App Name: %s", settings.app.name)
    logger.info("Environment: %s | Debug: %s", settings.app.env, settings.app.debug)
    logger.info("Timezone Config: %s", settings.app.timezone)
    logger.info("Timezone Source: %s", time_source)
    logger.info("Program Local Time: %s", local_now.isoformat())
    logger.info("Program UTC Time: %s", utc_now.isoformat())
    logger.info("Local UTC Offset: %s", local_now.strftime("%z"))
    logger.info("SQLite DB Path: %s", settings.storage.sqlite_db_path)
    logger.info(
        "LLM Provider: %s | Model: %s | Base URL: %s | Key: %s",
        settings.llm.provider,
        settings.llm.model,
        settings.llm.base_url,
        _mask_secret(settings.llm.api_key),
    )
    logger.info(
        "OneBot Enabled: %s | WS URL: %s | Debug User ID: %s | Token: %s",
        settings.onebot.enabled,
        settings.onebot.ws_url,
        settings.onebot.debug_only_user_id,
        _mask_secret(settings.onebot.token),
    )
    logger.info("==========================")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _log_startup_diagnostics()
    container.task_queue.start()
    container.periodic_scheduler.start()
    if settings.llm.startup_healthcheck_enabled:
        ok = container.llm_client.startup_health_check()
        if not ok:
            logger.error("LLM startup health check failed, service continues in degraded mode.")
    onebot_client = OneBotWSClient(container.chat_orchestrator)
    await onebot_client.start()
    try:
        yield
    finally:
        await onebot_client.stop()
        container.periodic_scheduler.stop()
        container.task_queue.stop()


app = FastAPI(title=settings.app.name, debug=settings.app.debug, lifespan=lifespan)
app.include_router(health_router)
app.include_router(session_router, prefix="/session", tags=["session"])
app.include_router(chat_router, prefix="/chat", tags=["chat"])
app.include_router(llm_router, prefix="/llm", tags=["llm"])

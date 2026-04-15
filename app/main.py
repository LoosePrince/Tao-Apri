from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.chat import router as chat_router
from app.api.routes.health import router as health_router
from app.api.routes.llm import router as llm_router
from app.api.routes.session import router as session_router
from app.core.container import container
from app.core.config import settings
from app.core.logging import setup_logging
from app.integrations.onebot_ws_client import OneBotWSClient

setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    onebot_client = OneBotWSClient(container.chat_orchestrator)
    await onebot_client.start()
    try:
        yield
    finally:
        await onebot_client.stop()


app = FastAPI(title=settings.app.name, debug=settings.app.debug, lifespan=lifespan)
app.include_router(health_router)
app.include_router(session_router, prefix="/session", tags=["session"])
app.include_router(chat_router, prefix="/chat", tags=["chat"])
app.include_router(llm_router, prefix="/llm", tags=["llm"])

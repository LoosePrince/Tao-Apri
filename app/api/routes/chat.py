from datetime import datetime, timezone

from fastapi import APIRouter

from app.api.schemas import ChatRequest, ChatResponse
from app.core.container import container

router = APIRouter()


@router.post("", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    result = container.chat_orchestrator.handle_message(
        user_id=payload.user_id,
        user_message=payload.message,
    )
    return ChatResponse(
        user_id=payload.user_id,
        session_id=result.session_id,
        reply=result.reply,
        session_emotion=result.session_emotion,
        global_emotion=result.global_emotion,
        timestamp=datetime.now(timezone.utc),
    )

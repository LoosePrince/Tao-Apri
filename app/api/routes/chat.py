from datetime import datetime, timezone

from fastapi import APIRouter

from app.api.schemas import ChatRequest, ChatResponse
from app.core.container import container
from app.domain.conversation_scope import ConversationScope

router = APIRouter()


@router.post("", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    scope = ConversationScope.private(platform="api", user_id=payload.user_id)
    result = container.window_manager.process_user_message(
        scope=scope,
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

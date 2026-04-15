from fastapi import APIRouter, HTTPException

from app.api.schemas import SessionStateResponse
from app.core.container import container

router = APIRouter()


@router.get("/{user_id}", response_model=SessionStateResponse)
def get_session_state(user_id: str) -> SessionStateResponse:
    session = container.session_repo.get_by_user_id(user_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionStateResponse(
        user_id=user_id,
        session_id=session.session_id,
        last_seen_at=session.last_seen_at,
        turn_count=session.turn_count,
    )

from datetime import datetime
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=5000)


class ChatResponse(BaseModel):
    user_id: str
    session_id: str
    reply: str
    session_emotion: float
    global_emotion: float
    timestamp: datetime


class SessionStateResponse(BaseModel):
    user_id: str
    session_id: str
    last_seen_at: datetime | None
    turn_count: int

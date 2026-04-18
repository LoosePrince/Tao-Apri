from __future__ import annotations

from dataclasses import dataclass

from app.core.onebot_service import OneBotService
from app.services.channel_sender import SendMessageRequest


@dataclass(slots=True)
class OneBotChannelSender:
    onebot_service: OneBotService

    def send(self, request: SendMessageRequest) -> str:
        if request.channel.strip().lower() not in {"qq", "onebot"}:
            raise ValueError(f"unsupported onebot channel alias: {request.channel}")
        return self.onebot_service.send_message_sync(
            target_type=request.target_type,
            target_id=request.target_id,
            content=request.content,
        )

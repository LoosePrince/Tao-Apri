from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class SendMessageRequest:
    channel: str
    target_type: str
    target_id: str
    content: str
    idempotency_key: str


class ChannelSender(Protocol):
    def send(self, request: SendMessageRequest) -> str: ...


class ChannelRouter:
    def __init__(self) -> None:
        self._senders: dict[str, ChannelSender] = {}

    def register(self, channel: str, sender: ChannelSender) -> None:
        self._senders[channel.strip().lower()] = sender

    def send(self, request: SendMessageRequest) -> str:
        sender = self._senders.get(request.channel.strip().lower())
        if sender is None:
            raise ValueError(f"unsupported channel: {request.channel}")
        return sender.send(request)

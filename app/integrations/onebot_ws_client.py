import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse, urlunparse

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.config import settings
from app.services.chat_orchestrator import ChatOrchestrator

logger = logging.getLogger(__name__)


def _normalize_ws_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    scheme = parsed.scheme.lower()
    if scheme == "http":
        parsed = parsed._replace(scheme="ws")
    elif scheme == "https":
        parsed = parsed._replace(scheme="wss")
    elif scheme not in ("ws", "wss"):
        raise ValueError(f"Unsupported OneBot URL scheme: {raw_url}")
    return urlunparse(parsed)


def _extract_text_from_array_message(message: Any) -> str:
    if isinstance(message, str):
        return message
    if not isinstance(message, list):
        return ""
    text_parts: list[str] = []
    for segment in message:
        if not isinstance(segment, dict):
            continue
        seg_type = segment.get("type")
        data = segment.get("data", {})
        if seg_type == "text":
            text_parts.append(str(data.get("text", "")))
    return "".join(text_parts).strip()


class OneBotWSClient:
    def __init__(self, chat_orchestrator: ChatOrchestrator) -> None:
        self.chat_orchestrator = chat_orchestrator
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not settings.onebot.enabled:
            logger.info("OneBot disabled by config.")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await self._task

    async def _run_loop(self) -> None:
        ws_url = _normalize_ws_url(settings.onebot.ws_url)
        headers = {"Authorization": f"Bearer {settings.onebot.token}"}
        while not self._stop_event.is_set():
            try:
                logger.info("Connecting OneBot WS: %s", ws_url)
                async with websockets.connect(ws_url, additional_headers=headers) as ws:
                    logger.info("OneBot WS connected.")
                    await self._consume(ws)
            except Exception as exc:
                logger.warning("OneBot WS disconnected: %s", exc)
                await asyncio.sleep(settings.onebot.reconnect_interval_seconds)

    async def _consume(self, ws: websockets.ClientConnection) -> None:
        while not self._stop_event.is_set():
            try:
                raw = await ws.recv()
            except ConnectionClosed:
                break
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await self._handle_event(ws, event)

    async def _handle_event(self, ws: websockets.ClientConnection, event: dict[str, Any]) -> None:
        if event.get("post_type") != "message":
            return
        if event.get("message_type") != "private":
            return
        user_id = int(event.get("user_id", 0))
        if settings.app.debug and user_id != settings.onebot.debug_only_user_id:
            return

        message_payload = event.get("message")
        user_text = _extract_text_from_array_message(message_payload)
        if not user_text:
            return

        result = self.chat_orchestrator.handle_message(
            user_id=str(user_id),
            user_message=user_text,
        )

        action_payload = {
            "action": "send_private_msg",
            "params": {
                "user_id": user_id,
                "message": result.reply,
            },
        }
        await ws.send(json.dumps(action_payload, ensure_ascii=False))

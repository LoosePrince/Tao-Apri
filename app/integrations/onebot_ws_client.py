from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.config import settings
from app.domain.conversation_scope import ConversationScope
from app.services.conversation_window_manager import ConversationWindowManager

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

def _is_mentioned_in_array_message(message: Any, *, self_id: int) -> bool:
    if not isinstance(message, list):
        return False
    for segment in message:
        if not isinstance(segment, dict):
            continue
        if segment.get("type") != "at":
            continue
        data = segment.get("data", {}) or {}
        qq = str(data.get("qq", "")).strip()
        if not qq:
            continue
        if qq == "all":
            return True
        try:
            if int(qq) == int(self_id):
                return True
        except Exception:
            continue
    return False


class OneBotWSClient:
    def __init__(self, window_manager: ConversationWindowManager) -> None:
        self.window_manager = window_manager
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._inflight_tasks: set[asyncio.Task] = set()
        self._processed_message_ids: dict[str, float] = {}
        self._processed_message_ttl_seconds = 600.0
        self._processed_message_max_entries = 4096

    def _remember_message_id(self, message_id: str) -> bool:
        now = time.monotonic()
        expired_before = now - self._processed_message_ttl_seconds
        stale_ids = [key for key, seen_at in self._processed_message_ids.items() if seen_at < expired_before]
        for stale_id in stale_ids:
            self._processed_message_ids.pop(stale_id, None)

        if message_id in self._processed_message_ids:
            return False

        if len(self._processed_message_ids) >= self._processed_message_max_entries:
            oldest_id = next(iter(self._processed_message_ids))
            self._processed_message_ids.pop(oldest_id, None)

        self._processed_message_ids[message_id] = now
        return True

    async def start(self) -> None:
        if not settings.onebot.enabled:
            logger.info("OneBot disabled by config.")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        inflight = list(self._inflight_tasks)
        for task in inflight:
            task.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
        if self._task:
            await self._task

    @staticmethod
    def _split_reply_segments(reply: str) -> list[str]:
        text = (reply or "").strip()
        if not text:
            return []
        parts: list[str] = []
        for block in re.split(r"\n\s*\n+", text):
            block = block.strip()
            if not block:
                continue
            sentence_parts = [item.strip() for item in re.split(r"(?<=[。！？!?])\s+", block) if item.strip()]
            if sentence_parts:
                parts.extend(sentence_parts)
            else:
                parts.append(block)
        return parts or [text]

    @staticmethod
    def _segment_delay_seconds(segment: str) -> float:
        char_count = max(0, len(segment.strip()))
        if char_count <= 0:
            return 0.0
        return min(5.0, char_count / 5.0)

    async def _send_reply_segments(self, ws: websockets.ClientConnection, *, scope: ConversationScope, reply: str) -> None:
        segments = self._split_reply_segments(reply)
        if not segments:
            return
        if scope.scene_type == "group" and not scope.group_id:
            return
        for index, segment in enumerate(segments):
            if scope.scene_type == "group":
                action_payload = {
                    "action": "send_group_msg",
                    "params": {
                        "group_id": int(scope.group_id or 0),
                        "message": segment,
                    },
                }
            else:
                action_payload = {
                    "action": "send_private_msg",
                    "params": {
                        "user_id": int(scope.actor_user_id),
                        "message": segment,
                    },
                }
            logger.debug("OneBot send payload: %s", action_payload)
            async with self._send_lock:
                await ws.send(json.dumps(action_payload, ensure_ascii=False))
            logger.info(
                "OneBot reply segment sent | scope=%s | len=%s",
                scope.scope_id,
                len(segment),
            )
            if index >= len(segments) - 1:
                continue
            delay_seconds = self._segment_delay_seconds(segment)
            if delay_seconds <= 0:
                continue
            logger.debug(
                "OneBot segment delay | scope=%s | len=%s | delay=%.2fs",
                scope.scope_id,
                len(segment),
                delay_seconds,
            )
            await asyncio.sleep(delay_seconds)

    async def _process_message(
        self,
        ws: websockets.ClientConnection,
        *,
        scope: ConversationScope,
        user_text: str,
        nickname: str | None = None,
    ) -> None:
        result = await asyncio.to_thread(
            self.window_manager.process_user_message,
            scope=scope,
            user_message=user_text,
            nickname=nickname,
        )
        logger.info(
            "OneBot message processed | scope=%s | session_id=%s | session_emotion=%.3f | global_emotion=%.3f",
            scope.scope_id,
            result.session_id,
            result.session_emotion,
            result.global_emotion,
        )
        logger.debug("OneBot reply text: %s", result.reply)
        await self._send_reply_segments(ws, scope=scope, reply=result.reply)

    def _on_inflight_done(self, task: asyncio.Task) -> None:
        self._inflight_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover
            logger.exception("OneBot async process failed: %s", exc)

    async def _run_loop(self) -> None:
        headers = {"Authorization": f"Bearer {settings.onebot.token}"}
        while not self._stop_event.is_set():
            try:
                ws_url = _normalize_ws_url(settings.onebot.ws_url)
                logger.info("Connecting OneBot WS: %s", ws_url)
                async with websockets.connect(ws_url, additional_headers=headers) as ws:
                    logger.info("OneBot WS connected.")
                    await self._consume(ws)
            except ValueError as exc:
                logger.error("OneBot config error: %s", exc)
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(settings.onebot.reconnect_interval_seconds)
            except Exception as exc:
                logger.warning("OneBot WS disconnected: %s", exc)
                await asyncio.sleep(settings.onebot.reconnect_interval_seconds)

    async def _consume(self, ws: websockets.ClientConnection) -> None:
        while not self._stop_event.is_set():
            try:
                raw = await ws.recv()
            except ConnectionClosed:
                break
            logger.debug("OneBot raw event: %s", raw)
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("Skip non-json event frame.")
                continue
            await self._handle_event(ws, event)

    async def _handle_event(self, ws: websockets.ClientConnection, event: dict[str, Any]) -> None:
        if event.get("post_type") != "message":
            logger.debug("Skip non-message event post_type=%s", event.get("post_type"))
            return
        message_type = str(event.get("message_type", "") or "").strip()
        if message_type not in {"private", "group"}:
            logger.debug("Skip unsupported message type=%s", message_type)
            return
        self_id = int(event.get("self_id", 0) or 0)
        user_id = int(event.get("user_id", 0))
        group_id = int(event.get("group_id", 0) or 0) if message_type == "group" else 0
        sender = event.get("sender")
        sender_user_id = 0
        if isinstance(sender, dict):
            sender_user_id = int(sender.get("user_id", 0) or 0)
        if self_id and sender_user_id == self_id:
            logger.debug("Skip self-sent message | self_id=%s | message_id=%s", self_id, event.get("message_id"))
            return
        if settings.app.debug and user_id != settings.onebot.debug_only_user_id:
            logger.debug(
                "Skip message by debug filter user_id=%s expected=%s",
                user_id,
                settings.onebot.debug_only_user_id,
            )
            return

        message_payload = event.get("message")
        user_text = _extract_text_from_array_message(message_payload)
        if not user_text:
            logger.debug("Skip empty text message payload=%s", message_payload)
            return
        message_id = str(event.get("message_id", "")).strip()
        if message_id and not self._remember_message_id(message_id):
            logger.info("Skip duplicate message | user_id=%s | message_id=%s", user_id, message_id)
            return
        if message_type == "group":
            whitelist = set(settings.onebot.group_autonomous_whitelist or [])
            in_whitelist = group_id in whitelist
            if settings.onebot.force_group_whitelist and not in_whitelist:
                logger.debug(
                    "Skip group message (force whitelist) | group_id=%s | user_id=%s",
                    group_id,
                    user_id,
                )
                return
            allow_autonomous = in_whitelist
            mentioned = _is_mentioned_in_array_message(message_payload, self_id=self_id)
            if not allow_autonomous and not mentioned:
                logger.debug(
                    "Skip group message (not mentioned) | group_id=%s | user_id=%s | self_id=%s",
                    group_id,
                    user_id,
                    self_id,
                )
                return
        logger.info("OneBot received message | type=%s | user_id=%s | text=%s", message_type, user_id, user_text)
        logger.debug("OneBot full event payload: %s", event)
        sender_nickname: str | None = None
        if isinstance(sender, dict):
            raw_nick = sender.get("card") or sender.get("nickname") or sender.get("remark") or ""
            sender_nickname = str(raw_nick).strip() or None

        if message_type == "group":
            scope = ConversationScope.group(platform="onebot", group_id=str(group_id), user_id=str(user_id))
        else:
            scope = ConversationScope.private(platform="onebot", user_id=str(user_id))

        task = asyncio.create_task(self._process_message(ws, scope=scope, user_text=user_text, nickname=sender_nickname))
        self._inflight_tasks.add(task)
        task.add_done_callback(self._on_inflight_done)

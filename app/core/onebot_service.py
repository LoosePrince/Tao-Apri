from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.integrations.onebot_ws_client import OneBotWSClient


class OneBotService:
    """
    抽离 OneBot 客户端生命周期，便于运行时配置变更后受控重连。
    """

    def __init__(self) -> None:
        self._client: OneBotWSClient | None = None
        self._lock = asyncio.Lock()
        self._runtime_loop: asyncio.AbstractEventLoop | None = None

    async def start(self, *, window_manager: Any, reply_message_lookup: Callable[[str], str] | None = None) -> None:
        async with self._lock:
            if self._client is not None:
                return
            self._runtime_loop = asyncio.get_running_loop()
            self._client = OneBotWSClient(window_manager, reply_message_lookup=reply_message_lookup)
            await self._client.start()

    async def stop(self) -> None:
        async with self._lock:
            if self._client is None:
                return
            await self._client.stop()
            self._client = None
            self._runtime_loop = None

    async def restart(self, *, window_manager: Any, reply_message_lookup: Callable[[str], str] | None = None) -> None:
        async with self._lock:
            if self._client is not None:
                await self._client.stop()
            self._runtime_loop = asyncio.get_running_loop()
            self._client = OneBotWSClient(window_manager, reply_message_lookup=reply_message_lookup)
            await self._client.start()

    async def send_message(self, *, target_type: str, target_id: str, content: str) -> str:
        async with self._lock:
            if self._client is None:
                raise RuntimeError("onebot client not started")
            return await self._client.send_text(target_type=target_type, target_id=target_id, content=content)

    def send_message_sync(self, *, target_type: str, target_id: str, content: str, timeout_seconds: float = 6.0) -> str:
        loop = self._runtime_loop
        if loop is None:
            raise RuntimeError("onebot runtime loop not ready")
        future = asyncio.run_coroutine_threadsafe(
            self.send_message(target_type=target_type, target_id=target_id, content=content),
            loop,
        )
        return future.result(timeout=timeout_seconds)


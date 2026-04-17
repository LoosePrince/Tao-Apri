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

    async def start(self, *, window_manager: Any, reply_message_lookup: Callable[[str], str] | None = None) -> None:
        async with self._lock:
            if self._client is not None:
                return
            self._client = OneBotWSClient(window_manager, reply_message_lookup=reply_message_lookup)
            await self._client.start()

    async def stop(self) -> None:
        async with self._lock:
            if self._client is None:
                return
            await self._client.stop()
            self._client = None

    async def restart(self, *, window_manager: Any, reply_message_lookup: Callable[[str], str] | None = None) -> None:
        async with self._lock:
            if self._client is not None:
                await self._client.stop()
            self._client = OneBotWSClient(window_manager, reply_message_lookup=reply_message_lookup)
            await self._client.start()


import asyncio

from app.core.config import settings
from app.integrations.onebot_ws_client import OneBotWSClient
from app.domain.conversation_scope import ConversationScope


class _StubWindowManager:
    def process_user_message(self, *, scope: ConversationScope, user_message: str, nickname: str | None = None):
        del scope, user_message, nickname
        return None


class _StubWS:
    def __init__(self) -> None:
        self.sent_payloads: list[str] = []

    async def send(self, _: str) -> None:
        self.sent_payloads.append(_)
        return None


class _InspectableOneBotClient(OneBotWSClient):
    def __init__(self) -> None:
        super().__init__(_StubWindowManager())
        self.processed: list[tuple[int, str]] = []

    async def _process_message(  # type: ignore[override]
        self,
        ws,
        *,
        scope: ConversationScope,
        user_text: str,
        nickname: str | None = None,
    ) -> None:
        del ws
        del nickname
        self.processed.append((int(scope.actor_user_id), user_text))


def test_onebot_duplicate_message_id_is_skipped() -> None:
    async def _run() -> list[tuple[int, str]]:
        client = _InspectableOneBotClient()
        ws = _StubWS()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 3396584245,
            "user_id": 1377820366,
            "message_id": 473724290,
            "sender": {"user_id": 1377820366},
            "message": [{"type": "text", "data": {"text": "下午好"}}],
        }

        await client._handle_event(ws, event)
        await asyncio.sleep(0)
        await client._handle_event(ws, event)
        await asyncio.sleep(0)
        return client.processed

    assert asyncio.run(_run()) == [(1377820366, "下午好")]


def test_onebot_self_sent_message_is_skipped() -> None:
    async def _run() -> list[tuple[int, str]]:
        client = _InspectableOneBotClient()
        ws = _StubWS()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 3396584245,
            "user_id": 3396584245,
            "message_id": 999,
            "sender": {"user_id": 3396584245},
            "message": [{"type": "text", "data": {"text": "我是机器人自己发的"}}],
        }

        await client._handle_event(ws, event)
        await asyncio.sleep(0)
        return client.processed

    assert asyncio.run(_run()) == []


def test_segment_delay_seconds_matches_rule() -> None:
    client = OneBotWSClient(_StubWindowManager())

    assert client._segment_delay_seconds("你好呀") == 0.6
    assert client._segment_delay_seconds("12345") == 1.0
    assert client._segment_delay_seconds("a" * 100) == 5.0


def test_send_reply_segments_waits_between_segments() -> None:
    async def _run() -> tuple[list[str], list[float]]:
        client = OneBotWSClient(_StubWindowManager())
        ws = _StubWS()
        delays: list[float] = []
        original_sleep = asyncio.sleep

        async def _fake_sleep(delay: float) -> None:
            delays.append(delay)

        asyncio.sleep = _fake_sleep
        try:
            scope = ConversationScope.private(platform="test", user_id="1")
            await client._send_reply_segments(ws, scope=scope, reply="你好呀\n\n今天怎么样")
        finally:
            asyncio.sleep = original_sleep
        return ws.sent_payloads, delays

    sent_payloads, delays = asyncio.run(_run())

    assert len(sent_payloads) == 2
    assert delays == [0.6]


def test_group_message_not_mentioned_is_skipped() -> None:
    async def _run() -> list[tuple[int, str]]:
        client = _InspectableOneBotClient()
        ws = _StubWS()
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 3396584245,
            "group_id": 10001,
            "user_id": 1377820366,
            "message_id": 1,
            "sender": {"user_id": 1377820366},
            "message": [{"type": "text", "data": {"text": "大家好"}}],
        }
        await client._handle_event(ws, event)
        await asyncio.sleep(0)
        return client.processed

    assert asyncio.run(_run()) == []


def test_group_message_mentioned_is_processed() -> None:
    async def _run() -> list[tuple[int, str]]:
        client = _InspectableOneBotClient()
        ws = _StubWS()
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 3396584245,
            "group_id": 10001,
            "user_id": 1377820366,
            "message_id": 2,
            "sender": {"user_id": 1377820366},
            "message": [
                {"type": "at", "data": {"qq": "3396584245"}},
                {"type": "text", "data": {"text": " 你好"}} ,
            ],
        }
        await client._handle_event(ws, event)
        await asyncio.sleep(0)
        return client.processed

    assert asyncio.run(_run()) == [(1377820366, "你好")]


def test_group_message_force_whitelist_blocks_non_whitelist_even_if_mentioned() -> None:
    old_force = settings.onebot.force_group_whitelist
    old_whitelist = list(settings.onebot.group_autonomous_whitelist)
    settings.onebot.force_group_whitelist = True
    settings.onebot.group_autonomous_whitelist = [10001]
    try:
        async def _run() -> list[tuple[int, str]]:
            client = _InspectableOneBotClient()
            ws = _StubWS()
            event = {
                "post_type": "message",
                "message_type": "group",
                "self_id": 3396584245,
                "group_id": 20002,
                "user_id": 1377820366,
                "message_id": 3,
                "sender": {"user_id": 1377820366},
                "message": [
                    {"type": "at", "data": {"qq": "3396584245"}},
                    {"type": "text", "data": {"text": " 你好"}},
                ],
            }
            await client._handle_event(ws, event)
            await asyncio.sleep(0)
            return client.processed

        assert asyncio.run(_run()) == []
    finally:
        settings.onebot.force_group_whitelist = old_force
        settings.onebot.group_autonomous_whitelist = old_whitelist


def test_group_message_when_not_force_whitelist_allows_mentioned_non_whitelist() -> None:
    old_force = settings.onebot.force_group_whitelist
    old_whitelist = list(settings.onebot.group_autonomous_whitelist)
    settings.onebot.force_group_whitelist = False
    settings.onebot.group_autonomous_whitelist = [10001]
    try:
        async def _run() -> list[tuple[int, str]]:
            client = _InspectableOneBotClient()
            ws = _StubWS()
            event = {
                "post_type": "message",
                "message_type": "group",
                "self_id": 3396584245,
                "group_id": 20002,
                "user_id": 1377820366,
                "message_id": 4,
                "sender": {"user_id": 1377820366},
                "message": [
                    {"type": "at", "data": {"qq": "3396584245"}},
                    {"type": "text", "data": {"text": " 你好"}},
                ],
            }
            await client._handle_event(ws, event)
            await asyncio.sleep(0)
            return client.processed

        assert asyncio.run(_run()) == [(1377820366, "你好")]
    finally:
        settings.onebot.force_group_whitelist = old_force
        settings.onebot.group_autonomous_whitelist = old_whitelist

import asyncio

from app.core.config import settings
from app.services.chat_orchestrator import ChatResult
from app.integrations.onebot_ws_client import OneBotWSClient, _extract_attachments_from_array_message
from app.domain.conversation_scope import ConversationScope


class _StubWindowManager:
    def process_user_message(
        self,
        *,
        scope: ConversationScope,
        user_message: str,
        nickname: str | None = None,
        source_message_id: str | None = None,
    ):
        del scope, user_message, nickname, source_message_id
        return None


class _StubWS:
    def __init__(self) -> None:
        self.sent_payloads: list[str] = []

    async def send(self, _: str) -> None:
        self.sent_payloads.append(_)
        return None


class _InspectableOneBotClient(OneBotWSClient):
    def __init__(self, *, reply_message_lookup=None) -> None:
        super().__init__(_StubWindowManager(), reply_message_lookup=reply_message_lookup)
        self.processed: list[tuple[int, str]] = []

    async def _process_message(  # type: ignore[override]
        self,
        ws,
        *,
        scope: ConversationScope,
        user_text: str,
        attachments: list[dict[str, object]] | None = None,
        source_message_id: str | None = None,
        nickname: str | None = None,
        group_bot_mentioned: bool | None = None,
        group_allow_autonomous: bool | None = None,
    ) -> None:
        del ws, attachments, source_message_id, nickname, group_bot_mentioned, group_allow_autonomous
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
    old_force = settings.onebot.force_group_whitelist
    old_whitelist = list(settings.onebot.group_autonomous_whitelist)
    settings.onebot.force_group_whitelist = False
    settings.onebot.group_autonomous_whitelist = []
    try:

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
    finally:
        settings.onebot.force_group_whitelist = old_force
        settings.onebot.group_autonomous_whitelist = old_whitelist


def test_group_message_mentioned_is_processed() -> None:
    old_force = settings.onebot.force_group_whitelist
    old_whitelist = list(settings.onebot.group_autonomous_whitelist)
    settings.onebot.force_group_whitelist = False
    settings.onebot.group_autonomous_whitelist = []
    try:

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


def test_private_burst_messages_emit_single_reply() -> None:
    class _BurstWindowManager:
        def process_user_message(
            self,
            *,
            scope: ConversationScope,
            user_message: str,
            nickname: str | None = None,
            source_message_id: str | None = None,
            attachments: list[dict[str, object]] | None = None,
        ):
            del scope, user_message, nickname, source_message_id, attachments
            return ChatResult(session_id="s1", reply="合并回复", session_emotion=0.1, global_emotion=0.2)

    async def _run() -> tuple[int, int]:
        client = OneBotWSClient(_BurstWindowManager())  # type: ignore[arg-type]
        ws = _StubWS()
        scope = ConversationScope.private(platform="onebot", user_id="1377820366")
        tasks = [
            asyncio.create_task(client._process_message(ws, scope=scope, user_text="m1")),
            asyncio.create_task(client._process_message(ws, scope=scope, user_text="m2")),
            asyncio.create_task(client._process_message(ws, scope=scope, user_text="m3")),
        ]
        await asyncio.gather(*tasks)
        return len(ws.sent_payloads), len(tasks)

    sent_count, task_count = asyncio.run(_run())
    assert task_count == 3
    assert sent_count == 1


def test_private_known_non_text_segments_are_placeholder_processed() -> None:
    async def _run() -> list[tuple[int, str]]:
        client = _InspectableOneBotClient()
        ws = _StubWS()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 3396584245,
            "user_id": 1377820366,
            "message_id": 8881,
            "sender": {"user_id": 1377820366},
            "message": [
                {"type": "image", "data": {"file": "a.png"}},
                {"type": "text", "data": {"text": " 看一下"}},
            ],
        }
        await client._handle_event(ws, event)
        await asyncio.sleep(0)
        return client.processed

    assert asyncio.run(_run()) == [(1377820366, "[image: a.png] 看一下")]


def test_private_unknown_segments_only_are_skipped() -> None:
    async def _run() -> list[tuple[int, str]]:
        client = _InspectableOneBotClient()
        ws = _StubWS()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 3396584245,
            "user_id": 1377820366,
            "message_id": 8882,
            "sender": {"user_id": 1377820366},
            "message": [
                {"type": "poke", "data": {"id": "1"}},
                {"type": "mystery", "data": {"foo": "bar"}},
            ],
        }
        await client._handle_event(ws, event)
        await asyncio.sleep(0)
        return client.processed

    assert asyncio.run(_run()) == []


def test_reply_segment_with_text_is_processed_as_readable_placeholder() -> None:
    async def _run() -> list[tuple[int, str]]:
        client = _InspectableOneBotClient()
        ws = _StubWS()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 3396584245,
            "user_id": 1377820366,
            "message_id": 8883,
            "sender": {"user_id": 1377820366},
            "message": [
                {"type": "reply", "data": {"id": "10001", "text": "上一条核心信息"}},
                {"type": "text", "data": {"text": " 收到"}},
            ],
        }
        await client._handle_event(ws, event)
        await asyncio.sleep(0)
        return client.processed

    assert asyncio.run(_run()) == [(1377820366, "[reply: 上一条核心信息] 收到")]


def test_reply_segment_can_resolve_cached_message_text_by_id() -> None:
    async def _run() -> list[tuple[int, str]]:
        client = _InspectableOneBotClient()
        ws = _StubWS()
        first_event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 3396584245,
            "user_id": 1377820366,
            "message_id": 9001,
            "sender": {"user_id": 1377820366},
            "message": [{"type": "text", "data": {"text": "你昨晚让我整理的清单"}}],
        }
        second_event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 3396584245,
            "user_id": 1377820366,
            "message_id": 9002,
            "sender": {"user_id": 1377820366},
            "message": [
                {"type": "reply", "data": {"id": "9001"}},
                {"type": "text", "data": {"text": " 我已经改好了"}},
            ],
        }
        await client._handle_event(ws, first_event)
        await asyncio.sleep(0)
        await client._handle_event(ws, second_event)
        await asyncio.sleep(0)
        return client.processed

    assert asyncio.run(_run()) == [
        (1377820366, "你昨晚让我整理的清单"),
        (1377820366, "[reply: 你昨晚让我整理的清单] 我已经改好了"),
    ]


def test_reply_segment_can_resolve_external_lookup_when_cache_miss() -> None:
    async def _run() -> list[tuple[int, str]]:
        client = _InspectableOneBotClient(reply_message_lookup=lambda message_id: "数据库里那条消息" if message_id == "777" else "")
        ws = _StubWS()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 3396584245,
            "user_id": 1377820366,
            "message_id": 9003,
            "sender": {"user_id": 1377820366},
            "message": [
                {"type": "reply", "data": {"id": "777"}},
                {"type": "text", "data": {"text": " 我补充一下"}},
            ],
        }
        await client._handle_event(ws, event)
        await asyncio.sleep(0)
        return client.processed

    assert asyncio.run(_run()) == [(1377820366, "[reply: 数据库里那条消息] 我补充一下")]


def test_extract_attachments_from_array_message_keeps_supported_media_segments() -> None:
    attachments = _extract_attachments_from_array_message(
        [
            {"type": "image", "data": {"url": "http://example.com/a.png"}},
            {"type": "text", "data": {"text": "hi"}},
            {"type": "file", "data": {"name": "doc.txt"}},
            {"type": "at", "data": {"qq": "1"}},
        ]
    )
    assert attachments == [
        {"type": "image", "data": {"url": "http://example.com/a.png"}},
        {"type": "file", "data": {"name": "doc.txt"}},
    ]

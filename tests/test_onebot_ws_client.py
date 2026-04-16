import asyncio

from app.integrations.onebot_ws_client import OneBotWSClient


class _StubWindowManager:
    def process_user_message(self, *, user_id: str, user_message: str, nickname: str | None = None):
        del user_id, user_message, nickname
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
        user_id: int,
        user_text: str,
        nickname: str | None = None,
    ) -> None:
        del ws
        self.processed.append((user_id, user_text))


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
            await client._send_reply_segments(ws, user_id=1, reply="你好呀\n\n今天怎么样")
        finally:
            asyncio.sleep = original_sleep
        return ws.sent_payloads, delays

    sent_payloads, delays = asyncio.run(_run())

    assert len(sent_payloads) == 2
    assert delays == [0.6]

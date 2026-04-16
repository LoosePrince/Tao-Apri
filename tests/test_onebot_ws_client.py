import asyncio

from app.integrations.onebot_ws_client import OneBotWSClient


class _StubWindowManager:
    def process_user_message(self, *, user_id: str, user_message: str):
        del user_id, user_message
        return None


class _StubWS:
    async def send(self, _: str) -> None:
        return None


class _InspectableOneBotClient(OneBotWSClient):
    def __init__(self) -> None:
        super().__init__(_StubWindowManager())
        self.processed: list[tuple[int, str]] = []

    async def _process_message(self, ws, *, user_id: int, user_text: str) -> None:  # type: ignore[override]
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

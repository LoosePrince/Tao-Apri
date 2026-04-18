from app.services.window_delivery_timeout import (
    LATE_ASSISTANT_DELIVERY_PREFIX,
    consume_late_assistant_delivery,
    mark_late_assistant_delivery,
)


def test_mark_consume_late_delivery_once_per_round() -> None:
    mark_late_assistant_delivery("scope-a", 7)
    assert consume_late_assistant_delivery("scope-a", 7) is True
    assert consume_late_assistant_delivery("scope-a", 7) is False


def test_consume_without_mark_or_none_round() -> None:
    assert consume_late_assistant_delivery("scope-b", 1) is False
    assert consume_late_assistant_delivery("scope-b", None) is False


def test_prefix_constant() -> None:
    assert LATE_ASSISTANT_DELIVERY_PREFIX == "[此消息超时未成功发送]"

from app.core.rule_lexicons import (
    allowed_topic_labels,
    classify_deterministic_topic,
    text_hints_timeline_fact,
)


def test_classify_topic_matches_legacy_rules() -> None:
    assert classify_deterministic_topic("我在准备考试复习") == "学习与考试"
    assert classify_deterministic_topic("今天要加班开会") == "工作与职业"
    assert classify_deterministic_topic("昨晚睡太晚头痛") == "作息与健康"
    assert classify_deterministic_topic("我很焦虑关系很烦") == "情绪与关系"
    assert classify_deterministic_topic("周末看电影") == "娱乐与兴趣"
    assert classify_deterministic_topic("随便聊聊天气") == "日常近况"


def test_allowed_topic_labels_covers_all_rules_and_default() -> None:
    labels = allowed_topic_labels()
    assert "日常近况" in labels
    assert "学习与考试" in labels
    assert len(labels) >= 6


def test_timeline_tokens() -> None:
    assert text_hints_timeline_fact("明天见")
    assert not text_hints_timeline_fact("没有日期")

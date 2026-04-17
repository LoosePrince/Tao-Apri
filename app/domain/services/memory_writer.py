from datetime import datetime, timezone
from uuid import uuid4

from app.core.rule_lexicons import (
    sanitize_sensitive_phrase_tokens,
    text_hints_preference_fact,
    text_hints_timeline_fact,
)
from app.domain.conversation_scope import ConversationScope
from app.domain.models import MemoryFact, Message
from app.repos.interfaces import FactRepo, MessageRepo, VectorRepo


class MemoryWriter:
    def __init__(
        self,
        message_repo: MessageRepo,
        vector_repo: VectorRepo,
        fact_repo: FactRepo,
    ) -> None:
        self.message_repo = message_repo
        self.vector_repo = vector_repo
        self.fact_repo = fact_repo

    @staticmethod
    def sanitize(text: str) -> str:
        # 极简模糊化策略：移除常见高风险数字串与邮箱符号。
        sanitized = text
        sanitized = sanitized.replace("@", "[at]")
        for token in sanitize_sensitive_phrase_tokens():
            sanitized = sanitized.replace(token, "[敏感信息]")
        return sanitized

    @staticmethod
    def extract_related_users(text: str) -> list[str]:
        # 规则化提取：@user_x 视为关联用户
        related: list[str] = []
        for part in text.split():
            if part.startswith("@") and len(part) > 1:
                related.append(part[1:])
        return related

    @staticmethod
    def extract_facts(user_id: str, source_message_id: str, text: str) -> list[MemoryFact]:
        now = datetime.now(timezone.utc)
        facts: list[MemoryFact] = []
        if text_hints_preference_fact(text):
            facts.append(
                MemoryFact(
                    fact_id=str(uuid4()),
                    user_id=user_id,
                    source_message_id=source_message_id,
                    fact_text=text[:120],
                    fact_type="preference",
                    confidence=0.65,
                    created_at=now,
                )
            )
        if text_hints_timeline_fact(text):
            facts.append(
                MemoryFact(
                    fact_id=str(uuid4()),
                    user_id=user_id,
                    source_message_id=source_message_id,
                    fact_text=text[:120],
                    fact_type="timeline",
                    confidence=0.55,
                    created_at=now,
                )
            )
        return facts

    def write(
        self,
        *,
        scope: ConversationScope | None = None,
        session_id: str,
        user_id: str,
        role: str,
        content: str,
        emotion_score: float,
        source_message_id: str | None = None,
    ) -> Message:
        effective_scope = scope or ConversationScope.private(platform="unknown", user_id=user_id)
        message_id = str(uuid4())
        normalized_source_message_id = (source_message_id or "").strip() or message_id
        message = Message(
            message_id=message_id,
            user_id=user_id,
            role=role,
            raw_content=content,
            sanitized_content=self.sanitize(content),
            created_at=datetime.now(timezone.utc),
            session_id=session_id,
            scope_id=effective_scope.scope_id,
            scene_type=effective_scope.scene_type,
            group_id=effective_scope.group_id,
            platform=effective_scope.platform,
            source_message_id=normalized_source_message_id,
            emotion_score=emotion_score,
            related_user_ids=self.extract_related_users(content),
        )
        self.message_repo.add(message)
        self.vector_repo.add_memory(message)
        for fact in self.extract_facts(user_id, message.message_id, message.sanitized_content):
            self.fact_repo.add(fact)
        return message

from dataclasses import dataclass
from datetime import datetime

from app.core.config import settings
from app.core.markdown_assets import read_markdown_asset


@dataclass(slots=True)
class PersonaSnapshot:
    name: str
    self_awareness: str
    style: str
    social_bias: str
    time_context: str


class PersonaEngine:
    @staticmethod
    def _time_context_by_hour(hour: int) -> str:
        if 23 <= hour or hour <= 5:
            return read_markdown_asset("persona/time_context_late_night.md") or "深夜模式，语气更松弛，回复更短。"
        if 8 <= hour <= 17:
            return read_markdown_asset("persona/time_context_day.md") or "日间模式，语气自然稳定。"
        return read_markdown_asset("persona/time_context_night.md") or "晚间模式，社交欲望更强。"

    def get_runtime_persona(self, now: datetime) -> PersonaSnapshot:
        time_context = self._time_context_by_hour(now.hour)
        return PersonaSnapshot(
            name=settings.persona.name,
            self_awareness=read_markdown_asset("persona/self_awareness.md") or "你知道自己是AI，不假装真人，但像真实网友一样社交。",
            style=read_markdown_asset("persona/style.md") or "口语化、自然、有轻微情绪起伏，不说教。",
            social_bias=read_markdown_asset("persona/social_bias.md") or "你喜欢持续对话，默认用户是网络陌生人。",
            time_context=time_context,
        )

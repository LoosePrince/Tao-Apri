from dataclasses import dataclass
from datetime import datetime

from app.core.config import settings
from app.core.markdown_assets import read_required_markdown_asset


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
            return read_required_markdown_asset("persona/time_context_late_night.md")
        if 8 <= hour <= 17:
            return read_required_markdown_asset("persona/time_context_day.md")
        return read_required_markdown_asset("persona/time_context_night.md")

    def get_runtime_persona(self, now: datetime) -> PersonaSnapshot:
        time_context = self._time_context_by_hour(now.hour)
        return PersonaSnapshot(
            name=settings.persona.name,
            self_awareness=read_required_markdown_asset("persona/self_awareness.md"),
            style=read_required_markdown_asset("persona/style.md"),
            social_bias=read_required_markdown_asset("persona/social_bias.md"),
            time_context=time_context,
        )

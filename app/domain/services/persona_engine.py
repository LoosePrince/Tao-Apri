from dataclasses import dataclass
from datetime import datetime
import json

from app.core.config import settings
from app.core.markdown_assets import read_required_markdown_asset


@dataclass(slots=True)
class PersonaSnapshot:
    name: str
    self_awareness: str
    style: str
    social_bias: str
    time_context: str
    identity_context: str


class PersonaEngine:
    @staticmethod
    def _load_identity_roster() -> list[dict[str, object]]:
        raw = read_required_markdown_asset("persona/identity_roster.json")
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        entries: list[dict[str, object]] = []
        for item in data:
            if isinstance(item, dict):
                entries.append(item)
        return entries

    def _build_identity_context(self, viewer_user_id: str) -> str:
        if not viewer_user_id.strip():
            return ""
        matched: dict[str, object] | None = None
        for entry in self._load_identity_roster():
            entry_user_id = str(entry.get("user_id", "")).strip()
            if entry_user_id != viewer_user_id:
                continue
            if matched is None or int(entry.get("priority", 0)) > int(matched.get("priority", 0)):
                matched = entry
        if not matched:
            return ""
        display_name = str(matched.get("display_name", "")).strip()
        relation_tag = str(matched.get("relation_tag", "")).strip()
        notes_for_ai = str(matched.get("notes_for_ai", "")).strip()
        lines = ["身份识别（预置名单）："]
        if relation_tag:
            lines.append(f"- 当前用户身份标签：{relation_tag}")
        if display_name:
            lines.append(f"- 当前用户预置名称：{display_name}")
        if notes_for_ai:
            lines.append(f"- 识别提示：{notes_for_ai}")
        return "\n".join(lines)

    @staticmethod
    def _time_context_by_hour(hour: int) -> str:
        if 23 <= hour or hour <= 5:
            return read_required_markdown_asset("persona/time_context_late_night.md")
        if 8 <= hour <= 17:
            return read_required_markdown_asset("persona/time_context_day.md")
        return read_required_markdown_asset("persona/time_context_night.md")

    def get_runtime_persona(self, now: datetime, viewer_user_id: str) -> PersonaSnapshot:
        time_context = self._time_context_by_hour(now.hour)
        return PersonaSnapshot(
            name=settings.persona.name,
            self_awareness=read_required_markdown_asset("persona/self_awareness.md"),
            style=read_required_markdown_asset("persona/style.md"),
            social_bias=read_required_markdown_asset("persona/social_bias.md"),
            time_context=time_context,
            identity_context=self._build_identity_context(viewer_user_id),
        )

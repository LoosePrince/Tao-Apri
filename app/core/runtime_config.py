from __future__ import annotations

import re
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from app.core.config import Settings, settings


FieldLevel = Literal["live", "rebuild", "read_only"]
RuntimeFieldType = Literal["bool", "number", "text", "password", "array"]


def _snake_to_label(s: str) -> str:
    parts = s.split("_")
    return " ".join(p[:1].upper() + p[1:] if p else p for p in parts if p)


def _flatten_paths_from_updates(updates: dict[str, Any], *, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for k, v in updates.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            paths |= _flatten_paths_from_updates(v, prefix=path)
        else:
            paths.add(path)
    return paths


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def _env_key_for_path(path: str) -> str:
    # Example: llm.api_key -> LLM__API_KEY
    return "__".join(part.upper() for part in path.split("."))


def _to_env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        # Keep it aligned with .env.example: JSON array string.
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    return str(value)


@dataclass(frozen=True, slots=True)
class RuntimeFieldMeta:
    path: str
    env_key: str
    label: str
    type: RuntimeFieldType
    level: FieldLevel
    editable: bool
    sensitive: bool


class RuntimeConfigManager:
    """
    - 提供运行时配置快照（从全局 settings 实例取）
    - 提供字段分级（live/rebuild/read_only）
    - 支持 validate / apply（apply 仍由 container 执行重建）
    - 支持 export 为 .env 文本
    """

    _SENSITIVE_PATHS: set[str] = {"llm.api_key", "onebot.token"}

    def __init__(self) -> None:
        self._desc_by_env_key = self._load_desc_from_configuration_doc()

    def _load_desc_from_configuration_doc(self) -> dict[str, str]:
        """
        从 docs/configuration.md 解析：- `ENV_KEY`：描述
        用于给前端每个字段展示 desc。
        """
        repo_root = Path(__file__).resolve().parents[2]
        doc_path = repo_root / "docs" / "configuration.md"
        if not doc_path.exists():
            return {}

        text = doc_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        desc_by_key: dict[str, str] = {}
        key_re = re.compile(r"^\s*-\s*`(?P<key>[^`]+)`：(?P<desc>.+?)\s*$")

        cur_key: str | None = None
        for line in lines:
            m = key_re.match(line)
            if m:
                cur_key = str(m.group("key")).strip()
                desc = str(m.group("desc")).strip()
                desc_by_key[cur_key] = desc
                continue

            # 支持多行描述：缩进且上一个 key 还在
            if cur_key and line.strip() and (line.startswith("  ") or line.startswith("\t")):
                desc_by_key[cur_key] = (desc_by_key.get(cur_key, "") + " " + line.strip()).strip()
            else:
                cur_key = None

        return desc_by_key

    def get_field_level(self, path: str) -> FieldLevel:
        if path.startswith("storage."):
            return "read_only"

        # 关键组件初始化字段：需要重建对应对象。
        if path.startswith("emotion."):
            return "rebuild"
        if path in {"llm.api_key", "llm.base_url", "llm.model", "llm.timeout_seconds", "llm.provider"}:
            return "rebuild"
        if path.startswith("jobs."):
            return "rebuild"
        if path.startswith("onebot."):
            return "rebuild"

        # 其它多数配置在当前实现中按 settings 运行时读取。
        return "live"

    def infer_type(self, path: str, value: Any) -> RuntimeFieldType:
        if path in self._SENSITIVE_PATHS:
            return "password"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, list):
            return "array"
        return "text"

    def _iter_leaf_fields(self) -> list[tuple[str, Any]]:
        dumped = settings.model_dump()
        leaves: list[tuple[str, Any]] = []

        def walk(node: Any, prefix: str) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{prefix}.{k}" if prefix else k)
            else:
                leaves.append((prefix, node))

        walk(dumped, "")
        return leaves

    def get_runtime_config(self) -> dict[str, Any]:
        dumped = settings.model_dump()

        fields: list[dict[str, Any]] = []
        for path, value in self._iter_leaf_fields():
            level = self.get_field_level(path)
            sensitive = path in self._SENSITIVE_PATHS
            f_type = self.infer_type(path, value)
            editable = level != "read_only"
            env_key = _env_key_for_path(path)
            desc = self._desc_by_env_key.get(env_key, "")
            # Sensitive: mask only for UI display.
            display_value: Any
            if sensitive:
                display_value = ""  # password input uses empty as "keep original"
            else:
                display_value = value

            fields.append(
                {
                    "path": path,
                    "env_key": env_key,
                    "label": _snake_to_label(path.split(".")[-1]),
                    "desc": desc,
                    "type": f_type,
                    "level": level,
                    "editable": editable,
                    "sensitive": sensitive,
                    "value": display_value,
                }
            )

        return {"config": dumped, "fields": fields}

    def validate_update(self, updates: dict[str, Any]) -> tuple[Settings, list[str]]:
        """
        返回 (new_settings, errors)
        - read_only 字段如果被修改，则返回错误
        - 其它字段交给 Pydantic 做类型校验
        """
        updated_paths = _flatten_paths_from_updates(updates)
        errors: list[str] = []

        for p in updated_paths:
            if self.get_field_level(p) == "read_only":
                errors.append(f"Field is read_only: {p}")

        onebot_updates = updates.get("onebot")
        if isinstance(onebot_updates, dict) and "ws_url" in onebot_updates:
            raw_url = str(onebot_updates.get("ws_url", "")).strip()
            parsed = urlparse(raw_url)
            if parsed.scheme.lower() not in {"http", "https", "ws", "wss"}:
                errors.append(
                    "Field invalid: onebot.ws_url must use http/https/ws/wss scheme"
                )

        if errors:
            return settings, errors

        merged = _deep_merge_dict(settings.model_dump(), updates)
        new_settings = Settings.model_validate(merged)
        return new_settings, []

    def apply_update_to_singleton(self, new_settings: Settings) -> None:
        # Replace nested model objects to let runtime reads pick new values.
        for top_key in new_settings.model_fields.keys():
            setattr(settings, top_key, getattr(new_settings, top_key))

    def export_env_text(self, cfg: Settings | None = None) -> str:
        cfg = cfg or settings
        dumped = cfg.model_dump()
        lines: list[str] = []

        def walk(node: Any, prefix: str) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{prefix}.{k}" if prefix else k)
            else:
                env_key = _env_key_for_path(prefix)
                lines.append(f"{env_key}={_to_env_value(node)}")

        walk(dumped, "")
        return "\n".join(lines) + "\n"


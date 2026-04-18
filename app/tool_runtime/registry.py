from __future__ import annotations

from app.tool_runtime.types import Tool, ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.spec().name.strip()
        if not name:
            raise ValueError("tool name must not be empty")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for _, tool in sorted(self._tools.items(), key=lambda item: item[0])]

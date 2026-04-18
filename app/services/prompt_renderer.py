from __future__ import annotations

import re


class PromptRenderer:
    @staticmethod
    def render(template: str, values: dict[str, object]) -> str:
        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in values:
                return str(values[key])
            return match.group(0)

        return re.sub(r"\{([a-zA-Z_]\w*)\}", _replace, template)

from pathlib import Path

from app.core.config import settings


def read_markdown_asset(relative_path: str) -> str:
    base_dir = Path(settings.persona.assets_dir)
    file_path = base_dir / relative_path
    if not file_path.exists():
        return ""
    return file_path.read_text(encoding="utf-8").strip()


def read_required_markdown_asset(relative_path: str) -> str:
    content = read_markdown_asset(relative_path)
    if not content:
        raise FileNotFoundError(f"Required markdown asset is missing or empty: {relative_path}")
    return content

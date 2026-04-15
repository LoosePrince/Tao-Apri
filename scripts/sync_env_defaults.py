from __future__ import annotations

import argparse
from pathlib import Path


def _parse_example(path: Path) -> tuple[list[str], dict[str, str], dict[str, list[str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    order: list[str] = []
    values: dict[str, str] = {}
    comments: dict[str, list[str]] = {}
    pending_comments: list[str] = []

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            pending_comments = []
            continue
        if stripped.startswith("#"):
            pending_comments.append(raw)
            continue
        if "=" not in raw:
            pending_comments = []
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        order.append(key)
        values[key] = value
        if pending_comments:
            comments[key] = list(pending_comments)
        pending_comments = []
    return order, values, comments


def _parse_env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            continue
        key, _ = raw.split("=", 1)
        keys.add(key.strip())
    return keys


def sync_env_defaults(*, env_path: Path, example_path: Path) -> list[str]:
    order, values, comments = _parse_example(example_path)
    existing_keys = _parse_env_keys(env_path)
    missing_keys = [key for key in order if key not in existing_keys]
    if not missing_keys:
        return []

    if env_path.exists():
        original = env_path.read_text(encoding="utf-8")
        out_lines = original.splitlines()
    else:
        out_lines = []

    if out_lines and out_lines[-1].strip():
        out_lines.append("")
    out_lines.append("# ===== Auto appended from .env.example =====")
    for key in missing_keys:
        key_comments = comments.get(key, [])
        for c in key_comments:
            out_lines.append(c)
        out_lines.append(f"{key}={values[key]}")
    out_lines.append("# ===== End auto appended =====")

    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return missing_keys


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill missing .env keys from .env.example.")
    parser.add_argument("--env", default=".env", help="Target .env path")
    parser.add_argument("--example", default=".env.example", help="Source .env.example path")
    args = parser.parse_args()

    env_path = Path(args.env)
    example_path = Path(args.example)
    if not example_path.exists():
        raise FileNotFoundError(f"Example file not found: {example_path}")

    added = sync_env_defaults(env_path=env_path, example_path=example_path)
    if not added:
        print("No missing keys. .env is already up to date.")
        return
    print(f"Added {len(added)} keys:")
    for key in added:
        print(f"- {key}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import fnmatch
import os
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    ".cursor",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "logs",
}

DEFAULT_EXCLUDE_FILES = {
    ".env",
    ".DS_Store",
    "Thumbs.db",
}

DEFAULT_EXCLUDE_GLOBS = {
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.log",
    "*.tmp",
    "*.swp",
    "*.zip",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package project files into a deployment zip."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project root path (default: auto-detect from script location).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist"),
        help="Output directory for zip file (default: dist).",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="taoapri",
        help="Base package name (default: taoapri).",
    )
    parser.add_argument(
        "--include-env",
        action="store_true",
        help="Include .env in package (default: excluded).",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Additional exclude glob, can be used multiple times.",
    )
    return parser.parse_args()


def should_exclude(rel_path: str, include_env: bool, extra_globs: list[str]) -> bool:
    rel_path_posix = rel_path.replace("\\", "/")
    parts = rel_path_posix.split("/")

    for part in parts[:-1]:
        if part in DEFAULT_EXCLUDE_DIRS:
            return True

    filename = parts[-1]
    if filename in DEFAULT_EXCLUDE_FILES:
        if filename == ".env" and include_env:
            pass
        else:
            return True

    patterns = list(DEFAULT_EXCLUDE_GLOBS) + extra_globs
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path_posix, pattern) or fnmatch.fnmatch(filename, pattern):
            return True

    return False


def build_package(
    project_root: Path,
    output_dir: Path,
    package_name: str,
    include_env: bool,
    extra_excludes: list[str],
) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"{package_name}_{ts}.zip"
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / zip_name

    file_count = 0
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(project_root):
            root_path = Path(root)
            rel_root = root_path.relative_to(project_root).as_posix()

            dirs[:] = [d for d in dirs if d not in DEFAULT_EXCLUDE_DIRS]

            for filename in files:
                full_path = root_path / filename
                rel_path = (Path(rel_root) / filename).as_posix() if rel_root != "." else filename

                if should_exclude(rel_path, include_env=include_env, extra_globs=extra_excludes):
                    continue

                zf.write(full_path, arcname=rel_path)
                file_count += 1

    print(f"Package created: {zip_path}")
    print(f"Files included: {file_count}")
    return zip_path


def main() -> None:
    args = parse_args()
    project_root = args.project_root.resolve()
    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = (project_root / output_dir).resolve()

    build_package(
        project_root=project_root,
        output_dir=output_dir,
        package_name=args.name.strip() or "taoapri",
        include_env=args.include_env,
        extra_excludes=args.exclude,
    )


if __name__ == "__main__":
    main()


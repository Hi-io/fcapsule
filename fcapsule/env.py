"""Small .env loader for local runs without external dependencies."""

from __future__ import annotations

import os
from pathlib import Path


def _candidate_env_paths(start: Path) -> list[Path]:
    paths = [start / ".env"]
    paths.extend(parent / ".env" for parent in start.parents)
    return paths


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def load_env_file(path: str | Path | None = None, override: bool = False) -> Path | None:
    """Load the nearest .env file into os.environ.

    The project intentionally avoids a python-dotenv dependency. Existing
    environment variables win by default so CI or shell exports can override
    local files.
    """

    if path is None:
        candidates = _candidate_env_paths(Path.cwd().resolve())
        env_path = next((candidate for candidate in candidates if candidate.exists()), None)
    else:
        env_path = Path(path).resolve()
    if env_path is None or not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if override or key not in os.environ:
            os.environ[key] = value
    return env_path

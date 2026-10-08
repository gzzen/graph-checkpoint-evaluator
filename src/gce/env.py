"""Minimal .env loading for credentials and judge settings.

Lines are `KEY=value` or `export KEY=value`; blank lines and `#` comments are ignored; values may be
single- or double-quoted; an unquoted value ends at ` #`. Variables already exported in the process
environment take precedence over the file, so a .env never overrides an explicit export.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env(text: str) -> dict[str, str]:
    values = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = _LINE.match(raw)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key] = value
    return values


def default_env_file() -> Path | None:
    """`.env` in the current directory, else in the repository root."""
    for candidate in (Path.cwd() / ".env", REPO_ROOT / ".env"):
        if candidate.is_file():
            return candidate
    return None


def load_env(path: str | Path | None = None) -> Path | None:
    """Load a .env file into os.environ without overriding existing variables; return the file used."""
    env_file = Path(path) if path else default_env_file()
    if env_file is None:
        return None
    if not env_file.is_file():
        raise FileNotFoundError(f"env file not found: {env_file}")
    for key, value in parse_env(env_file.read_text(encoding="utf-8")).items():
        os.environ.setdefault(key, value)
    return env_file

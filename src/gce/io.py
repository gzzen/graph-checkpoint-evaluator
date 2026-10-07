"""Reading and writing JSON, YAML and JSON-lines documents."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def load_doc(path: str | Path) -> Any:
    """Load a .json, .yaml/.yml or .jsonl file. JSON lines yield a list of objects."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    if suffix == ".jsonl":
        return load_jsonl_text(text, source=str(path))
    return json.loads(text)


def load_jsonl_text(text: str, source: str = "<text>") -> list[Any]:
    rows = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source}:{lineno}: invalid JSON line: {exc.msg}") from exc
    return rows


def dump_json(doc: Any, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")

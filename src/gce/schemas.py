"""JSON Schema loading and validation for gce documents."""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_FILES = {
    "value_spec": "value_spec.v1.schema.json",
    "checkpoint_graph": "checkpoint_graph.v1.schema.json",
    "stage_log_entry": "stage_log_entry.v1.schema.json",
    "trace": "trace.v1.schema.json",
    "evaluation": "evaluation.v1.schema.json",
}
# Value of the top-level "schema" field that identifies each tagged document kind.
KIND_BY_TAG = {
    "gce.checkpoint_graph.v1": "checkpoint_graph",
    "gce.trace.v1": "trace",
    "gce.evaluation.v1": "evaluation",
}


def schema_dir() -> Path:
    packaged = resources.files("gce") / "schemas"
    if packaged.is_dir():
        return Path(str(packaged))
    return Path(__file__).resolve().parents[2] / "schemas"


@lru_cache(maxsize=1)
def _registry() -> Registry:
    pairs = []
    for path in sorted(schema_dir().glob("*.schema.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        pairs.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(pairs)


@lru_cache(maxsize=None)
def validator(kind: str) -> Draft202012Validator:
    if kind not in SCHEMA_FILES:
        raise KeyError(f"unknown document kind {kind!r}; expected one of {sorted(SCHEMA_FILES)}")
    doc = json.loads((schema_dir() / SCHEMA_FILES[kind]).read_text(encoding="utf-8"))
    return Draft202012Validator(doc, registry=_registry())


def detect_kind(doc: Any) -> str | None:
    if isinstance(doc, dict):
        return KIND_BY_TAG.get(doc.get("schema"))
    if isinstance(doc, list):
        return "stage_log_entry"
    return None


def schema_errors(doc: Any, kind: str) -> list[str]:
    """Return human-readable schema violations ('' path means document root)."""
    if kind == "stage_log_entry" and isinstance(doc, list):
        errors = []
        for i, entry in enumerate(doc):
            errors += [f"[{i}]{e}" for e in schema_errors(entry, kind)]
        return errors
    found = sorted(validator(kind).iter_errors(doc), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in found]

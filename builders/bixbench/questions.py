"""BixBench question set: loading, capsule grouping and ideal answers as ValueSpecs."""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from pathlib import Path

from gce.values import infer_spec

from .config import HERE


def load_questions(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def by_capsule(rows: list[dict]) -> "OrderedDict[str, list[dict]]":
    """short_id -> questions, in benchmark order. bix-61 spans several capsule uuids; they stay together."""
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for row in rows:
        groups.setdefault(row["short_id"], []).append(row)
    return groups


def ideal_spec(row: dict) -> dict:
    """ValueSpec for an official BixBench ideal answer.

    range_verifier ideals are '(lo, hi)', bounds possibly swapped. str_verifier and llm_verifier ideals
    are single values or short phrases; phrases become labels (the LLM verifier accepts paraphrases,
    which a label match only approximates).
    """
    ideal = str(row["ideal"]).strip()
    if row.get("eval_mode") == "range_verifier":
        m = re.fullmatch(r"\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)", ideal)
        if m:
            return {"type": "range", "min": float(m.group(1)), "max": float(m.group(2))}
    return infer_spec(ideal)


def answer_visibility() -> dict:
    return json.loads((HERE / "data" / "answer_visibility.json").read_text(encoding="utf-8"))

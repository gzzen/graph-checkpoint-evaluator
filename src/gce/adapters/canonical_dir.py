"""Built-in adapter for the canonical run layout (see docs/canonical_layout.md).

A run unit is any directory under the run root that contains either

* ``trace.json``: a complete gce.trace.v1 document, used as-is; or
* ``meta.json``: run metadata, plus optional ``answers.json``, ``stage_log.jsonl``,
  ``steps.jsonl`` and ``scripts/``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..io import load_doc, load_jsonl_text
from ..trace import empty_trace
from .base import Adapter, RunUnitRef

META_KEYS = ("experiment", "condition", "seed", "model", "group_id", "status", "started_at", "duration_seconds")


class CanonicalDirAdapter(Adapter):
    name = "canonical_dir"

    def discover(self, run_root: Path) -> Iterable[RunUnitRef]:
        run_root = Path(run_root)
        units = sorted({p.parent for pattern in ("trace.json", "meta.json") for p in run_root.rglob(pattern)})
        for unit in units:
            if (unit / "trace.json").is_file():
                unit_id = json.loads((unit / "trace.json").read_text(encoding="utf-8"))["run"]["run_unit_id"]
            else:
                unit_id = json.loads((unit / "meta.json").read_text(encoding="utf-8")).get("run_unit_id") or unit.relative_to(run_root).as_posix()
            yield RunUnitRef(unit_id, str(unit))

    def to_trace(self, ref: RunUnitRef) -> dict:
        unit = Path(ref.location)
        if (unit / "trace.json").is_file():
            return load_doc(unit / "trace.json")
        meta = load_doc(unit / "meta.json")
        trace = empty_trace(ref.id, meta["tasks_covered"], **{k: meta[k] for k in META_KEYS if k in meta})
        if (unit / "steps.jsonl").is_file():
            rows = load_jsonl_text((unit / "steps.jsonl").read_text(encoding="utf-8"), str(unit / "steps.jsonl"))
            trace["steps"] = [{"index": i, **{k: v for k, v in row.items() if k != "index"}} for i, row in enumerate(rows)]
        if (unit / "stage_log.jsonl").is_file():
            trace["stage_log"] = load_jsonl_text((unit / "stage_log.jsonl").read_text(encoding="utf-8"), str(unit / "stage_log.jsonl"))
        answers = load_doc(unit / "answers.json") if (unit / "answers.json").is_file() else {}
        for task in meta["tasks_covered"]:
            value = answers.get(task)
            if value is None:
                trace["final_answers"][task] = {"status": "missing"}
            elif isinstance(value, dict):
                trace["final_answers"][task] = value
            else:
                trace["final_answers"][task] = {"status": "resolved", "answer": value}
        trace["capabilities"] = {
            "raw_steps": any(s.get("output") for s in trace["steps"]),
            "stage_log": bool(trace["stage_log"]),
            "replayable_code": (unit / "scripts").is_dir() and any((unit / "scripts").iterdir()),
        }
        extra = {k: v for k, v in meta.items() if k not in META_KEYS and k not in ("run_unit_id", "tasks_covered")}
        if extra:
            trace["extras"]["meta"] = extra
        return trace

"""Adapter for <describe the experiment>.

Layout (fill in before writing code):

    <run>/                   the exact run given to `gce adapt --run`
      <...>/<unit>/            one run unit = <what a unit is>
        <file>                 <what it contains and which trace fields it feeds>

Mapping notes:
    - tasks_covered: <where task ids come from>
    - steps: <event log format and how kinds are assigned>
    - final_answers: <answer file and format>
    - extras: <what is kept there>
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from gce.adapters.base import Adapter, RunUnitRef
from gce.trace import empty_trace

MAX_OUTPUT_CHARS = 200_000


def clip(text: str | None, stats: dict) -> str | None:
    """Keep head and tail of very large outputs, counting truncations in stats."""
    if text is None or len(text) <= MAX_OUTPUT_CHARS:
        return text
    stats["truncated_outputs"] = stats.get("truncated_outputs", 0) + 1
    half = MAX_OUTPUT_CHARS // 2
    return f"{text[:half]}\n[... {len(text) - 2 * half} characters truncated by adapter ...]\n{text[-half:]}"


class TemplateAdapter(Adapter):
    name = "template"

    def inputs(self, run: Path) -> list[Path]:
        return []  # TODO: files outside `run` that this adapter reads (e.g. the pack's manifests)

    def discover(self, run: Path) -> Iterable[RunUnitRef]:
        run = Path(run)
        units = sorted(p for p in run.glob("*/*") if p.is_dir())  # TODO: real unit boundary
        for unit in units:
            yield RunUnitRef(id=unit.relative_to(run).as_posix(), location=str(unit))

    def to_trace(self, ref: RunUnitRef) -> dict:
        unit = Path(ref.location)
        tasks = [unit.name]  # TODO: read the task ids from the run files
        trace = empty_trace(ref.id, tasks, experiment=None, condition=None, seed=None, model=None, status="unknown")
        stats: dict = {}

        # TODO: steps -> trace["steps"].append({"index": i, "kind": "exec", "tool": ..., "input": ..., "output": clip(..., stats), "t": ...})
        # TODO: stage log (if any) -> trace["stage_log"]
        # TODO: answers -> trace["final_answers"][task] = {"status": "resolved", "answer": ..., "candidates": [...]}
        for task in tasks:
            trace["final_answers"].setdefault(task, {"status": "missing"})

        trace["capabilities"] = {
            "raw_steps": any(s.get("output") for s in trace["steps"]),
            "stage_log": bool(trace["stage_log"]),
            "replayable_code": False,  # TODO
        }
        if stats:
            trace["extras"].update(stats)
        return trace

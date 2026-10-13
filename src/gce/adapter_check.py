"""Conformance check for adapters: proves an adapter works on a concrete run directory."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from .adapters.base import Adapter
from .graph import Graph
from .schemas import schema_errors


@dataclass
class CheckReport:
    units_found: int = 0
    units_checked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    capabilities: dict[str, int] = field(default_factory=dict)
    statuses: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [
            f"adapter-check: {'PASS' if self.ok else 'FAIL'}",
            f"  units discovered: {self.units_found}; checked: {len(self.units_checked)}",
            f"  capabilities (units with flag set): {self.capabilities}",
            f"  answer statuses: {self.statuses}",
        ]
        lines += [f"  ERROR {e}" for e in self.errors]
        lines += [f"  warn  {w}" for w in self.warnings]
        return "\n".join(lines)


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def _snapshot_inputs(adapter: Adapter, run: Path) -> dict[str, tuple[int, int]]:
    snap = _snapshot(run)
    for path in map(Path, adapter.inputs(run)):
        files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
        snap.update({str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in files})
    return snap


def check_adapter(adapter: Adapter, run_root: str | Path, graphs: list[Graph] | None = None, sample: int = 5, seed: int = 0) -> CheckReport:
    """Check `adapter` on one run directory (`run_root`); the run and the adapter's declared inputs must stay unchanged."""
    run_root = Path(run_root)
    report = CheckReport()
    before = _snapshot_inputs(adapter, run_root)
    known_tasks = {t for g in graphs or [] for t in g.tasks}

    refs = list(adapter.discover(run_root))
    report.units_found = len(refs)
    if not refs:
        report.errors.append("discover() found no run units")
        return report
    ids = [r.id for r in refs]
    if len(ids) != len(set(ids)):
        report.errors.append("run unit ids are not unique: " + ", ".join(sorted({i for i in ids if ids.count(i) > 1})[:5]))
    if [r.id for r in adapter.discover(run_root)] != ids:
        report.errors.append("discover() order or ids differ between two calls (must be deterministic)")

    picked = refs if len(refs) <= sample else sorted(random.Random(seed).sample(refs, sample), key=lambda r: ids.index(r.id))
    covered_known = 0
    unknown_tasks: list[str] = []
    for ref in picked:
        report.units_checked.append(ref.id)
        where = f"[{ref.id}]"
        try:
            doc = adapter.to_trace(ref)
        except Exception as exc:
            report.errors.append(f"{where} to_trace raised {type(exc).__name__}: {exc}")
            continue
        if json.dumps(doc, sort_keys=True, default=str) != json.dumps(adapter.to_trace(ref), sort_keys=True, default=str):
            report.errors.append(f"{where} to_trace is not deterministic")
        errs = schema_errors(doc, "trace")
        if errs:
            report.errors += [f"{where} schema: {e}" for e in errs[:10]]
            continue
        run, steps = doc["run"], doc["steps"]
        if run["run_unit_id"] != ref.id:
            report.errors.append(f"{where} run.run_unit_id {run['run_unit_id']!r} differs from the discovered id")
        if [s["index"] for s in steps] != list(range(len(steps))):
            report.errors.append(f"{where} step indices must be 0..n-1 in order")
        times = [s["t"] for s in steps if s.get("t") is not None]
        if times != sorted(times):
            report.warnings.append(f"{where} step times are not non-decreasing")
        for task in run["tasks_covered"]:
            if task not in doc["final_answers"]:
                report.errors.append(f"{where} no final_answers entry for {task!r} (use status 'missing')")
            if task in known_tasks:
                covered_known += 1
            elif known_tasks:
                unknown_tasks.append(task)
        extra_answers = set(doc["final_answers"]) - set(run["tasks_covered"])
        if extra_answers:
            report.errors.append(f"{where} final_answers for tasks not in tasks_covered: {sorted(extra_answers)}")
        caps = doc["capabilities"]
        if caps["raw_steps"] and not any(s.get("output") for s in steps):
            report.errors.append(f"{where} capabilities.raw_steps is true but no step has output")
        if not caps["raw_steps"] and any(s.get("output") for s in steps):
            report.warnings.append(f"{where} steps carry output but capabilities.raw_steps is false")
        if caps["stage_log"] != bool(doc["stage_log"]):
            report.errors.append(f"{where} capabilities.stage_log disagrees with stage_log content")
        for flag, value in caps.items():
            report.capabilities[flag] = report.capabilities.get(flag, 0) + int(bool(value))
        for answer in doc["final_answers"].values():
            report.statuses[answer["status"]] = report.statuses.get(answer["status"], 0) + 1

    if unknown_tasks:
        report.warnings.append(f"{len(unknown_tasks)} covered task(s) have no graph yet, e.g. {sorted(set(unknown_tasks))[:5]}")
    if known_tasks and covered_known == 0 and report.units_checked:
        # graphs may cover only part of a benchmark; a format mismatch shows up as zero matches overall
        report.warnings.append("none of the checked units cover a task known to the provided graphs "
                               "(fine if the graphs cover other tasks; otherwise check how task ids are read)")
    if _snapshot_inputs(adapter, run_root) != before:
        report.errors.append("the run or the adapter's inputs changed during the check (adapters must be read-only)")
    return report

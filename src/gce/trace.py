"""Trace model: the normalized record of one run unit (gce.trace.v1)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .io import load_doc

TRACE_TAG = "gce.trace.v1"


@dataclass
class Step:
    index: int
    kind: str
    tool: str | None = None
    input: str | None = None
    output: str | None = None
    path: str | None = None
    t: float | None = None
    tasks: list[str] = field(default_factory=list)
    status: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        return cls(d["index"], d["kind"], d.get("tool"), d.get("input"), d.get("output"), d.get("path"), d.get("t"), list(d.get("tasks", [])), d.get("status"))


@dataclass
class StageEntry:
    position: int
    action: str
    outputs: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    tasks: list[str] | str | None = None
    step: Any = None
    artifacts: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, position: int, d: dict) -> "StageEntry":
        return cls(position, d["action"], dict(d.get("outputs", {})), dict(d.get("params", {})), d.get("tasks"), d.get("step"), list(d.get("artifacts", [])))

    def serves(self, task_ids: list[str]) -> bool:
        """Whether this entry may evidence a node used by any of `task_ids`."""
        if self.tasks is None or self.tasks == "shared":
            return True
        return any(t in self.tasks for t in task_ids)


@dataclass
class FinalAnswer:
    status: str
    answer: Any = None
    candidates: list[dict] = field(default_factory=list)
    summary: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "FinalAnswer":
        return cls(d["status"], d.get("answer"), list(d.get("candidates", [])), d.get("summary"))

    def values(self) -> list[Any]:
        """The answer followed by any candidate answers."""
        vals = [] if self.answer is None else [self.answer]
        return vals + [c["answer"] for c in self.candidates if c.get("answer") is not None and c.get("answer") != self.answer]


@dataclass
class Trace:
    run: dict
    capabilities: dict
    steps: list[Step]
    stage_log: list[StageEntry]
    final_answers: dict[str, FinalAnswer]
    extras: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Trace":
        return cls(
            run=dict(d["run"]),
            capabilities=dict(d["capabilities"]),
            steps=[Step.from_dict(s) for s in d.get("steps", [])],
            stage_log=[StageEntry.from_dict(i, e) for i, e in enumerate(d.get("stage_log", []))],
            final_answers={k: FinalAnswer.from_dict(v) for k, v in d.get("final_answers", {}).items()},
            extras=dict(d.get("extras", {})),
            raw=d,
        )

    @classmethod
    def load(cls, path: str | Path) -> "Trace":
        return cls.from_dict(load_doc(path))

    @property
    def run_unit_id(self) -> str:
        return self.run["run_unit_id"]

    @property
    def tasks_covered(self) -> list[str]:
        return list(self.run["tasks_covered"])

    def raw_text(self) -> str:
        """All tool outputs concatenated; used to corroborate self-reported values."""
        return "\n".join(s.output for s in self.steps if s.output)


def empty_trace(run_unit_id: str, tasks: list[str], **run: Any) -> dict:
    """Skeleton trace.v1 document for adapters to fill."""
    return {
        "schema": TRACE_TAG,
        "run": {"run_unit_id": run_unit_id, "tasks_covered": list(tasks), **run},
        "capabilities": {"raw_steps": False, "stage_log": False, "replayable_code": False},
        "steps": [],
        "stage_log": [],
        "final_answers": {},
        "extras": {},
    }

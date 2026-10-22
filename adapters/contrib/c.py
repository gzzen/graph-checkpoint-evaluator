"""Adapter for one condition-c run (any level: c_minimal, c_single, c_blind): question-local reflective repairs
over a frozen condition-b parent run. Condition c = the b parent with every repaired question replaced by its
repair, so this adapter reads both runs; wherever they differ, the repair wins.

Pass the c run directory, e.g.
    20261022T060324Z/runs/20261022T060324Z_b_c_minimal

Written by following adapters/ADAPTER_PROMPT.md against that run (read-only), whose controller was still running
when the pack was taken: 48 rescue targets, 4 repair workspaces, 2 of them finished.

Layout:
    <pack>/runs/<parent>_<level>/                 THE RUN: <parent> is the b run, <level> = protocol.level
      protocol.json                               level, revision, policy, lineage, model, agent (cli, version),
                                                  question_ids (the rescue targets: b questions judged No),
                                                  parent_generation_sha256 (checked against the parent's file)
      prompt_snapshot.json, code_snapshot/        the repair prompt and controller sources (not read)
      frozen_parent_packets/<question_id>/        b capsule result, grounding and scripts copied for the repair
                                                  (not read: the parent run itself is read through b.py)
      repair_questions/<question_id>/<short_id>__<capsule_uuid>/      one repair = one agent context, one question
        questions.json                            [the question] -> tasks_covered, group_id
        question_only.json, feedback.json         the question and the b verdict the agent was given ("no")
        primary_result.json                       the frozen b result for this question (the repair's start point)
        primary_provenance.json, primary_grounding.json, repair_packet.json   the rest of the repair input
        event_timeline.json                       OpenCode events -> steps (events.jsonl repeats them, not read)
        repair_result.json                        schema-valid repair proposal (written by the controller only
                                                  when the agent's final JSON passed its schema check)
        final_result.json                         the controller's c output: the repair when accepted, else the
                                                  frozen b result (fallback) -> final_answers
        run_manifest.json                         controller_status, accepted, failure_reason, fallback_to_primary,
                                                  repair_provenance, grounding, usage, event_metrics
        analysis/                                 grounding.json, scripts/ (b's scripts copied in plus the agent's)
      A repair workspace without run_manifest.json was prepared but never finished (the controller writes events,
      results and the manifest only when the call ends), so it has no c output yet.
    <pack>/runs/<parent>/                         the frozen b run, read with adapters/contrib/b.py

Units, sorted by run_unit_id:
    - repair units <experiment_id>/<tag>/<question_id>: every finished repair; condition = protocol.level.
    - parent units <experiment_id>/<tag>/<short_id>: the b capsule trace (steps, answers, extras exactly as b.py
      gives them) for the capsule's questions that have no finished repair, i.e. questions locked by b, not
      targeted, not yet repaired, or whose repair never finished (the controller falls back to b for those). A
      capsule whose questions were all repaired has no parent unit. extras.source says which kind a unit is.

Mapping notes (repair units; parent units are as in b.py plus extras.c_* bookkeeping):
    - status: controller_status passed -> completed; failed / interrupted_* -> failed.
    - steps, capabilities.raw_steps: as for b (_native_b_common.opencode_steps); the final JSON message is left out.
    - final_answers, option `answers`:
        controller (default): final_result.json, the controller's c output (b's answer when the repair failed
            validation); condition = <level>.
        proposed: what the repair agent proposed, ignoring controller validation: repair_result.json, else the
            final message's JSON; final_result.json when neither names the question. condition = <level>_proposed,
            and run_unit_ids <experiment_id>/<tag>_proposed/..., so both readings can be scored into one report.
      extras.primary_answer keeps b's answer, extras.proposed_repair the agent's results and repair_records,
      extras.final_answer_source names the file used.
    - replayable_code: a runnable (.py/.r/.sh) file the agent wrote, or one its repair record lists in
      execution.source_files, exists in the workspace. Scripts copied in from b do not count.
    - c_blind's pre-repair phase (blind_workspace) is not mapped; its path is kept in extras.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Iterable

from gce.adapters.base import Adapter, RunUnitRef
from gce.trace import empty_trace


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"gce_adapter_{name}", Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_common = _load("_native_b_common")
_b = _load("b")
load = _common.load

ANSWER_MODES = ("controller", "proposed")


class CRun:
    """Where one c run's files are: the run, its protocol, and the frozen b parent run next to it."""

    def __init__(self, run: Path):
        self.run = Path(run).resolve()
        if not self.run.is_dir():
            raise FileNotFoundError(f"{run}: not a directory")
        self.protocol = load(self.run / "protocol.json")
        level = (self.protocol or {}).get("level") or ""
        if not level.startswith("c_") or not (self.run / "repair_questions").is_dir():
            raise ValueError(f"{self.run}: not a condition-c run (expected protocol.json with level c_* and "
                             "repair_questions/); for a b run use adapters/contrib/b.py")
        self.level = level
        if not self.run.name.endswith("_" + level):
            raise ValueError(f"{self.run}: run name does not end with its protocol level _{level}")
        self.parent = self.run.parent / self.run.name[:-len(level) - 1]
        self.parent_ctl = _common.ControllerRun(self.parent)
        expected = self.protocol.get("parent_generation_sha256")
        actual = hashlib.sha256(self.parent_ctl.generation_path.read_bytes()).hexdigest()
        if expected and expected != actual:
            raise ValueError(f"{self.run}: protocol.parent_generation_sha256 {expected} does not match "
                             f"{self.parent_ctl.generation_path} ({actual}); the b parent in this pack is not the one repaired")
        self.experiment_id = self.parent_ctl.experiment_id
        self.tag = self.run.name[len(self.experiment_id) + 1:] if self.run.name.startswith(self.experiment_id + "_") else self.run.name

    def repairs(self) -> dict[str, list[Path]]:
        """question_id -> repair workspaces in name order (one per question unless a level retries)."""
        out: dict[str, list[Path]] = {}
        for qdir in sorted(p for p in (self.run / "repair_questions").iterdir() if p.is_dir()):
            for unit in sorted(p for p in qdir.iterdir() if p.is_dir() and (p / "questions.json").is_file()):
                out.setdefault(qdir.name, []).append(unit)
        return out


def _finished(unit: Path) -> bool:
    return (unit / "run_manifest.json").is_file()


def _results_for(doc, qid: str) -> dict | None:
    if not isinstance(doc, dict):
        return None
    return next((r for r in doc.get("results") or [] if isinstance(r, dict) and r.get("question_id") == qid), None)


class CAdapter(Adapter):
    name = "c"

    def __init__(self, **options):
        super().__init__(**options)
        self.answers = options.get("answers", "controller")
        if self.answers not in ANSWER_MODES:
            raise ValueError(f"option answers={self.answers!r}: expected one of {ANSWER_MODES}")
        self.b = _b.BAdapter()

    def inputs(self, run: Path) -> list[Path]:
        ctl = CRun(run)
        return [ctl.parent, *ctl.parent_ctl.inputs()]

    def condition(self, ctl: CRun) -> str:
        return ctl.level if self.answers == "controller" else f"{ctl.level}_proposed"

    def discover(self, run: Path) -> Iterable[RunUnitRef]:
        ctl = CRun(run)
        prefix = f"{ctl.experiment_id}/{ctl.tag}" + ("" if self.answers == "controller" else "_proposed")
        targets = set(ctl.protocol.get("question_ids") or [])
        repairs = ctl.repairs()
        refs = []
        superseded: set[str] = set()
        for qid, units in repairs.items():
            finished = [u for u in units if _finished(u)]
            if not finished:
                continue
            superseded.add(qid)
            chosen = finished[-1]
            others = [u.relative_to(ctl.run).as_posix() for u in units if u != chosen]
            refs.append(RunUnitRef(f"{prefix}/{qid}", str(chosen), meta={"kind": "repair", "run": str(ctl.run), "other_attempts": others}))
        for b_ref in self.b.discover(ctl.parent):
            questions = [q["question_id"] for q in self.b.questions(Path(b_ref.location))]
            remaining = [q for q in questions if q not in superseded]
            if not remaining:
                continue
            refs.append(RunUnitRef(f"{prefix}/{b_ref.id.rsplit('/', 1)[-1]}", b_ref.location, meta={
                "kind": "parent", "run": str(ctl.run), "b_ref": b_ref, "tasks": remaining,
                "superseded": [q for q in questions if q in superseded],
                "unfinished": [q for q in remaining if q in repairs],
                "pending": [q for q in remaining if q in targets and q not in repairs]}))
        return sorted(refs, key=lambda r: r.id)

    def to_trace(self, ref: RunUnitRef) -> dict:
        ctl = CRun(ref.meta["run"])
        if ref.meta["kind"] == "parent":
            return self.parent_trace(ctl, ref)
        return self.repair_trace(ctl, ref)

    def parent_trace(self, ctl: CRun, ref: RunUnitRef) -> dict:
        b_ref = ref.meta["b_ref"]
        trace = self.b.to_trace(b_ref)
        tasks = ref.meta["tasks"]
        trace["run"].update(run_unit_id=ref.id, condition=self.condition(ctl), tasks_covered=tasks)
        trace["final_answers"] = {t: trace["final_answers"][t] for t in tasks}
        trace["extras"] = {
            "source": "frozen_b_parent",
            "parent_run": ctl.parent.name,
            "parent_run_unit_id": b_ref.id,
            "c_level": ctl.level,
            "c_superseded_questions": ref.meta["superseded"],
            "c_unfinished_repairs": ref.meta["unfinished"],
            "c_pending_rescue_targets": ref.meta["pending"],
            **trace["extras"],
        }
        return trace

    def repair_trace(self, ctl: CRun, ref: RunUnitRef) -> dict:
        unit = Path(ref.location)
        protocol = ctl.protocol
        agent = protocol.get("agent") or {}
        cli = agent.get("cli")
        question = load(unit / "questions.json")[0]
        qid = question["question_id"]
        manifest = load(unit / "run_manifest.json") or {}
        controller = manifest.get("controller_status")
        trace = empty_trace(
            ref.id, [qid], experiment=ctl.experiment_id, condition=self.condition(ctl), seed=None,
            model=protocol.get("model"),
            group_id=f"{question.get('tag')}-v{question.get('version')}/{question['short_id']}" if question.get("tag") else None,
            status=_common.STATUS.get(controller, "failed" if controller else "unknown"),
            started_at=None, duration_seconds=manifest.get("runtime_seconds"),
        )
        stats: dict = {}
        extras: dict = {"agent_errors": [], "nonzero_exits": [], "usage": None}
        trace["extras"]["_workspace"] = manifest.get("workspace")

        events = load(unit / "event_timeline.json") or []
        texts = _common.message_texts(cli, events)
        final_msg = _common.parse_final(texts[-1][1]) if texts else None
        final_index = texts[-1][0] if final_msg is not None else None
        if cli == "codex":
            _common.codex_steps(trace, events, final_index, stats, extras)
        elif cli == "opencode":
            _common.opencode_steps(trace, events, final_index, stats, extras)
        else:
            raise ValueError(f"{ctl.run}: unsupported agent cli {cli!r}")

        final_result = load(unit / "final_result.json")
        repair_result = load(unit / "repair_result.json")
        proposal, proposal_source = ((repair_result, "repair_result.json") if isinstance(repair_result, dict)
                                     else (final_msg, "final_message") if final_msg is not None else (None, None))
        primary = _results_for(load(unit / "primary_result.json"), qid)
        if self.answers == "proposed" and _results_for(proposal, qid) is not None:
            chosen, source = _results_for(proposal, qid), proposal_source
        else:
            chosen, source = _results_for(final_result, qid), "final_result.json" if _results_for(final_result, qid) else None
        trace["final_answers"][qid] = _common.answer_entry(chosen) if chosen is not None else {"status": "missing"}

        records = [r for r in (proposal or {}).get("repair_records") or [] if isinstance(r, dict)]
        written = [s["path"] for s in trace["steps"] if s["kind"] == "file_write" and s.get("path")]
        declared = written + [f for r in records for f in ((r.get("execution") or {}).get("source_files") or [])]
        runnable = sorted({p for p in declared if Path(p).suffix.lower() in _common.RUNNABLE_SUFFIXES and (unit / p).is_file()})
        trace["capabilities"] = {"raw_steps": any(s.get("output") for s in trace["steps"] if s["kind"] in ("exec", "tool_call", "file_read")),
                                 "stage_log": False, "replayable_code": bool(runnable)}
        metrics = manifest.get("event_metrics") or {}
        analysis = unit / "analysis"
        stderr = (unit / "stderr.log").read_text(encoding="utf-8", errors="replace") if (unit / "stderr.log").is_file() else None
        trace["extras"] = {
            "source": "c_repair",
            "c_level": ctl.level,
            "c_revision": protocol.get("revision"),
            "c_policy": protocol.get("policy"),
            "c_lineage": protocol.get("lineage"),
            "parent_run": ctl.parent.name,
            "parent_run_unit_id": f"{ctl.parent_ctl.experiment_id}/{ctl.parent_ctl.tag}/{question['short_id']}" if ctl.parent_ctl.tag
                                  else f"{ctl.parent_ctl.experiment_id}/{question['short_id']}",
            "agent_cli": cli,
            "agent_cli_version": agent.get("cli_version") or agent.get("cli_major_version"),
            "reasoning_effort": protocol.get("reasoning_effort"),
            "attempt_dir": unit.relative_to(ctl.run).as_posix(),
            "attempts": ref.meta.get("other_attempts") or [],
            "attempt_count": manifest.get("attempt_count"),
            "total_agent_calls": manifest.get("total_agent_calls"),
            "controller_status": controller,
            "accepted": manifest.get("accepted"),
            "return_code": manifest.get("return_code"),
            "failure_reason": manifest.get("failure_reason"),
            "policy_violation": manifest.get("policy_violation"),
            "fallback_to_primary": manifest.get("fallback_to_primary"),
            "fallback_to_primary_question_ids": manifest.get("fallback_to_primary_question_ids"),
            "repair_provenance": manifest.get("repair_provenance"),
            "grounding": manifest.get("grounding"),
            "blind_workspace": manifest.get("blind_workspace"),
            "feedback": (load(unit / "feedback.json") or {}).get("items"),
            "primary_answer": _common.answer_entry(primary) if primary is not None else None,
            "final_answer_source": source,
            "final_result_is_primary": _results_for(final_result, qid) == primary if primary is not None else None,
            "proposed_repair": {"source": proposal_source, "results": proposal.get("results"),
                                "repair_records": proposal.get("repair_records")} if isinstance(proposal, dict) else None,
            "final_text_head": None if final_msg is not None or not texts else texts[-1][1][:500],
            "replayable_sources": runnable,
            "analysis_files": [{"path": p.relative_to(unit).as_posix(), "bytes": p.stat().st_size}
                               for p in sorted(analysis.rglob("*")) if p.is_file()] if analysis.is_dir() else [],
            "command_class_counts": {k: v for k, v in metrics.items() if k.endswith("_count")},
            "command_classification": metrics.get("command_classification"),
            "stderr": _common.clip(stderr, stats) if stderr and stderr.strip() else None,
            **extras,
            "usage": manifest.get("usage") or extras["usage"],
            **stats,
        }
        return trace

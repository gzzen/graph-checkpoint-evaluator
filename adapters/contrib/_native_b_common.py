"""Shared code for the native_b controller adapters (`native.py`, `b.py`); not an adapter to load by itself.

Every run of the native_b controller has the same shape, whatever the condition:

    <pack>/                                           a pack (or the controller's source root)
      PACK_MANIFEST.json                              experiment_id (optional; else derived from the run name)
      analysis/<run>_generation.json                  manifest (model, agent.cli, ...) and one entry per unit whose
                                                      `workspace` is the unit's final attempt directory
      analysis/<run>_manifest_resume_<stamp>.json     resume plans: reused_terminal_units, interrupted_workspaces
      runs/<run>/                                     THE RUN an adapter is given: <run> = <experiment_id>[_<tag>],
                                                      tag = <condition>_(rep|seed)<NN>, e.g. native_rep01, b_seed01
        <slot NNNN>/<unit>/                           one attempt; 0000 first attempt, 0001 retry
        resume_<stamp>/<slot NNNN>/<unit>/            continuation after the original run halted

    An attempt directory holds run_manifest.json (controller verdict, event_metrics, code_provenance, ...),
    event_timeline.json ([{elapsed_seconds, event}]; events.jsonl has the same events and is not read), the result
    file (absent when the attempt failed), analysis/ (compliance_audit.json, scripts/, outputs/), stderr.log,
    environment_contract.json and isolation_boundary.json. Attempts cut off by a halt have no manifest or events.

Trace mapping shared by all conditions:
    - run unit: the attempt the generation entry names (the last attempt in stage order without one); every
      other attempt of the unit is summarized in extras.attempts, noted "interrupted_by_halt" when a resume plan
      lists it. run_unit_id: <experiment_id>[/<tag>]/<unit key>.
    - experiment: experiment_id; condition and seed from the tag (the adapter's condition and null without one);
      model: generation manifest model; agent CLI and version in extras.
    - status: controller_status passed -> completed; anything else (failed, interrupted_*) -> failed.
    - steps, Codex events (item.completed only; item.started duplicates them):
        command_execution -> exec (the `/usr/bin/bash -lc` wrapper is unquoted to the script it runs),
        file_access/apply_patch -> file_write per changed path (patch content is not logged: input null,
        status = change kind add/update/delete), agent_message -> message, reasoning -> other (summary text in
        output, so it is kept but never counts as evidence), error / turn.failed -> other.
      OpenCode events: tool parts shell -> exec, write/edit -> file_write, read -> file_read, others ->
        tool_call (state.error as output when the call failed); text -> message;
        controller.policy_violation -> other.
      The final message is left out of steps when it is the JSON final answer. t = elapsed_seconds. Step paths
      under the attempt workspace (run_manifest.workspace) are made relative to it.
    - final_answers: the result file; without it, the final message when it carries the answer JSON (bare, in a
      ```json fence, or as an object ending the message after prose); else missing. extras.final_answer_source
      says which, and extras.accepted whether the controller accepted the attempt.
    - capabilities: raw_steps from tool outputs; no stage log; replayable_code when runnable persisted sources
      (code_provenance.runnable_source_files, or source_files with a .py/.r/.sh suffix when only those are
      recorded) exist in the attempt directory.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Iterable

from gce.adapters.base import Adapter, RunUnitRef
from gce.trace import empty_trace

MAX_OUTPUT_CHARS = 200_000
OPENCODE_KIND = {"shell": "exec", "bash": "exec", "write": "file_write", "edit": "file_write", "read": "file_read"}
STATUS = {"passed": "completed"}
BASH_WRAPPER = "/usr/bin/bash -lc "
RUNNABLE_SUFFIXES = (".py", ".r", ".sh")  # native code_acceptance_policy.runnable_suffixes


def clip(text: str | None, stats: dict) -> str | None:
    if text is None or len(text) <= MAX_OUTPUT_CHARS:
        return text
    stats["truncated_outputs"] = stats.get("truncated_outputs", 0) + 1
    half = MAX_OUTPUT_CHARS // 2
    return f"{text[:half]}\n[... {len(text) - 2 * half} characters truncated by adapter ...]\n{text[-half:]}"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def parse_final(text: str) -> dict | None:
    """Final answer JSON from the agent's last message: plain JSON, the first fenced ```json block, or a JSON
    object that starts a line and ends the message (prose followed by the answer object)."""
    stripped = text.strip()
    try:
        doc = json.loads(stripped)
        return doc if isinstance(doc, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if m:
        try:
            doc = json.loads(m.group(1))
            return doc if isinstance(doc, dict) else None
        except json.JSONDecodeError:
            pass
    for start in re.finditer(r"(?m)^\{", stripped):
        try:
            doc, end = json.JSONDecoder().raw_decode(stripped, start.start())
        except json.JSONDecodeError:
            continue
        if end == len(stripped) and isinstance(doc, dict):
            return doc
    return None


def answer_entry(result: dict) -> dict:
    candidates = [c for c in result.get("candidates") or [] if isinstance(c, dict) and "answer" in c]
    status = result.get("status") if result.get("status") in ("resolved", "unresolved") else "unresolved"
    entry = {"status": status, "answer": candidates[0]["answer"] if candidates and status == "resolved" else None, "candidates": candidates}
    if result.get("summary") is not None:
        entry["summary"] = str(result["summary"])
    return entry


def unwrap_bash(command: str) -> tuple[str, bool]:
    """`/usr/bin/bash -lc '<script>'` -> `<script>`, when the command is exactly that argv."""
    if command.startswith(BASH_WRAPPER):
        try:
            argv = shlex.split(command)
        except ValueError:
            return command, False
        if len(argv) == 3:
            return argv[2], True
    return command, False


def step(trace: dict, kind: str, tool, inp, out, t, stats: dict, path=None, status=None) -> int:
    index = len(trace["steps"])
    workspace = trace["extras"].get("_workspace")
    if path and workspace and (path == workspace or path.startswith(workspace + "/")):
        path = path[len(workspace) + 1:] or "."
    trace["steps"].append({"index": index, "kind": kind, "tool": tool, "input": inp, "output": clip(out, stats),
                           "path": path, "t": t, "status": status})
    return index


def codex_steps(trace: dict, events: list, final_index: int | None, stats: dict, extras: dict) -> None:
    for i, e in enumerate(events):
        ev, t = e["event"], e.get("elapsed_seconds")
        item = ev.get("item") or {}
        if ev.get("type") in ("error", "turn.failed"):
            message = ev.get("message") or (ev.get("error") or {}).get("message")
            step(trace, "other", ev["type"], None, message, t, stats, status="error")
            if ev["type"] == "error":  # turn.failed repeats the preceding error's message
                extras["agent_errors"].append(message)
            continue
        if ev.get("type") == "turn.completed":
            extras["usage"] = ev.get("usage")
        if ev.get("type") != "item.completed":
            continue
        kind = item.get("type")
        if kind == "command_execution":
            command, unwrapped = unwrap_bash(item.get("command") or "")
            stats["unwrapped_bash_commands"] = stats.get("unwrapped_bash_commands", 0) + int(unwrapped)
            index = step(trace, "exec", item.get("agent_tool") or "shell", command, item.get("aggregated_output"), t, stats,
                         status=item.get("status"))
            if item.get("exit_code") not in (0, None):
                extras["nonzero_exits"].append({"step": index, "exit_code": item["exit_code"]})
        elif kind == "file_access":
            for change in item.get("changes") or []:
                path = change.get("path")
                step(trace, "file_write", item.get("agent_tool"), None, None, t, stats, path=path, status=change.get("kind"))
        elif kind == "agent_message" and i != final_index:
            step(trace, "message", None, None, item.get("text"), t, stats)
        elif kind == "reasoning":
            step(trace, "other", "reasoning", None, item.get("text"), t, stats)


def opencode_steps(trace: dict, events: list, final_index: int | None, stats: dict, extras: dict) -> None:
    usage = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}
    for i, e in enumerate(events):
        ev, t = e["event"], e.get("elapsed_seconds")
        part = ev.get("part") or {}
        if ev.get("type") == "controller.policy_violation":
            detail = {k: ev.get(k) for k in ("policy", "reason", "command", "path", "action")}
            step(trace, "other", ev["type"], ev.get("command"), json.dumps(detail, sort_keys=True), t, stats, status="error")
        elif ev.get("type") == "error":
            message = json.dumps(ev.get("error"), sort_keys=True)
            step(trace, "other", "error", None, message, t, stats, status="error")
            extras["agent_errors"].append(message)
        elif part.get("type") == "step-finish":
            tokens = part.get("tokens") or {}
            for key in ("input", "output", "reasoning"):
                usage[key] += tokens.get(key) or 0
            usage["cache_read"] += (tokens.get("cache") or {}).get("read") or 0
            usage["cache_write"] += (tokens.get("cache") or {}).get("write") or 0
            usage["cost"] = round(usage["cost"] + (part.get("cost") or 0), 9)
        elif part.get("type") == "tool":
            state = part.get("state") or {}
            inp = state.get("input") or {}
            tool = part.get("tool")
            if tool in ("shell", "bash"):
                text_in = inp.get("command")
            elif tool == "write":
                text_in = inp.get("content")
            elif tool == "edit":
                text_in = json.dumps({k: v for k, v in inp.items() if k != "path"}, ensure_ascii=False, sort_keys=True)
            else:
                text_in = json.dumps(inp, ensure_ascii=False, sort_keys=True)
            output = state.get("output") if state.get("status") != "error" else state.get("error")
            if output is not None and not isinstance(output, str):
                output = json.dumps(output, sort_keys=True)
            index = step(trace, OPENCODE_KIND.get(tool, "tool_call"), tool, text_in, output, t, stats,
                         path=inp.get("path") or inp.get("filePath") if tool not in ("grep", "webfetch") else None,
                         status=state.get("status"))
            exit_code = ((state.get("metadata") or {}).get("metadata") or {}).get("exit")
            if exit_code not in (0, None):
                extras["nonzero_exits"].append({"step": index, "exit_code": exit_code})
        elif part.get("type") == "text" and i != final_index:
            step(trace, "message", None, None, part.get("text"), t, stats)
    extras["usage"] = usage


def message_texts(cli: str, events: list) -> list[tuple[int, str]]:
    if cli == "codex":
        return [(i, e["event"]["item"].get("text") or "") for i, e in enumerate(events)
                if e["event"].get("type") == "item.completed" and (e["event"].get("item") or {}).get("type") == "agent_message"]
    return [(i, e["event"]["part"].get("text") or "") for i, e in enumerate(events) if (e["event"].get("part") or {}).get("type") == "text"]


TAG = re.compile(r"^(?P<condition>[a-z][a-z0-9]*)_(?:rep|seed)(?P<n>\d+)$")
RUN_NAME = re.compile(r"^(?P<experiment>.+?)_(?P<tag>[a-z][a-z0-9]*_(?:rep|seed)\d+)$")


def attempt_summary(attempt_dir: Path, run: Path, result_file: str, note: str | None = None) -> dict:
    m = load(attempt_dir / "run_manifest.json") or {}
    return {"attempt_index": m.get("attempt_index"), "dir": attempt_dir.relative_to(run).as_posix(), "note": note,
            "has_run_manifest": bool(m), "has_events": (attempt_dir / "event_timeline.json").is_file(),
            "controller_status": m.get("controller_status"), "return_code": m.get("return_code"),
            "runtime_seconds": m.get("runtime_seconds"), "failure_reason": m.get("failure_reason"),
            "policy_violation": m.get("policy_violation"), "relay_failure": m.get("relay_failure"),
            "has_result": (attempt_dir / result_file).is_file()}


class ControllerRun:
    """Where one run's files are: the run directory and the pack-level files that describe it."""

    def __init__(self, run: Path):
        self.run = Path(run).resolve()
        if not self.run.is_dir():
            raise FileNotFoundError(f"{run}: not a directory")
        self.pack = self.run.parent.parent
        self.name = self.run.name
        pack_manifest = load(self.pack / "PACK_MANIFEST.json") or {}
        exp_id = pack_manifest.get("experiment_id")
        if exp_id and (self.name == exp_id or self.name.startswith(exp_id + "_")):
            self.experiment_id, self.tag = exp_id, self.name[len(exp_id) + 1:]
        elif (m := RUN_NAME.match(self.name)):
            self.experiment_id, self.tag = m["experiment"], m["tag"]
        else:
            self.experiment_id, self.tag = self.name, ""
        self.generation_path = self.pack / "analysis" / f"{self.name}_generation.json"
        if not self.generation_path.is_file():
            raise FileNotFoundError(f"{self.run}: expected the controller's generation file at {self.generation_path} "
                                    "(pass a run directory <pack>/runs/<run>)")
        self.generation = load(self.generation_path)
        self.resume_plans = sorted((self.pack / "analysis").glob(f"{self.name}_manifest_resume_*.json"))

    def inputs(self) -> list[Path]:
        return [self.generation_path, *self.resume_plans] + [p for p in [self.pack / "PACK_MANIFEST.json"] if p.is_file()]

    def stages(self) -> list[tuple[str, Path]]:
        return [("initial", self.run)] + [(p.name, p) for p in sorted(self.run.iterdir()) if p.is_dir() and p.name.startswith("resume_")]


class ControllerRunAdapter(Adapter):
    """One run of the native_b controller -> one trace per unit. Subclasses define the unit type."""

    condition = ""            # condition when the run name carries no tag
    unit_file = ""            # file that marks a unit directory and lists its question(s)
    result_file = ""          # controller-accepted answer file
    entries_key = ""          # generation list of units: "records" (questions) or "episodes" (capsules)
    entry_key = ""            # field of a generation entry naming the unit
    other_adapter = ""        # adapter to suggest when the run holds the other unit type

    def unit_key(self, unit: Path) -> str | None:
        raise NotImplementedError

    def questions(self, unit: Path) -> list[dict]:
        raise NotImplementedError

    def answers(self, doc: dict, tasks: list[str]) -> tuple[dict, list]:
        """final_answers entries found in an answer document, and results naming no covered task."""
        raise NotImplementedError

    def unit_extras(self, unit: Path, run_manifest: dict, doc: dict | None) -> dict:
        return {}

    def inputs(self, run: Path) -> list[Path]:
        """Files outside the run directory that this adapter reads (covered by adapter-check's read-only check)."""
        return ControllerRun(run).inputs()

    def discover(self, run: Path) -> Iterable[RunUnitRef]:
        ctl = ControllerRun(run)
        entries = ctl.generation.get(self.entries_key)
        if entries is None:
            other = "records" if self.entries_key == "episodes" else "episodes"
            if other in ctl.generation:
                raise ValueError(f"{ctl.run}: the generation file lists `{other}`, not `{self.entries_key}`; "
                                 f"this run has the other unit type, use {self.other_adapter}")
            entries = []
        final_workspace = {e.get(self.entry_key): e.get("workspace") for e in entries}
        m = TAG.match(ctl.tag)
        condition, seed = (m["condition"], int(m["n"])) if m else (ctl.tag or self.condition, None)
        interrupted: set[str] = set()
        reused: set[str] = set()
        for plan_path in ctl.resume_plans:
            plan = load(plan_path) or {}
            interrupted |= set(plan.get("interrupted_workspaces") or [])
            reused |= set(plan.get("reused_terminal_units") or [])

        attempts: dict[str, list[tuple[str, Path]]] = {}
        for stage, stage_dir in ctl.stages():
            for slot in sorted(p for p in stage_dir.iterdir() if p.is_dir() and p.name.isdigit()):
                for unit in sorted(p for p in slot.iterdir() if p.is_dir() and (p / self.unit_file).is_file()):
                    attempts.setdefault(self.unit_key(unit), []).append((stage, unit))

        def suffix(unit: Path) -> str:
            return "/" + unit.relative_to(ctl.run.parent).as_posix()

        prefix = f"{ctl.experiment_id}/{ctl.tag}" if ctl.tag else ctl.experiment_id
        for key in sorted(attempts):
            found = attempts[key]
            workspace = final_workspace.get(key) or ""
            stage, chosen = next(((s, d) for s, d in found if workspace.endswith(suffix(d))), found[-1])
            others = [{"dir": str(d), "note": "interrupted_by_halt" if any(w.endswith(suffix(d)) for w in interrupted) else None}
                      for _, d in found if d != chosen]
            yield RunUnitRef(f"{prefix}/{key}", str(chosen), meta={
                "run": str(ctl.run), "condition": condition, "seed": seed, "other_attempts": others,
                "extras": {"run_tag": ctl.tag or None, "stage": stage, "reused_by_resume": key in reused}})

    def to_trace(self, ref: RunUnitRef) -> dict:
        unit = Path(ref.location)
        ctl = ControllerRun(ref.meta["run"])
        manifest = ctl.generation.get("manifest") or {}
        agent = manifest.get("agent") or {}
        cli = agent.get("cli")
        questions = self.questions(unit)
        question = questions[0]
        run_manifest = load(unit / "run_manifest.json") or {}
        tasks = [q["question_id"] for q in questions]
        controller = run_manifest.get("controller_status")
        trace = empty_trace(
            ref.id, tasks, experiment=ctl.experiment_id, condition=ref.meta["condition"], seed=ref.meta["seed"],
            model=manifest.get("model"),
            group_id=f"{question.get('tag')}-v{question.get('version')}/{question['short_id']}" if question.get("tag") else None,
            status=STATUS.get(controller, "failed" if controller else "unknown"),
            started_at=None, duration_seconds=run_manifest.get("runtime_seconds"),
        )
        stats: dict = {}
        extras: dict = {"agent_errors": [], "nonzero_exits": [], "usage": None}
        trace["extras"]["_workspace"] = run_manifest.get("workspace")  # absolute paths under it become relative

        events = load(unit / "event_timeline.json") or []
        texts = message_texts(cli, events)
        final_msg = parse_final(texts[-1][1]) if texts else None
        final_index = texts[-1][0] if final_msg is not None else None
        if cli == "codex":
            codex_steps(trace, events, final_index, stats, extras)
        elif cli == "opencode":
            opencode_steps(trace, events, final_index, stats, extras)
        else:
            raise ValueError(f"{ctl.run}: unsupported agent cli {cli!r}")

        result = load(unit / self.result_file)
        doc, source = (result, self.result_file) if isinstance(result, dict) else (final_msg, "final_message" if final_msg is not None else None)
        found, foreign = self.answers(doc, tasks) if doc is not None else ({}, [])
        trace["final_answers"].update(found)
        for task in tasks:
            trace["final_answers"].setdefault(task, {"status": "missing"})

        provenance = run_manifest.get("code_provenance") or {}
        # condition b records only source_files: keep those with the native policy's runnable suffixes
        declared = provenance.get("runnable_source_files") or [f["path"] for f in provenance.get("source_files") or []
                                                                if Path(f["path"]).suffix.lower() in RUNNABLE_SUFFIXES]
        runnable = [p for p in declared if (unit / p).is_file()]
        trace["capabilities"] = {"raw_steps": any(s.get("output") for s in trace["steps"] if s["kind"] in ("exec", "tool_call", "file_read")),
                                 "stage_log": False, "replayable_code": bool(runnable)}
        metrics = run_manifest.get("event_metrics") or {}
        analysis = unit / "analysis"
        stderr = (unit / "stderr.log").read_text(encoding="utf-8", errors="replace") if (unit / "stderr.log").is_file() else None
        trace["extras"] = {
            "agent_cli": cli,
            "agent_cli_version": agent.get("cli_version") or agent.get("cli_major_version"),
            "reasoning_effort": manifest.get("reasoning_effort"),
            "attempt_dir": unit.relative_to(ctl.run).as_posix(),
            "attempt_index": run_manifest.get("attempt_index"),
            "attempts": [attempt_summary(Path(d["dir"]), ctl.run, self.result_file, d.get("note")) for d in ref.meta.get("other_attempts") or []],
            "retry_reason": run_manifest.get("retry_reason"),
            "controller_status": controller,
            "accepted": run_manifest.get("accepted"),
            "return_code": run_manifest.get("return_code"),
            "failure_reason": run_manifest.get("failure_reason"),
            "policy_violation": run_manifest.get("policy_violation"),
            "relay_failure": run_manifest.get("relay_failure"),
            "isolation_audit": run_manifest.get("isolation_audit"),
            "question_local_failures": run_manifest.get("question_local_failures"),
            "soft_warnings": run_manifest.get("soft_warnings"),
            "compliance_audit": load(analysis / "compliance_audit.json"),
            "code_provenance": provenance or None,
            "analysis_files": [{"path": p.relative_to(unit).as_posix(), "bytes": p.stat().st_size}
                               for p in sorted(analysis.rglob("*")) if p.is_file()] if analysis.is_dir() else [],
            "command_class_counts": {k: v for k, v in metrics.items() if k.endswith("_count")},
            "command_classification": metrics.get("command_classification"),
            "final_answer_source": source,
            "final_text_head": None if source or not texts else texts[-1][1][:500],
            "stderr": clip(stderr, stats) if stderr and stderr.strip() else None,
            **self.unit_extras(unit, run_manifest, doc),
            **extras,
            **ref.meta.get("extras", {}),
            **stats,
        }
        return trace

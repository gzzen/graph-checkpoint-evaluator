"""Adapter for the native diagnostic packs (one Codex pack, one OpenCode pack) of the BixBench native
executable-code experiment.

Written by following adapters/ADAPTER_PROMPT.md against dasc/1012_test_run/{codex,opencode}_native_diagnostic_*
(read-only). Both packs share one controller layout; only the agent event format differs, so one adapter
serves both and their traces have the same structure. Pass one pack directory as the run root.

Layout:

    <run_root>/                                   one pack = one experiment (one agent CLI, one model)
      PACK_MANIFEST.json                          experiment_id, file inventory
      analysis/<experiment_id>_generation.json    manifest (model, agent.cli, retry policy, ...) and one record
                                                  per question: the final attempt_index and attempt_history
      runs/<experiment_id>/
        <slot NNNN>/                              attempt slot; 0000 = first attempt, 0001 = retry
          <question_id>/                          one attempt of one question (one agent context)
            question.json                         question_id, short_id, tag, version
            run_manifest.json                     controller_status, accepted, return_code, runtime_seconds,
                                                  failure_reason, policy_violation, relay_failure, event_metrics,
                                                  code_provenance, soft_warnings, attempt_history, ...
            event_timeline.json                   [{elapsed_seconds, event}] (events.jsonl holds the same events
                                                  without elapsed_seconds; not read)
            question_result.json                  controller-accepted final answer {status, candidates, summary};
                                                  absent for failed attempts
            analysis/compliance_audit.json        persisted-source audit (absent for failed attempts)
            analysis/scripts/*.py                 persisted agent code; analysis/outputs/* agent outputs
            stderr.log, environment_contract.json, isolation_boundary.json

    A run unit is one question: the attempt named by the generation record (the highest slot when no record
    exists). Earlier attempts are summarized in extras.attempts.

Mapping notes:
    - tasks_covered: question.json question_id (one task per unit). group_id: <tag>-v<version>/<short_id>.
    - experiment: PACK_MANIFEST experiment_id; condition "native" (the experiment's only condition); seed null;
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
      under the attempt workspace (run_manifest.workspace) are made relative to it, as Codex already reports them.
    - final_answers: question_result.json; without it, the final message if it parses as JSON; else missing.
    - capabilities: raw_steps from tool outputs; no stage log; replayable_code when runnable persisted sources
      (code_provenance.runnable_source_files) exist in the attempt directory.
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


def clip(text: str | None, stats: dict) -> str | None:
    if text is None or len(text) <= MAX_OUTPUT_CHARS:
        return text
    stats["truncated_outputs"] = stats.get("truncated_outputs", 0) + 1
    half = MAX_OUTPUT_CHARS // 2
    return f"{text[:half]}\n[... {len(text) - 2 * half} characters truncated by adapter ...]\n{text[-half:]}"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def parse_final(text: str) -> dict | None:
    """Final answer JSON from the agent's last message: plain JSON, or the first fenced ```json block."""
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


def attempt_summary(attempt_dir: Path, root: Path, note: str | None = None) -> dict:
    m = load(attempt_dir / "run_manifest.json") or {}
    return {"attempt_index": m.get("attempt_index"), "dir": attempt_dir.relative_to(root).as_posix(), "note": note,
            "has_run_manifest": bool(m), "has_events": (attempt_dir / "event_timeline.json").is_file(),
            "controller_status": m.get("controller_status"), "return_code": m.get("return_code"),
            "runtime_seconds": m.get("runtime_seconds"), "failure_reason": m.get("failure_reason"),
            "policy_violation": m.get("policy_violation"), "relay_failure": m.get("relay_failure"),
            "has_question_result": (attempt_dir / "question_result.json").is_file()}


class NativeDiagnosticAdapter(Adapter):
    name = "native_diagnostic"

    def discover(self, run_root: Path) -> Iterable[RunUnitRef]:
        run_root = Path(run_root)
        exp_id = json.loads((run_root / "PACK_MANIFEST.json").read_text(encoding="utf-8"))["experiment_id"]
        generation = load(run_root / "analysis" / f"{exp_id}_generation.json") or {}
        final_slot = {r["question_id"]: r.get("attempt_index") for r in generation.get("records") or []}
        attempts: dict[str, list[Path]] = {}
        for slot in sorted(p for p in (run_root / "runs" / exp_id).iterdir() if p.is_dir() and p.name.isdigit()):
            for unit in sorted(p for p in slot.iterdir() if p.is_dir() and (p / "question.json").is_file()):
                qid = json.loads((unit / "question.json").read_text(encoding="utf-8"))["question_id"]
                attempts.setdefault(qid, []).append(unit)
        for qid in sorted(attempts):
            dirs = attempts[qid]
            chosen = next((d for d in dirs if final_slot.get(qid) is not None and int(d.parent.name) == final_slot[qid]), dirs[-1])
            yield RunUnitRef(f"{exp_id}/{qid}", str(chosen), meta={
                "run_root": str(run_root), "experiment_id": exp_id,
                "other_attempts": [str(d) for d in dirs if d != chosen]})

    def to_trace(self, ref: RunUnitRef) -> dict:
        """ref.meta: run_root, experiment_id, other_attempts (dirs, or {dir, note}); optionally generation (path of
        the generation file relative to run_root), condition, seed, and extras merged into trace extras."""
        unit, root = Path(ref.location), Path(ref.meta["run_root"])
        exp_id = ref.meta["experiment_id"]
        generation = ref.meta.get("generation") or f"analysis/{exp_id}_generation.json"
        manifest = (load(root / generation) or {}).get("manifest") or {}
        agent = manifest.get("agent") or {}
        cli = agent.get("cli")
        question = load(unit / "question.json")
        run_manifest = load(unit / "run_manifest.json") or {}
        tasks = [question["question_id"]]
        controller = run_manifest.get("controller_status")
        trace = empty_trace(
            ref.id, tasks, experiment=exp_id, condition=ref.meta.get("condition", "native"), seed=ref.meta.get("seed"), model=manifest.get("model"),
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
            raise ValueError(f"{exp_id}: unsupported agent cli {cli!r}")

        result = load(unit / "question_result.json")
        if isinstance(result, dict):
            trace["final_answers"][tasks[0]] = answer_entry(result)
            source = "question_result.json"
        elif final_msg is not None:
            trace["final_answers"][tasks[0]] = answer_entry(final_msg)
            source = "final_message"
        else:
            trace["final_answers"][tasks[0]] = {"status": "missing"}
            source = None

        provenance = run_manifest.get("code_provenance") or {}
        runnable = [p for p in provenance.get("runnable_source_files") or [] if (unit / p).is_file()]
        trace["capabilities"] = {"raw_steps": any(s.get("output") for s in trace["steps"] if s["kind"] in ("exec", "tool_call", "file_read")),
                                 "stage_log": False, "replayable_code": bool(runnable)}
        metrics = run_manifest.get("event_metrics") or {}
        analysis = unit / "analysis"
        stderr = (unit / "stderr.log").read_text(encoding="utf-8", errors="replace") if (unit / "stderr.log").is_file() else None
        trace["extras"] = {
            "agent_cli": cli,
            "agent_cli_version": agent.get("cli_version") or agent.get("cli_major_version"),
            "reasoning_effort": manifest.get("reasoning_effort"),
            "attempt_dir": unit.relative_to(root).as_posix(),
            "attempt_index": run_manifest.get("attempt_index"),
            "attempts": [attempt_summary(Path(d), root) if isinstance(d, str) else attempt_summary(Path(d["dir"]), root, d.get("note"))
                         for d in ref.meta.get("other_attempts") or []],
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
            **extras,
            **ref.meta.get("extras", {}),
            **stats,
        }
        return trace

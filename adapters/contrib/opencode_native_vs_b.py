"""Adapter for the native_vs_b BixBench experiment (OpenCode agent runs), as exported to `runs_out/`.

Written by following adapters/ADAPTER_PROMPT.md against dasc/1004_run_analysis/runs_out (read-only).

Layout:

    <run_root>/
      <model>_<variant>_<YYYYMMDDTHHMMSSZ>_<pid>_<condition>_seed<NN>/   one experiment arm x seed
        0000/                                                      attempt slot
          <question_id>/                     native: one run unit = one question (single-question mode)
          <short_id>__<capsule_uuid>/        b: one run unit = one capsule, all its questions (capsule mode)
            run_manifest.json                question_id | question_ids, controller_status, accepted,
                                             return_code, runtime_seconds, failure_reason, event_metrics, ...
            event_timeline.json              OpenCode events: tool parts (shell/read/write/edit/webfetch/
                                             glob/grep) with state.input/output, text parts, step markers, errors
            environment_contract.json        execution environment (not used)

Mapping notes:
    - tasks_covered: run_manifest question_id (native) or question_ids (b). Units without a manifest carry no
      events and no recoverable task ids; they are skipped (listed by `skipped_units`).
    - steps: tool parts in event order; shell -> exec, write/edit -> file_write, read -> file_read,
      others -> tool_call; intermediate text parts -> message. t = elapsed_seconds.
    - final_answers: the last text part parsed as JSON (plain or fenced): native {status, candidates, summary};
      b {short_id, capsule_uuid, workflow_summary, results[{question_id, status, candidates, summary}]}.
      No parseable final JSON -> every task "missing" (the text is kept in extras.final_text_head).
    - status: controller_status passed -> completed; failed / interrupted_* -> failed (raw value in extras).
    - capabilities: raw_steps from tool outputs; no stage log; code is not exported (replayable_code false).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from gce.adapters.base import Adapter, RunUnitRef
from gce.trace import empty_trace

MAX_OUTPUT_CHARS = 200_000
ARM = re.compile(r"^(?P<model>[a-z0-9]+)_(?P<variant>[a-z0-9]+)_(?P<ts>\d{8}T\d{6}Z)_(?P<pid>\d+)_(?P<condition>[a-z]+)_seed(?P<seed>\d+)$")
STEP_KIND = {"shell": "exec", "bash": "exec", "write": "file_write", "edit": "file_write", "read": "file_read"}
STATUS = {"passed": "completed", "failed": "failed"}


def clip(text: str | None, stats: dict) -> str | None:
    if text is None or len(text) <= MAX_OUTPUT_CHARS:
        return text
    stats["truncated_outputs"] = stats.get("truncated_outputs", 0) + 1
    half = MAX_OUTPUT_CHARS // 2
    return f"{text[:half]}\n[... {len(text) - 2 * half} characters truncated by adapter ...]\n{text[-half:]}"


def parse_final(text: str) -> tuple[dict | None, str]:
    """Final answer JSON from the agent's last text: plain JSON, or the first fenced ```json block."""
    stripped = text.strip()
    try:
        doc = json.loads(stripped)
        return (doc, "json") if isinstance(doc, dict) else (None, "none")
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1)), "fenced"
        except json.JSONDecodeError:
            pass
    return None, "none"


def answer_entry(result: dict) -> dict:
    candidates = [c for c in result.get("candidates", []) if isinstance(c, dict) and "answer" in c]
    status = result.get("status") if result.get("status") in ("resolved", "unresolved") else "unresolved"
    entry = {"status": status, "answer": candidates[0]["answer"] if candidates and status == "resolved" else None, "candidates": candidates}
    if result.get("summary") is not None:
        entry["summary"] = str(result["summary"])
    return entry


class OpenCodeNativeVsBAdapter(Adapter):
    name = "opencode_native_vs_b"

    def __init__(self, **options):
        super().__init__(**options)
        self.skipped_units: list[str] = []

    def discover(self, run_root: Path) -> Iterable[RunUnitRef]:
        run_root = Path(run_root)
        self.skipped_units = []
        for arm_dir in sorted(p for p in run_root.iterdir() if p.is_dir() and ARM.match(p.name)):
            arm = ARM.match(arm_dir.name).groupdict()
            for slot in sorted(p for p in arm_dir.iterdir() if p.is_dir()):
                for unit in sorted(p for p in slot.iterdir() if p.is_dir()):
                    if not (unit / "run_manifest.json").is_file():
                        self.skipped_units.append(unit.relative_to(run_root).as_posix())
                        continue
                    unit_id = f"native_vs_b/{arm['model']}_{arm['variant']}_{arm['ts']}/{arm['condition']}/seed{arm['seed']}/{slot.name}/{unit.name}"
                    yield RunUnitRef(unit_id, str(unit), meta=arm)

    def to_trace(self, ref: RunUnitRef) -> dict:
        unit = Path(ref.location)
        arm = ref.meta or ARM.match(unit.parent.parent.name).groupdict()
        manifest = json.loads((unit / "run_manifest.json").read_text(encoding="utf-8"))
        tasks = list(manifest.get("question_ids") or [manifest["question_id"]])
        controller = manifest.get("controller_status")
        trace = empty_trace(
            ref.id, tasks,
            experiment=f"native_vs_b/{arm['model']}_{arm['variant']}_{arm['ts']}_{arm['pid']}",
            condition=arm["condition"], seed=int(arm["seed"]), model=arm["model"],
            group_id=f"bixbench-v1.5/{manifest['short_id']}",
            status=STATUS.get(controller, "failed" if controller else "unknown"),
            duration_seconds=manifest.get("runtime_seconds"),
        )
        stats: dict = {}
        events = json.loads((unit / "event_timeline.json").read_text(encoding="utf-8")) if (unit / "event_timeline.json").is_file() else []
        texts = [(i, e) for i, e in enumerate(events) if (e["event"].get("part") or {}).get("type") == "text"]
        final_index = texts[-1][0] if texts else None
        errors = []
        for i, e in enumerate(events):
            ev = e["event"]
            part = ev.get("part") or {}
            if ev.get("type") == "error":
                errors.append(json.dumps(ev.get("error"), sort_keys=True)[:300])
            if part.get("type") == "tool":
                state = part.get("state") or {}
                inp = state.get("input") or {}
                tool = part.get("tool")
                kind = STEP_KIND.get(tool, "tool_call")
                if tool in ("shell", "bash"):
                    text_in = inp.get("command")
                elif tool == "write":
                    text_in = inp.get("content")
                elif tool == "edit":
                    text_in = json.dumps({k: v for k, v in inp.items() if k != "path"}, ensure_ascii=False)
                else:
                    text_in = json.dumps(inp, ensure_ascii=False, sort_keys=True)
                output = state.get("output")
                trace["steps"].append({
                    "index": len(trace["steps"]), "kind": kind, "tool": tool, "input": text_in,
                    "output": clip(output if isinstance(output, str) else (json.dumps(output) if output is not None else None), stats),
                    "path": inp.get("path") or inp.get("filePath"), "t": e.get("elapsed_seconds"), "status": state.get("status"),
                })
            elif part.get("type") == "text" and i != final_index:
                trace["steps"].append({"index": len(trace["steps"]), "kind": "message", "tool": None, "input": None,
                                       "output": clip(part.get("text"), stats), "t": e.get("elapsed_seconds")})

        final, how = parse_final(texts[-1][1]["event"]["part"]["text"]) if texts else (None, "none")
        if final is not None and "results" in final:
            for result in final.get("results") or []:
                if isinstance(result, dict) and result.get("question_id") in tasks:
                    trace["final_answers"][result["question_id"]] = answer_entry(result)
        elif final is not None and len(tasks) == 1:
            trace["final_answers"][tasks[0]] = answer_entry(final)
        for task in tasks:
            trace["final_answers"].setdefault(task, {"status": "missing"})

        trace["capabilities"] = {"raw_steps": any(s.get("output") for s in trace["steps"] if s["kind"] != "message"),
                                 "stage_log": False, "replayable_code": False}
        metrics = manifest.get("event_metrics") or {}
        trace["extras"] = {
            "controller_status": controller,
            "accepted": manifest.get("accepted"),
            "return_code": manifest.get("return_code"),
            "failure_reason": manifest.get("failure_reason"),
            "policy_violation": manifest.get("policy_violation"),
            "question_local_failures": manifest.get("question_local_failures"),
            "command_class_counts": {k: v for k, v in metrics.items() if k.endswith("_count")},
            "final_text_parse": how,
            "final_text_head": None if final is not None or not texts else texts[-1][1]["event"]["part"]["text"][:500],
            "provider_errors": errors,
            "workflow_summary": final.get("workflow_summary") if final else None,
            **stats,
        }
        return trace

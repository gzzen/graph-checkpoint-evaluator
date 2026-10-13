"""Adapter for one native run of the native_b controller: one agent session per question.

Pass the run directory, e.g.
    opencode_deepseek_20261012T065851Z_2938483/runs/opencode_deepseek_20261012T065851Z_2938483_native_rep01
    codex_native_diagnostic_20261012T062056Z_1835731/runs/codex_native_diagnostic_20261012T062056Z_1835731

Written by following adapters/ADAPTER_PROMPT.md against the dasc/1012_test_run packs (read-only). The run layout,
resume stages and the trace mapping shared with condition b are described in _native_b_common.py.

Unit (one question attempt), <run>/[resume_<stamp>/]<slot>/<question_id>/:
    question.json              question_id, short_id, tag, version -> tasks_covered, group_id <tag>-v<version>/<short_id>
    question_result.json       {status, candidates, summary} -> final_answers (absent when the attempt failed)
    run_manifest.json          controller verdict, event_metrics, code_provenance (runnable_source_files), ...
    event_timeline.json        Codex or OpenCode events -> steps
    analysis/                  compliance_audit.json, scripts/, outputs/

Mapping notes:
    - generation entries: `records`, one per question, keyed by question_id.
    - run_unit_id: <experiment_id>[/<tag>]/<question_id>; condition `native` when the run name has no tag.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("gce_adapter_native_b_common", Path(__file__).with_name("_native_b_common.py"))
_common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_common)


class NativeAdapter(_common.ControllerRunAdapter):
    name = "native"
    condition = "native"
    unit_file = "question.json"
    result_file = "question_result.json"
    entries_key = "records"
    entry_key = "question_id"
    other_adapter = "adapters/contrib/b.py"

    def unit_key(self, unit: Path) -> str:
        return _common.load(unit / "question.json")["question_id"]

    def questions(self, unit: Path) -> list[dict]:
        return [_common.load(unit / "question.json")]

    def answers(self, doc: dict, tasks: list[str]) -> tuple[dict, list]:
        return {tasks[0]: _common.answer_entry(doc)}, []

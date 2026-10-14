"""Adapter for one condition-b run of the native_b controller: one agent session per capsule (BixBench short_id),
answering all of the capsule's questions.

Pass the run directory, e.g.
    opencode_deepseek_20261012T065851Z_2938483_2/runs/opencode_deepseek_20261012T065851Z_2938483_b_seed01

Written by following adapters/ADAPTER_PROMPT.md against that run (read-only). The run layout, resume stages and
the trace mapping shared with the native condition are described in _native_b_common.py.

Unit (one capsule attempt), <run>/[resume_<stamp>/]<slot>/<short_id>__<capsule_uuid>/:
    questions.json             all questions of the capsule -> tasks_covered, group_id <tag>-v<version>/<short_id>
    capsule_result.json        {capsule_uuid, short_id, workflow_summary, results[{question_id, status, candidates,
                               summary}]} -> final_answers, one per question (absent when the attempt failed)
    run_manifest.json          controller verdict plus the condition-b component checks (a_*, b_*, grounding,
                               consistency, operation contract, per-question failures); code_provenance lists
                               source_files only
    event_timeline.json        OpenCode events -> steps (not tagged with question ids, so steps[].tasks is empty)
    analysis/grounding.json    per-question semantic grounding (data object, observation unit, comparison, metric,
                               method, selection rationale) -> extras.grounding
    analysis/consistency.json, analysis/operation_contract.json, operation_hints.json   controller checks (the
                               manifest carries the same verdicts; extras.condition_checks)
    analysis/scripts/          persisted agent code

Mapping notes:
    - generation entries: `episodes`, one per capsule, keyed by short_id.
    - run_unit_id: <experiment_id>[/<tag>]/<short_id>; condition `b` when the run name has no tag.
    - final_answers: one entry per results[] item naming a covered question; covered questions without a result are
      missing; results naming other ids go to extras.results_for_unknown_questions.
    - grounding.json is a per-question interpretation record with no step quantities, so it is kept in extras and
      not mapped onto stage_log.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("gce_adapter_native_b_common", Path(__file__).with_name("_native_b_common.py"))
_common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_common)

# controller results of the condition-b components (base condition a + semantic grounding)
CONDITION_CHECKS = ("base_condition", "a_compliance_passed", "a_question_local_failures", "b_component_status",
                    "b_grounding_status", "b_question_local_failures", "b_grounding_failures", "b_consistency_failures",
                    "consistency_component", "grounding_component", "operation_contract", "identity_transport_repair",
                    "question_completion")


class BAdapter(_common.ControllerRunAdapter):
    name = "b"
    condition = "b"
    unit_file = "questions.json"
    result_file = "capsule_result.json"
    entries_key = "episodes"
    entry_key = "short_id"
    other_adapter = "adapters/contrib/native.py"

    def unit_key(self, unit: Path) -> str:
        questions = _common.load(unit / "questions.json")
        return questions[0]["short_id"] if questions else unit.name.split("__")[0]

    def questions(self, unit: Path) -> list[dict]:
        return _common.load(unit / "questions.json")

    def answers(self, doc: dict, tasks: list[str]) -> tuple[dict, list]:
        found, foreign = {}, []
        for item in doc.get("results") or []:
            if isinstance(item, dict) and item.get("question_id") in tasks:
                found[item["question_id"]] = _common.answer_entry(item)
            else:
                foreign.append(item)
        return found, foreign

    def unit_extras(self, unit: Path, run_manifest: dict, doc: dict | None) -> dict:
        _, foreign = self.answers(doc, [q["question_id"] for q in self.questions(unit)]) if doc else ({}, [])
        return {
            "workflow_summary": doc.get("workflow_summary") if doc else None,
            "results_for_unknown_questions": foreign,
            "grounding": _common.load(unit / "analysis" / "grounding.json"),
            "condition_checks": {k: run_manifest[k] for k in CONDITION_CHECKS if k in run_manifest} or None,
        }

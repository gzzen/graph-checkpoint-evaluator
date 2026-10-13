"""Adapter for native executable-code packs whose runs carry a run tag and may have been resumed, such as
opencode_deepseek_20261012T065851Z_2938483 (tag native_rep01, one resume stage).

Written by following adapters/ADAPTER_PROMPT.md against dasc/1012_test_run/opencode_deepseek_* (read-only). The
per-attempt files are those of the native diagnostic packs, so the trace mapping (steps, answers, extras) is
inherited from native_diagnostic.py; this adapter only adds run tags and resume stages to discovery.

Layout:

    <run_root>/
      PACK_MANIFEST.json                                experiment_id
      analysis/<experiment_id>_<tag>_generation.json    manifest (model, agent) and one record per question; the
                                                        record's `workspace` is the final attempt's directory
      analysis/<experiment_id>_<tag>_manifest_resume_<stamp>.json
                                                        resume plan: reused_terminal_units, interrupted_workspaces
                                                        (attempts cut off when the original run halted)
      analysis/<experiment_id>_<other tag>_manifest.json  a condition prepared but not packed (e.g. b_seed01);
                                                        without a runs directory it yields no units
      runs/<experiment_id>_<tag>/                       one run tag, e.g. native_rep01 = condition native, rep 1
        <slot NNNN>/<question_id>/                      original run: 0000 first attempt, 0001 retry
        resume_<stamp>/<slot NNNN>/<question_id>/       continuation after the original run halted: questions
                                                        that had no terminal result, run again from attempt 0 or
                                                        from the pending policy retry
      results/, logs/, reports/                         exported predictions, controller logs, summary (not read)

    Each attempt directory has the native diagnostic files (question.json, run_manifest.json,
    event_timeline.json, question_result.json, analysis/...). Attempts interrupted by the halt have only
    question.json and analysis/ (no manifest, no events).

    A run unit is one question of one run tag: the attempt named by the generation record's workspace (the last
    attempt in stage order when no record matches). Every other attempt of the question, from either stage, is
    summarized in extras.attempts; those listed in a resume plan's interrupted_workspaces are noted
    "interrupted_by_halt".

Mapping notes (beyond native_diagnostic.py):
    - run_unit_id: <experiment_id>/<tag>/<question_id>.
    - condition and seed: from the tag `<condition>_(rep|seed)<NN>`; a tag of another form becomes the condition
      with seed null. The raw tag is kept in extras.run_tag.
    - extras.stage: "initial" or the resume directory of the final attempt; extras.reused_by_resume: the question
      was terminal before the halt and was carried over by the resume plan.
    - Runs directories without a generation file are skipped (listed in `skipped_runs`).
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Iterable

from gce.adapters.base import RunUnitRef

_spec = importlib.util.spec_from_file_location("gce_adapter_native_diagnostic_base", Path(__file__).with_name("native_diagnostic.py"))
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

TAG = re.compile(r"^(?P<condition>[a-z][a-z0-9]*)_(?:rep|seed)(?P<n>\d+)$")


def attempt_dirs(stage_dir: Path) -> list[Path]:
    return [unit for slot in sorted(p for p in stage_dir.iterdir() if p.is_dir() and p.name.isdigit())
            for unit in sorted(p for p in slot.iterdir() if p.is_dir() and (p / "question.json").is_file())]


class NativeTaggedAdapter(_base.NativeDiagnosticAdapter):
    name = "native_tagged"

    def __init__(self, **options):
        super().__init__(**options)
        self.skipped_runs: list[str] = []

    def discover(self, run_root: Path) -> Iterable[RunUnitRef]:
        run_root = Path(run_root)
        self.skipped_runs = []
        exp_id = json.loads((run_root / "PACK_MANIFEST.json").read_text(encoding="utf-8"))["experiment_id"]
        for runs_dir in sorted(p for p in (run_root / "runs").iterdir() if p.is_dir() and p.name.startswith(exp_id + "_")):
            tag = runs_dir.name[len(exp_id) + 1:]
            generation_rel = f"analysis/{exp_id}_{tag}_generation.json"
            generation = _base.load(run_root / generation_rel)
            if generation is None:
                self.skipped_runs.append(runs_dir.relative_to(run_root).as_posix())
                continue
            m = TAG.match(tag)
            condition, seed = (m["condition"], int(m["n"])) if m else (tag, None)
            final_workspace = {r["question_id"]: r.get("workspace") for r in generation.get("records") or []}

            interrupted: set[str] = set()
            reused: set[str] = set()
            for plan_path in sorted((run_root / "analysis").glob(f"{exp_id}_{tag}_manifest_resume_*.json")):
                plan = _base.load(plan_path) or {}
                interrupted |= set(plan.get("interrupted_workspaces") or [])
                reused |= set(plan.get("reused_terminal_units") or [])

            stages = [("initial", runs_dir)] + [(p.name, p) for p in sorted(runs_dir.iterdir()) if p.is_dir() and p.name.startswith("resume_")]
            attempts: dict[str, list[tuple[str, Path]]] = {}
            for stage, stage_dir in stages:
                for unit in attempt_dirs(stage_dir):
                    qid = json.loads((unit / "question.json").read_text(encoding="utf-8"))["question_id"]
                    attempts.setdefault(qid, []).append((stage, unit))

            def suffix(unit: Path) -> str:
                return "/" + unit.relative_to(runs_dir.parent).as_posix()

            for qid in sorted(attempts):
                found = attempts[qid]
                workspace = final_workspace.get(qid) or ""
                stage, chosen = next(((s, d) for s, d in found if workspace.endswith(suffix(d))), found[-1])
                others = [{"dir": str(d), "note": "interrupted_by_halt" if any(w.endswith(suffix(d)) for w in interrupted) else None}
                          for _, d in found if d != chosen]
                yield RunUnitRef(f"{exp_id}/{tag}/{qid}", str(chosen), meta={
                    "run_root": str(run_root), "experiment_id": exp_id, "generation": generation_rel,
                    "condition": condition, "seed": seed, "other_attempts": others,
                    "extras": {"run_tag": tag, "stage": stage, "reused_by_resume": qid in reused}})

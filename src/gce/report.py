"""Aggregate evaluation documents into CSV tables and a markdown summary."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from .io import load_doc
from .paths import document_name, group_documents

TASK_FIELDS = [
    "experiment", "condition", "seed", "run_unit_id", "group_id", "unit_tasks", "task", "answer_status",
    "answer_correct", "answer_correct_vs_ideal", "depth", "path_length", "depth_fraction",
    "first_failure", "first_failure_stage", "first_gap", "first_gap_stage", "hidden_misses",
]
NODE_FIELDS = ["experiment", "condition", "seed", "run_unit_id", "group_id", "node", "state", "confidence", "self_reported", "observed", "bleed_from"]


def load_evaluations(paths: list[str | Path]) -> list[dict]:
    files: list[Path] = []
    for p in map(Path, paths):
        files += sorted(p.rglob("*.eval.json")) if p.is_dir() else [p]
    return [load_doc(f) for f in files]


def _run_fields(doc: dict) -> dict:
    run = doc["run"]
    return {"experiment": run.get("experiment"), "condition": run.get("condition"), "seed": run.get("seed"),
            "run_unit_id": run["run_unit_id"], "group_id": doc["group_id"]}


def task_rows(evals: list[dict]) -> list[dict]:
    rows = []
    for doc in evals:
        for task, t in doc["tasks"].items():
            rows.append({**_run_fields(doc), "unit_tasks": len(doc["tasks"]), "task": task,
                         **{k: t.get(k) for k in TASK_FIELDS if k in t}, "hidden_misses": ";".join(t.get("hidden_misses", []))})
    return rows


def node_rows(evals: list[dict]) -> list[dict]:
    rows = []
    for doc in evals:
        for node, n in doc["nodes"].items():
            observed = n.get("observed")
            rows.append({**_run_fields(doc), "node": node, "state": n["state"], "confidence": n.get("confidence"),
                         "self_reported": n.get("self_reported"), "bleed_from": n.get("bleed_from"),
                         "observed": json.dumps(observed) if isinstance(observed, (list, dict)) else observed})
    return rows


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _fmt(x: Any) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.2f}"
    return str(x)


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(_fmt(c) for c in r) + " |" for r in rows]
    return out


def shared_node_variance(evals: list[dict]) -> list[dict]:
    """Per (condition, group, node): state counts and distinct observed values across run units."""
    acc: dict[tuple, dict] = defaultdict(lambda: {"states": Counter(), "observed": set(), "units": 0})
    for doc in evals:
        cond = (doc["run"].get("experiment"), doc["run"].get("condition"))
        for node, n in doc["nodes"].items():
            slot = acc[(cond, doc["group_id"], node)]
            slot["states"][n["state"]] += 1
            slot["units"] += 1
            if n.get("observed") is not None:
                slot["observed"].add(json.dumps(n["observed"], sort_keys=True, default=str))
    return [{"condition": "/".join(str(c) for c in cond if c), "group_id": g, "node": node, "units": v["units"],
             "states": dict(v["states"]), "distinct_observed": len(v["observed"])}
            for (cond, g, node), v in sorted(acc.items(), key=lambda kv: (str(kv[0][0]), kv[0][1], kv[0][2]))]


def summary_markdown(evals: list[dict], graphs: list | None = None) -> str:
    rows = task_rows(evals)
    lines = ["# Checkpoint evaluation summary", "", f"{len(evals)} run-unit evaluations, {len(rows)} task results.", ""]

    by_cond: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cond["/".join(str(x) for x in (r["experiment"], r["condition"]) if x) or "(unlabelled)"].append(r)
    lines += ["## By condition", ""]
    table = []
    for cond, rs in sorted(by_cond.items()):
        answered = [r for r in rs if r["answer_correct"] is not None]
        table.append([cond, len(rs), float(mean(r["answer_correct"] is True for r in answered)) if answered else None,
                      mean(r["depth_fraction"] for r in rs), sum(1 for r in rs if r["hidden_misses"])])
    lines += _table(["condition", "tasks", "answer accuracy", "mean depth fraction", "tasks with hidden misses"], table) + [""]

    lines += ["## Where runs first went wrong (first_failure stage, else first_gap stage)", ""]
    stages = sorted({r["first_failure_stage"] or r["first_gap_stage"] or "none" for r in rows})
    table = []
    for cond, rs in sorted(by_cond.items()):
        counts = Counter(r["first_failure_stage"] or (f"gap:{r['first_gap_stage']}" if r["first_gap_stage"] else "none") for r in rs)
        table.append([cond, *[counts.get(s, 0) + counts.get(f"gap:{s}", 0) for s in stages]])
    lines += _table(["condition", *stages], table) + [""]

    lines += ["## Per task", ""]
    by_task: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_task[(r["task"], "/".join(str(x) for x in (r["experiment"], r["condition"]) if x))].append(r)
    table = []
    for (task, cond), rs in sorted(by_task.items()):
        ff = Counter(r["first_failure"] for r in rs if r["first_failure"])
        table.append([task, cond, len(rs), float(mean(r["answer_correct"] is True for r in rs)), mean(r["depth_fraction"] for r in rs),
                      ", ".join(f"{k} ({v})" for k, v in ff.most_common(3)) or "None"])
    lines += _table(["task", "condition", "runs", "accuracy", "mean depth fraction", "first failures"], table) + [""]

    multi = [d for d in evals if d.get("group_metrics")]
    if multi:
        lines += ["## Multi-task run units", ""]
        table = []
        for d in multi:
            gm = d["group_metrics"]
            table.append([d["run"]["run_unit_id"], d["group_id"], gm["failed_tasks"], len(gm["root_causes"]),
                          gm["consistency"]["consistent_fraction"], len(gm["parameter_bleed"]), len(gm["reuse"]["recomputed"])])
        lines += _table(["run unit", "group", "failed tasks", "root causes", "consistency", "parameter bleed", "recomputed shared nodes"], table) + [""]

    variance = [v for v in shared_node_variance(evals) if v["units"] > 1 and (len(v["states"]) > 1 or v["distinct_observed"] > 1)]
    if variance:
        lines += ["## Checkpoints that vary across run units of the same condition", ""]
        lines += _table(["condition", "group", "node", "units", "states", "distinct observed"],
                        [[v["condition"], v["group_id"], v["node"], v["units"], json.dumps(v["states"]), v["distinct_observed"]] for v in variance]) + [""]
    names = sorted({document_name(d["group_id"]) for d in evals})
    lines += ["## Checkpoint paths per question", "",
              "Diagrams and per-run tables for every question are in one file per task group:", ""]
    lines += [f"- [{name[:-3]}](paths/{name})" for name in names] + [""]
    return "\n".join(lines)


def write_report(evals: list[dict], out: str | Path, graphs: list | None = None) -> Path:
    out = Path(out)
    _write_csv(out / "tasks.csv", task_rows(evals), TASK_FIELDS)
    _write_csv(out / "nodes.csv", node_rows(evals), NODE_FIELDS)
    (out / "summary.md").write_text(summary_markdown(evals, graphs), encoding="utf-8")
    for group_id, text in group_documents(evals, graphs).items():
        path = out / "paths" / document_name(group_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    return out

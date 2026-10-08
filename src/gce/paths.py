"""Per-question path views for reports, written as one Markdown document per task group (e.g. per
BixBench capsule): how far each run progressed along each task's checkpoint path, and what it produced
at every checkpoint.

For every task the document contains
* a Mermaid flowchart of the task's path (edges from the graph), each checkpoint coloured by the
  outcomes the runs reached there and labelled with their counts;
* a table with one row per checkpoint (stage, whether it is a hidden decision or gating, expected
  value) and one column per run, each cell giving the outcome and the value the run produced, followed
  by the checkpoints passed, where the run stopped and its final answer.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from .graph import Graph

STATE_TEXT = {
    "pass": "Pass",
    "implied": "Implied",
    "fail": "Fail",
    "absent": "Not observed",
    "unsupported": "Unsupported",
    "n/a": "Not applicable",
}
DEFINITIONS = [
    ("Pass", "The expected value (or method) was found in the run's output."),
    ("Implied", "Not observed directly, but a later checkpoint on the path passed."),
    ("Fail", "The run produced this quantity with a value different from the reference."),
    ("Not observed", "Nothing in the run's output corresponds to this checkpoint."),
    ("Unsupported", "The run reported a value that no tool output corroborates."),
    ("Not applicable", "An accepted alternative method passed instead."),
    ("Low confidence", "The evidence relies on a heuristic match; verify manually."),
    ("First failure / first gap", "The first gating checkpoint at which the run failed / was not observed."),
    ("Hidden decision", "A choice made by the reference analysis that the question does not state."),
    ("Gating", "Whether failing this checkpoint blocks a correct answer."),
]
STATE_ORDER = ["pass", "implied", "n/a", "fail", "unsupported", "absent"]


def fmt_value(value: Any, width: int = 18) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        v = float(value)
        if v == int(v) and abs(v) < 1e9:
            return str(int(v))
        return f"{v:.4g}"
    if isinstance(value, (list, dict)):
        return "(structured value)"
    text = " ".join(str(value).split()).replace("|", "/")
    return text if len(text) <= width else text[: width - 3] + "..."


def describe_spec(spec: dict | None) -> str:
    if not spec:
        return ""
    kind = spec["type"]
    if kind == "range":
        lo, hi = sorted((spec["min"], spec["max"]))
        return f"{fmt_value(lo)} to {fmt_value(hi)}"
    if kind == "percent":
        return f"{fmt_value(spec['value'])}%"
    if kind in ("number", "integer", "label", "string", "boolean"):
        return fmt_value(spec.get("value", spec.get("regex")), 24)
    if kind == "set":
        return f"set of {spec.get('size', len(spec.get('items', [])))} items"
    if kind == "any_of":
        return " or ".join(describe_spec(o) for o in spec["options"])
    return kind


def _expected(graph: Graph | None, node_id: str) -> str:
    if graph is None or node_id not in graph.nodes:
        return ""
    node = graph.nodes[node_id]
    if not node.quantities:
        return "Method check"
    parts = [f"{q.name} = {describe_spec(q.expected)}" for q in node.quantities[:2]]
    joiner = "; " if node.require == "all" else "; or "
    return joiner.join(parts) + ("; ..." if len(node.quantities) > 2 else "")


def _run_label(doc: dict) -> str:
    """Column label within one condition's document: the seed (or unit id) and whether it is a capsule run."""
    run = doc["run"]
    parts = []
    if run.get("seed") is not None:
        parts.append(f"seed {run['seed']}")
    label = ", ".join(parts) or run["run_unit_id"].split("/")[-1]
    return label + (" (capsule run)" if len(run.get("tasks_covered", [])) > 1 else "")


def _cell(node: dict | None, stop: str | None) -> str:
    if node is None:
        return ""
    text = STATE_TEXT.get(node["state"], node["state"])
    qualifiers = []
    if node.get("confidence") == "low" and node["state"] in ("pass", "fail"):
        qualifiers.append("low confidence")
    if node["state"] == "pass" and "not reported" in node.get("note", ""):
        qualifiers.append("computed, not reported")
    if qualifiers:
        text += f" ({', '.join(qualifiers)})"
    value = fmt_value(node.get("observed"))
    if value:
        text += f": {value}"
    return f"**{text}** ({stop})" if stop else text


def _node_class(states: Counter) -> str:
    passing = states["pass"] + states["implied"] + states["n/a"]
    failing = states["fail"] + states["unsupported"]
    total = sum(states.values())
    if failing and failing == total:
        return "fail"
    if failing:
        return "mixed"
    if passing == total:
        return "pass"
    if passing:
        return "partial"
    return "absent"


def _mermaid_id(node_id: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", node_id)


def mermaid(path: list[str], runs: list[dict], graph: Graph | None) -> list[str]:
    lines = ["```mermaid", "flowchart LR"]
    for nid in path:
        states = Counter(r["nodes"][nid]["state"] for r in runs if nid in r["nodes"])
        counts = ", ".join(f"{STATE_TEXT[s]} {states[s]}" for s in STATE_ORDER if states[s])
        node = graph.nodes.get(nid) if graph else None
        notes = [node.stage_type] if node else []
        if node and node.hidden:
            notes.append("hidden decision")
        if node and not node.gating:
            notes.append("non-gating")
        label = f"{nid}<br/><small>{', '.join(notes)}</small><br/>{counts}"
        lines.append(f'  {_mermaid_id(nid)}["{label}"]:::{_node_class(states)}')
    members = set(path)
    if graph:
        for nid in path:
            for dep in graph.nodes[nid].depends_on if nid in graph.nodes else []:
                if dep in members:
                    lines.append(f"  {_mermaid_id(dep)} --> {_mermaid_id(nid)}")
    else:  # no graph: draw the path as a chain
        lines += [f"  {_mermaid_id(a)} --> {_mermaid_id(b)}" for a, b in zip(path, path[1:])]
    lines += [
        "  classDef pass fill:#d8f0dc,stroke:#2e7d32,color:#111;",
        "  classDef partial fill:#eef6d6,stroke:#7cb342,color:#111;",
        "  classDef mixed fill:#fff1c9,stroke:#f9a825,color:#111;",
        "  classDef fail fill:#f9d6d5,stroke:#c62828,color:#111;",
        "  classDef absent fill:#eeeeee,stroke:#9e9e9e,color:#111;",
        "```",
    ]
    return lines


def task_table(task: str, path: list[str], runs: list[dict], graph: Graph | None) -> list[str]:
    headers = ["Checkpoint", "Stage", "Hidden decision", "Gating", "Expected", *[_run_label(r) for r in runs]]
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    stops = []
    for r in runs:
        t = r["tasks"][task]
        stops.append((t["first_failure"], "first failure") if t.get("first_failure") else
                     (t["first_gap"], "first gap") if t.get("first_gap") else (None, None))
    for nid in path:
        node = graph.nodes.get(nid) if graph else None
        meta = [node.stage_type, "Yes" if node.hidden else "No", "Yes" if node.gating else "No"] if node else ["", "", ""]
        cells = [_cell(r["nodes"].get(nid), label if stop == nid else None) for r, (stop, label) in zip(runs, stops)]
        lines.append(f"| {nid} | " + " | ".join(meta) + f" | {_expected(graph, nid)} | " + " | ".join(cells) + " |")
    blank = " | " * 4
    passed = [f"{r['tasks'][task]['depth']} of {r['tasks'][task]['path_length']}" for r in runs]
    stopped = [f"{stop} ({label})" if stop else "Did not stop" for stop, label in stops]
    answers = []
    for r in runs:
        t = r["tasks"][task]
        if t.get("answer_status") != "resolved":
            answers.append("Not reported")
        else:
            answers.append(("Correct: " if t.get("answer_correct") else "Incorrect: ") + fmt_value(t.get("answer_observed"), 24))
    ideal = describe_spec(graph.tasks[task].ideal) if graph and task in graph.tasks else ""
    lines.append(f"| **Gating checkpoints passed**{blank} | " + " | ".join(passed) + " |")
    lines.append(f"| **Stopped at**{blank} | " + " | ".join(stopped) + " |")
    lines.append(f"| **Final answer** | | | | Official answer: {ideal} | " + " | ".join(answers) + " |")
    return lines


def _task_order(task: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", task)), task


def condition_key(doc: dict) -> tuple[str, str]:
    run = doc["run"]
    return str(run.get("experiment") or ""), str(run.get("condition") or "unlabelled")


def condition_dirs(evals: list[dict]) -> dict[tuple[str, str], str]:
    """Directory name per (experiment, condition): the condition alone, unless two experiments share it."""
    keys = sorted({condition_key(d) for d in evals})
    clash = Counter(cond for _, cond in keys)
    safe = lambda text: re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "unlabelled"
    return {(exp, cond): safe(cond if clash[cond] == 1 or not exp else f"{exp}__{cond}") for exp, cond in keys}


def group_documents(evals: list[dict], graphs: list[Graph] | None = None) -> dict[tuple[str, str], str]:
    """(condition directory, group_id) -> Markdown document with the path views of every task of that
    group, computed from that condition's runs only, so running conditions are never pooled."""
    graph_by_group = {g.group_id: g for g in graphs or []}
    dirs = condition_dirs(evals)
    by_key: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for doc in evals:
        for task in doc["tasks"]:
            by_key[(dirs[condition_key(doc)], doc["group_id"])][task].append(doc)
    documents = {}
    for (cond_dir, group_id), tasks in sorted(by_key.items()):
        graph = graph_by_group.get(group_id)
        sample = next(iter(tasks.values()))[0]["run"]
        title = graph.raw.get("title") if graph else None
        lines = [f"# Checkpoint paths: {group_id}, condition {sample.get('condition') or 'unlabelled'}", ""]
        if title:
            lines += [f"{title}.", ""]
        lines += [f"Experiment: {sample.get('experiment') or 'unlabelled'}. Condition: {sample.get('condition') or 'unlabelled'}. "
                  "Only runs of this condition are included.", ""]
        lines += ["## Definitions", "", "| Term | Meaning |", "|---|---|"]
        lines += [f"| {term} | {meaning} |" for term, meaning in DEFINITIONS] + [""]
        for task in sorted(tasks, key=_task_order):
            runs = sorted(tasks[task], key=lambda d: (str(d["run"].get("seed")), d["run"]["run_unit_id"]))
            path = runs[0]["tasks"][task]["path"]
            lines += [f"## {task}", ""]
            if graph and task in graph.tasks and graph.tasks[task].question:
                lines += [f"Question: {graph.tasks[task].question}", ""]
            lines += ["### Checkpoint path", ""] + mermaid(path, runs, graph) + [""]
            lines += ["### Outcome per run", ""] + task_table(task, path, runs, graph) + [""]
        documents[(cond_dir, group_id)] = "\n".join(lines)
    return documents


def document_name(group_id: str) -> str:
    """File name for a group's document: its last path segment, e.g. bixbench-v1.5/bix-10 -> bix-10.md."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", group_id.rstrip("/").split("/")[-1]) + ".md"

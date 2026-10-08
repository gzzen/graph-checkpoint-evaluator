"""Per-question path views for reports: how far each run got along a task's checkpoint path, and
what it produced at every checkpoint.

For every task two views are rendered:
* a Mermaid flowchart of the task's path (DAG edges from the graph), each node coloured by the states
  the runs reached there and labelled with their counts;
* a text grid: one row per checkpoint (stage, expected value), one column per run, each cell the state
  symbol plus the value the run produced; the last rows give depth reached, where it stopped and the
  final answer.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from .graph import Graph

SYMBOL = {"pass": "✓", "implied": "↑", "fail": "✗", "absent": "·", "unsupported": "⊘", "n/a": "–"}
LEGEND = ("✓ pass · ↑ implied by a later pass · ✗ fail (wrong value) · `·` absent · ⊘ unsupported claim · "
          "– n/a (alternative route) · ? low confidence · ▶ first failure/gap · `*` hidden decision · `°` non-gating")


def fmt_value(value: Any, width: int = 16) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        v = float(value)
        if v == int(v) and abs(v) < 1e9:
            return str(int(v))
        return f"{v:.4g}"
    if isinstance(value, (list, dict)):
        return "…"
    text = " ".join(str(value).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def describe_spec(spec: dict | None) -> str:
    if not spec:
        return ""
    kind = spec["type"]
    if kind == "range":
        lo, hi = sorted((spec["min"], spec["max"]))
        return f"{fmt_value(lo)}–{fmt_value(hi)}"
    if kind == "percent":
        return f"{fmt_value(spec['value'])}%"
    if kind in ("number", "integer", "label", "string", "boolean"):
        return fmt_value(spec.get("value", spec.get("regex")), 22)
    if kind == "set":
        return f"set({spec.get('size', len(spec.get('items', [])))})"
    if kind == "any_of":
        return " or ".join(describe_spec(o) for o in spec["options"])
    return kind


def _node_expected(graph: Graph | None, node_id: str) -> str:
    if graph is None or node_id not in graph.nodes:
        return ""
    node = graph.nodes[node_id]
    if not node.quantities:
        return "(method)"
    parts = [describe_spec(q.expected) for q in node.quantities[:2]]
    more = " …" if len(node.quantities) > 2 else ""
    return (" / " if node.require == "all" else " or ").join(parts) + more


def _run_label(doc: dict) -> str:
    run = doc["run"]
    label = "/".join(str(x) for x in (run.get("condition"), f"s{run['seed']}" if run.get("seed") is not None else None) if x)
    label = label or run["run_unit_id"].split("/")[-1]
    return label + (" ⧉" if len(run.get("tasks_covered", [])) > 1 else "")


def _cell(node: dict | None, is_stop: bool) -> str:
    if node is None:
        return ""
    sym = SYMBOL.get(node["state"], "?")
    if node.get("confidence") == "low" and node["state"] in ("pass", "fail"):
        sym += "?"
    value = fmt_value(node.get("observed"))
    text = f"{sym} {value}".strip()
    return f"▶ **{text}**" if is_stop else text


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


def mermaid(task: str, path: list[str], runs: list[dict], graph: Graph | None) -> list[str]:
    lines = ["```mermaid", "flowchart LR"]
    for nid in path:
        states = Counter(r["nodes"][nid]["state"] for r in runs if nid in r["nodes"])
        tally = " ".join(f"{SYMBOL[s]}{n}" for s, n in states.most_common())
        stage = graph.nodes[nid].stage_type if graph and nid in graph.nodes else ""
        flags = ("*" if graph and nid in graph.nodes and graph.nodes[nid].hidden else "") + \
                ("°" if graph and nid in graph.nodes and not graph.nodes[nid].gating else "")
        label = f"{nid}{flags}<br/><small>{stage}</small><br/>{tally}"
        lines.append(f'  {_mermaid_id(nid)}["{label}"]:::{_node_class(states)}')
    members = set(path)
    for nid in path:
        deps = graph.nodes[nid].depends_on if graph and nid in graph.nodes else []
        for dep in deps:
            if dep in members:
                lines.append(f"  {_mermaid_id(dep)} --> {_mermaid_id(nid)}")
    if not graph:  # no graph: draw the path as a chain
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


def task_grid(task: str, path: list[str], runs: list[dict], graph: Graph | None) -> list[str]:
    headers = ["checkpoint", "stage", "expected", *[_run_label(r) for r in runs]]
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    stops = [r["tasks"][task].get("first_failure") or r["tasks"][task].get("first_gap") for r in runs]
    for nid in path:
        node = graph.nodes.get(nid) if graph else None
        flags = ("\\*" if node and node.hidden else "") + ("°" if node and not node.gating else "")
        cells = [_cell(r["nodes"].get(nid), stop == nid) for r, stop in zip(runs, stops)]
        lines.append(f"| `{nid}`{flags} | {node.stage_type if node else ''} | {_node_expected(graph, nid)} | " + " | ".join(cells) + " |")
    reached = [f"{r['tasks'][task]['depth']}/{r['tasks'][task]['path_length']}" for r in runs]
    stopped = [stop or "–" for stop in stops]
    answers = []
    for r in runs:
        t = r["tasks"][task]
        if t.get("answer_status") != "resolved":
            answers.append(f"({t.get('answer_status', 'missing')})")
        else:
            answers.append(("✓ " if t.get("answer_correct") else "✗ ") + fmt_value(t.get("answer_observed"), 22))
    ideal = describe_spec(graph.tasks[task].ideal) if graph and task in graph.tasks else ""
    lines.append("| **reached** | | | " + " | ".join(reached) + " |")
    lines.append("| **stopped at** | | | " + " | ".join(stopped) + " |")
    lines.append(f"| **final answer** | | ideal {ideal} | " + " | ".join(answers) + " |")
    return lines


def paths_markdown(evals: list[dict], graphs: list[Graph] | None = None) -> list[str]:
    graph_by_group = {g.group_id: g for g in graphs or []}
    by_task: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for doc in evals:
        for task in doc["tasks"]:
            by_task[(doc["group_id"], task)].append(doc)
    lines = ["## Paths per question", "", LEGEND, ""]

    def order(key: tuple[str, str]) -> tuple:
        nums = [int(n) for n in re.findall(r"\d+", key[1])]
        return (key[0], nums, key[1])

    for group_id, task in sorted(by_task, key=order):
        runs = sorted(by_task[(group_id, task)], key=lambda d: (str(d["run"].get("condition")), str(d["run"].get("seed")), d["run"]["run_unit_id"]))
        graph = graph_by_group.get(group_id)
        path = runs[0]["tasks"][task]["path"]
        question = graph.tasks[task].question if graph and task in graph.tasks else ""
        lines += [f"### {task}", ""]
        if question:
            lines += [f"> {question}", ""]
        lines += mermaid(task, path, runs, graph) + [""]
        lines += task_grid(task, path, runs, graph) + [""]
    return lines

"""Checkpoint graph model: nodes, tasks, task paths and shared nodes."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .io import load_doc

STAGE_TYPES = ("inputs", "cohort", "filter", "transform", "model", "result_table", "downstream", "answer")


@dataclass
class Quantity:
    name: str
    expected: dict
    aliases: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    specificity: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Quantity":
        return cls(d["name"], d["expected"], list(d.get("aliases", [])), list(d.get("context", [])), d.get("specificity"))


@dataclass
class Node:
    id: str
    stage_type: str
    description: str
    params: dict = field(default_factory=dict)
    quantities: list[Quantity] = field(default_factory=list)
    require: str = "all"
    depends_on: list[str] = field(default_factory=list)
    gating: bool = True
    hidden: bool = False
    variant_of: str | None = None
    alternatives: list[str] = field(default_factory=list)
    probe: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            id=d["id"],
            stage_type=d["stage_type"],
            description=d["description"],
            params=dict(d.get("params", {})),
            quantities=[Quantity.from_dict(q) for q in d.get("quantities", [])],
            require=d.get("require", "all"),
            depends_on=list(d.get("depends_on", [])),
            gating=d.get("gating", True),
            hidden=d.get("hidden", False),
            variant_of=d.get("variant_of"),
            alternatives=list(d.get("alternatives", [])),
            probe=dict(d.get("probe", {})),
            provenance=dict(d.get("provenance", {})),
        )

    @property
    def semantic(self) -> bool:
        """A node without measurable quantities; evidenced by code patterns or a judge."""
        return not self.quantities


@dataclass
class Task:
    id: str
    answer_node: str
    question: str = ""
    ideal: dict | None = None
    label_status: str = "unverified"
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(d["id"], d["answer_node"], d.get("question", ""), d.get("ideal"), d.get("label_status", "unverified"), d.get("notes", ""))


class Graph:
    def __init__(self, group_id: str, nodes: list[Node], tasks: list[Task], raw: dict | None = None):
        self.group_id = group_id
        self.nodes: dict[str, Node] = {}
        self._duplicates = []
        for node in nodes:
            if node.id in self.nodes:
                self._duplicates.append(node.id)
            self.nodes[node.id] = node
        self.tasks: dict[str, Task] = {t.id: t for t in tasks}
        self.raw = raw or {}

    @classmethod
    def from_dict(cls, d: dict) -> "Graph":
        return cls(d["group_id"], [Node.from_dict(n) for n in d["nodes"]], [Task.from_dict(t) for t in d["tasks"]], d)

    @classmethod
    def load(cls, path: str | Path) -> "Graph":
        return cls.from_dict(load_doc(path))

    # ---- integrity -------------------------------------------------------------------------
    def semantic_errors(self) -> list[str]:
        errors = [f"duplicate node id {nid!r}" for nid in self._duplicates]
        for node in self.nodes.values():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    errors.append(f"node {node.id!r} depends on unknown node {dep!r}")
            for ref, label in [(node.variant_of, "variant_of"), *[(a, "alternatives") for a in node.alternatives]]:
                if ref is not None and ref not in self.nodes:
                    errors.append(f"node {node.id!r} {label} references unknown node {ref!r}")
            if node.variant_of == node.id:
                errors.append(f"node {node.id!r} is a variant of itself")
            names = [q.name for q in node.quantities]
            if len(names) != len(set(names)):
                errors.append(f"node {node.id!r} has duplicate quantity names")
        for task in self.tasks.values():
            if task.answer_node not in self.nodes:
                errors.append(f"task {task.id!r} answer_node {task.answer_node!r} is not a node")
        try:
            self.topo_order()
        except ValueError as exc:
            errors.append(str(exc))
        return errors

    # ---- structure -------------------------------------------------------------------------
    def topo_order(self) -> list[str]:
        """Topological order, stable with respect to the order nodes are declared in."""
        order, state = [], {}

        def visit(nid: str, stack: tuple[str, ...]) -> None:
            if state.get(nid) == "done":
                return
            if state.get(nid) == "active":
                raise ValueError("dependency cycle: " + " -> ".join(stack + (nid,)))
            state[nid] = "active"
            for dep in self.nodes[nid].depends_on:
                if dep in self.nodes:
                    visit(dep, stack + (nid,))
            state[nid] = "done"
            order.append(nid)

        for nid in self.nodes:
            visit(nid, ())
        return order

    def ancestors(self, node_id: str) -> set[str]:
        seen, todo = set(), list(self.nodes[node_id].depends_on)
        while todo:
            nid = todo.pop()
            if nid not in seen and nid in self.nodes:
                seen.add(nid)
                todo.extend(self.nodes[nid].depends_on)
        return seen

    def descendants(self, node_id: str) -> set[str]:
        return {nid for nid in self.nodes if node_id in self.ancestors(nid)}

    def task_path(self, task_id: str) -> list[str]:
        answer = self.tasks[task_id].answer_node
        members = self.ancestors(answer) | {answer}
        return [nid for nid in self.topo_order() if nid in members]

    def tasks_using(self, node_id: str, task_ids: list[str] | None = None) -> list[str]:
        ids = task_ids if task_ids is not None else list(self.tasks)
        return [t for t in ids if node_id in self.task_path(t)]

    def shared_nodes(self, task_ids: list[str] | None = None) -> list[str]:
        ids = task_ids if task_ids is not None else list(self.tasks)
        return [nid for nid in self.topo_order() if len(self.tasks_using(nid, ids)) >= 2]

    def variants(self, node_id: str) -> list[str]:
        return [nid for nid, n in self.nodes.items() if n.variant_of == node_id]

    def to_dict(self) -> dict[str, Any]:
        return self.raw


def load_graphs(paths: list[str | Path]) -> list[Graph]:
    """Load graphs from files and/or directories (all *.yaml, *.yml and *.json inside)."""
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.suffix.lower() in {".yaml", ".yml", ".json"})
        else:
            files.append(p)
    return [Graph.load(f) for f in files]

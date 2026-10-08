"""Score one run unit (trace) against one checkpoint graph, producing a gce.evaluation.v1 document."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from . import __version__
from .graph import Graph, Node
from .probes.deterministic import Evidence, QuantityResult, probe_patterns, probe_quantity
from .probes.judge import Judge, select_candidates
from .trace import Trace
from .values import answer_matches, matches

PASSING = {"pass", "implied", "n/a"}
FAILING = {"fail", "unsupported"}
CATEGORICAL = {"label", "string", "boolean", "set"}


@dataclass
class NodeResult:
    state: str = "absent"
    observed: Any = None
    confidence: str = "high"
    self_reported: bool = False
    evidence: list[Evidence] = field(default_factory=list)
    quantities: dict[str, QuantityResult] = field(default_factory=dict)
    bleed_from: str | None = None
    note: str = ""

    def to_dict(self) -> dict:
        doc: dict[str, Any] = {"state": self.state, "confidence": self.confidence, "self_reported": self.self_reported}
        if self.observed is not None:
            doc["observed"] = self.observed
        if self.quantities:
            doc["quantities"] = {n: {"state": q.state, "observed": q.observed, "confidence": q.confidence} for n, q in self.quantities.items()}
        doc["evidence"] = [e.to_dict() for e in self.evidence[:5]]
        if self.bleed_from:
            doc["bleed_from"] = self.bleed_from
        if self.note:
            doc["note"] = self.note
        return doc


def _combine(node: Node, results: dict[str, QuantityResult]) -> NodeResult:
    states = [r.state for r in results.values()]
    out = NodeResult(quantities=results)
    passed = [r for r in results.values() if r.state == "pass"]
    if node.require == "any":
        decisive = passed or [r for r in results.values() if r.state in FAILING]
        out.state = "pass" if passed else ("fail" if "fail" in states else "unsupported" if "unsupported" in states else "absent")
    else:
        decisive = [r for r in results.values() if r.state in FAILING] or passed
        if "fail" in states:
            out.state = "fail"
        elif "unsupported" in states:
            out.state = "unsupported"
        elif passed:
            out.state = "pass"
            missing = [r.name for r in results.values() if r.state == "absent"]
            if missing:
                out.confidence, out.note = "low", "partial: not observed " + ", ".join(missing)
        else:
            out.state = "absent"
    if decisive:
        first = decisive[0]
        out.observed = first.observed
        out.confidence = "low" if out.confidence == "low" or any(r.confidence == "low" for r in decisive) else "high"
        out.self_reported = all(r.self_reported for r in decisive)
        out.note = "; ".join(x for x in [out.note, *(r.note for r in decisive if r.note)] if x)
    out.evidence = [e for r in decisive for e in r.evidence]
    return out


def _representative(spec: dict) -> float | None:
    if spec.get("type") == "range":
        return (float(spec["min"]) + float(spec["max"])) / 2
    value = spec.get("value")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _ambiguous_quantities(graph: Graph) -> set[tuple[str, str]]:
    """(node, quantity) pairs whose expectation is also met by a *different* quantity's reference value
    elsewhere in the graph. The same quantity repeated on another node (same name, e.g. a result node
    and the answer node reporting it, possibly with the official tolerance) is not ambiguous."""
    out = set()
    for node in graph.nodes.values():
        for q in node.quantities:
            for other in graph.nodes.values():
                for q2 in other.quantities:
                    if other.id == node.id or q2.name == q.name:
                        continue
                    value = _representative(q2.expected)
                    if value is not None and matches(q.expected, value):
                        out.add((node.id, q.name))
    return out


class Scorer:
    def __init__(self, graph: Graph, judge: Judge | None = None):
        self.graph = graph
        self.judge = judge
        self.ambiguous = _ambiguous_quantities(graph)

    # ---- node probing ------------------------------------------------------------------------
    def _probe(self, node: Node, trace: Trace, users: list[str]) -> NodeResult:
        if node.quantities:
            return _combine(node, {q.name: probe_quantity(q, trace, users, (node.id, q.name) in self.ambiguous) for q in node.quantities})
        ev = probe_patterns(node, trace, users)
        if ev:
            return NodeResult(state="pass", evidence=[ev])
        method = node.probe.get("method", "auto")
        if self.judge is not None and method in ("auto", "judge"):
            verdict = self.judge.judge(node, select_candidates(node, trace, users))
            if verdict is not None:
                return NodeResult(state=verdict.state, confidence="low", evidence=[Evidence("judge", verdict.ref or "-", verdict.rationale)])
        return NodeResult(state="absent", note="semantic node: no pattern matched" + ("" if self.judge else " (no judge)"))

    def _probe_answer(self, node: Node, trace: Trace, owners: list[str], users: list[str]) -> NodeResult:
        """Answer nodes are decided by the final answer when one was given."""
        resolved = {t: trace.final_answers[t] for t in owners if t in trace.final_answers and trace.final_answers[t].status == "resolved"}
        if not resolved:
            if node.quantities and all(q.expected["type"] in CATEGORICAL for q in node.quantities) and not trace.stage_log:
                # a category merely mentioned in output ("1-50", a pathway name) is not a conclusion
                return NodeResult(state="absent", note="categorical answer not reported; mentions in output are not conclusions")
            result = self._probe(node, trace, users)
            if result.state == "pass":
                result.note = "value computed but not reported as a final answer"
            return result
        verdicts = {t: any(answer_matches(q.expected, v) for q in node.quantities for v in fa.values()[:1]) for t, fa in resolved.items()}
        first = next(iter(resolved.values()))
        state = "pass" if all(verdicts.values()) else "fail"
        out = NodeResult(state=state, observed=first.answer, evidence=[Evidence("final_answer", t, str(fa.answer)[:200]) for t, fa in resolved.items()])
        if state == "fail":
            computed = self._probe(node, trace, users)
            if computed.state == "pass":
                out.note = "expected value appears in the run, but the final answer differs"
        return out

    # ---- evaluation --------------------------------------------------------------------------
    def evaluate(self, trace: Trace) -> dict:
        g = self.graph
        tasks = [t for t in trace.tasks_covered if t in g.tasks]
        if not tasks:
            raise ValueError(f"trace {trace.run_unit_id} covers no task of graph {g.group_id}")
        paths = {t: g.task_path(t) for t in tasks}
        scope = [nid for nid in g.topo_order() if any(nid in p for p in paths.values())]
        extra = [a for nid in scope for a in g.nodes[nid].alternatives if a not in scope]
        answer_owner = {}
        for t in tasks:
            answer_owner.setdefault(g.tasks[t].answer_node, []).append(t)

        results: dict[str, NodeResult] = {}
        for nid in scope + extra:
            users = [t for t in tasks if nid in paths[t]] or tasks
            node = g.nodes[nid]
            if nid in answer_owner:
                results[nid] = self._probe_answer(node, trace, answer_owner[nid], users)
            else:
                results[nid] = self._probe(node, trace, users)

        raw_pass = {nid for nid, r in results.items() if r.state == "pass"}
        # only a pass on a measured value vouches for upstream steps; a method pattern ("ran a chi-square
        # test") or a judge verdict says nothing about the data that went into it
        value_pass = {nid for nid in raw_pass if g.nodes[nid].quantities}
        for nid in scope:
            r, node = results[nid], g.nodes[nid]
            if r.state in ("absent", "fail") and any(a in raw_pass for a in node.alternatives):
                r.state, r.note = "n/a", "an alternative route passed"
            elif r.state == "absent" and g.descendants(nid) & value_pass & set(scope):
                r.state = "implied"
            if r.state == "fail" and r.observed is not None:
                r.bleed_from = self._bleed_source(node, r)

        task_docs = {t: self._task_score(t, paths[t], results, trace) for t in tasks}
        return {
            "schema": "gce.evaluation.v1",
            "evaluator": {"version": __version__, "judge": getattr(self.judge, "name", None)},
            "group_id": g.group_id,
            "run": trace.run,
            "capabilities": trace.capabilities,
            "nodes": {nid: results[nid].to_dict() for nid in scope},
            "tasks": task_docs,
            "group_metrics": self._group_metrics(tasks, paths, scope, results, task_docs, trace) if len(tasks) > 1 else None,
        }

    def _bleed_source(self, node: Node, result: NodeResult) -> str | None:
        """A failing node whose observed value is what a sibling variant expects."""
        g = self.graph
        base = node.variant_of or node.id
        siblings = [base, *g.variants(base)]
        for sib in siblings:
            if sib == node.id:
                continue
            for q in g.nodes[sib].quantities:
                observed = result.quantities.get(q.name).observed if q.name in result.quantities else None
                if observed is not None and matches(q.expected, observed):
                    return sib
        return None

    def _task_score(self, task: str, path: list[str], results: dict[str, NodeResult], trace: Trace) -> dict:
        g = self.graph
        gating = [n for n in path if g.nodes[n].gating]
        depth, blocked = 0, False
        first_failure = first_gap = None
        for nid in gating:
            state = results[nid].state
            if state in PASSING and not blocked:
                depth += 1
                continue
            blocked = blocked or state not in PASSING
            if state in FAILING and first_failure is None:
                first_failure = nid
            if state == "absent" and first_gap is None:
                first_gap = nid
        fa = trace.final_answers.get(task)
        answer_node = g.nodes[g.tasks[task].answer_node]
        observed = fa.answer if fa else None
        correct = None
        if fa is not None:
            correct = fa.status == "resolved" and any(answer_matches(q.expected, observed) for q in answer_node.quantities)
        ideal = g.tasks[task].ideal
        return {
            "answer_status": fa.status if fa else "missing",
            "answer_observed": observed,
            "answer_correct": correct,
            "answer_correct_vs_ideal": (fa is not None and fa.status == "resolved" and answer_matches(ideal, observed)) if ideal else None,
            "path": path,
            "path_length": len(gating),
            "depth": depth,
            "depth_fraction": round(depth / len(gating), 4) if gating else 1.0,
            "first_failure": first_failure,
            "first_failure_stage": g.nodes[first_failure].stage_type if first_failure else None,
            "first_gap": first_gap,
            "first_gap_stage": g.nodes[first_gap].stage_type if first_gap else None,
            "hidden_misses": [n for n in path if g.nodes[n].hidden and results[n].state in FAILING | {"absent"}],
            "states": dict(Counter(results[n].state for n in path)),
        }

    def _group_metrics(self, tasks, paths, scope, results, task_docs, trace) -> dict:
        g = self.graph
        failed = {n for n in scope if results[n].state in FAILING}
        wrong = [t for t in tasks if task_docs[t]["answer_correct"] is not True]
        roots = []
        for nid in scope:
            if nid in failed and not (g.ancestors(nid) & failed):
                roots.append({"node": nid, "stage_type": g.nodes[nid].stage_type, "affected_tasks": [t for t in wrong if nid in paths[t]]})
        shared = [n for n in g.shared_nodes(tasks) if n in scope]
        consistency, reuse = {}, {}
        for nid in shared:
            observations = [(pos, val) for q in results[nid].quantities.values() for pos, _, val in q.named_observations]
            if observations:
                distinct = sorted({repr(v) for _, v in observations})
                consistency[nid] = {"values": distinct, "consistent": len(distinct) == 1}
                reuse[nid] = len({pos for pos, _ in observations})
        bleed = [{"node": n, "bleed_from": results[n].bleed_from, "tasks": [t for t in tasks if n in paths[t]], "observed": results[n].observed}
                 for n in scope if results[n].bleed_from]
        return {
            "root_causes": roots,
            "failed_tasks": len(wrong),
            "consistency": {"nodes": consistency, "consistent_fraction": round(sum(v["consistent"] for v in consistency.values()) / len(consistency), 4) if consistency else None},
            "parameter_bleed": bleed,
            "reuse": {"observations_per_shared_node": reuse, "recomputed": sorted(n for n, k in reuse.items() if k > 1)},
        }


def evaluate(graph: Graph, trace: Trace, judge: Judge | None = None) -> dict:
    return Scorer(graph, judge).evaluate(trace)

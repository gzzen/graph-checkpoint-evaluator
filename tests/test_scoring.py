import copy
from pathlib import Path

from gce.graph import Graph
from gce.io import load_doc
from gce.probes.judge import Verdict
from gce.schemas import schema_errors
from gce.scoring import evaluate
from gce.trace import Trace

FIX = Path(__file__).parent / "fixtures"


def graph():
    return Graph.load(FIX / "demo_graph.yaml")


def run(name, **kw):
    doc = evaluate(graph(), Trace.load(FIX / name), **kw)
    assert schema_errors(doc, "evaluation") == []
    return doc


def states(doc):
    return {n: r["state"] for n, r in doc["nodes"].items()}


def test_correct_single_task_run_passes_every_checkpoint():
    doc = run("trace_single_good.json")
    assert states(doc) == {"n_load": "pass", "n_cohort": "pass", "n_test": "pass", "n_stat": "pass", "a_q1": "pass"}
    task = doc["tasks"]["demo-q1"]
    assert (task["depth"], task["path_length"], task["first_failure"], task["answer_correct"]) == (5, 5, None, True)
    assert doc["group_metrics"] is None
    assert doc["nodes"]["n_cohort"]["self_reported"] is False  # corroborated by raw output


def test_wrong_run_locates_first_failure_at_hidden_cohort_step():
    doc = run("trace_single_wrong.json")
    s = states(doc)
    assert s["n_load"] == "pass"
    assert s["n_cohort"] == "fail" and doc["nodes"]["n_cohort"]["observed"] == 100
    assert s["n_test"] == "absent"  # Student t-test: no Welch pattern
    assert s["n_stat"] == "fail" and s["a_q1"] == "fail"
    task = doc["tasks"]["demo-q1"]
    assert task["depth"] == 1
    assert task["first_failure"] == "n_cohort" and task["first_failure_stage"] == "cohort"
    assert task["first_gap"] == "n_test"
    assert task["hidden_misses"] == ["n_cohort"]
    assert task["answer_correct"] is False


def test_multi_task_unit_detects_parameter_bleed_and_root_cause():
    doc = run("trace_multi_bleed.json")
    s = states(doc)
    assert s["n_cohort"] == "pass" and s["n_cohort_all"] == "fail"
    assert doc["nodes"]["n_cohort_all"]["bleed_from"] == "n_cohort"
    gm = doc["group_metrics"]
    assert gm["root_causes"] == [{"node": "n_cohort_all", "stage_type": "cohort", "affected_tasks": ["demo-q2"]}]
    assert gm["failed_tasks"] == 1
    assert gm["parameter_bleed"][0]["bleed_from"] == "n_cohort"
    assert gm["consistency"]["nodes"]["n_load"]["consistent"] is True
    assert doc["tasks"]["demo-q1"]["answer_correct"] is True
    assert doc["tasks"]["demo-q2"]["first_failure"] == "n_cohort_all"


def test_unsupported_stage_log_claim():
    doc = load_doc(FIX / "trace_single_good.json")
    doc["steps"] = [s for s in doc["steps"] if s["index"] != 2]  # drop the output that printed 98 and the test
    doc["steps"][-1]["index"] = 1
    result = evaluate(graph(), Trace.from_dict(doc))
    assert result["nodes"]["n_cohort"]["state"] == "unsupported"
    assert result["tasks"]["demo-q1"]["first_failure"] == "n_cohort"


def test_stage_log_only_trace_is_self_reported():
    doc = load_doc(FIX / "trace_single_good.json")
    doc["steps"], doc["capabilities"]["raw_steps"] = [], False
    result = evaluate(graph(), Trace.from_dict(doc))
    assert result["nodes"]["n_cohort"]["state"] == "pass"
    assert result["nodes"]["n_cohort"]["self_reported"] is True


def test_implied_when_only_the_answer_is_visible():
    doc = load_doc(FIX / "trace_single_good.json")
    doc["steps"], doc["stage_log"] = [], []
    doc["capabilities"] = {"raw_steps": False, "stage_log": False, "replayable_code": False}
    result = evaluate(graph(), Trace.from_dict(doc))
    assert states(result) == {"n_load": "implied", "n_cohort": "implied", "n_test": "implied", "n_stat": "implied", "a_q1": "pass"}
    assert result["tasks"]["demo-q1"]["depth"] == 5


def test_alternative_route_is_not_counted_as_failure():
    g = load_doc(FIX / "demo_graph.yaml")
    g = copy.deepcopy(g)
    g["nodes"].append({"id": "n_test_mwu", "stage_type": "model", "description": "Mann-Whitney U test", "depends_on": ["n_cohort"], "gating": False,
                       "probe": {"patterns": ["mannwhitneyu"]}})
    g["nodes"][3]["alternatives"] = ["n_test_mwu"]  # n_test
    t = load_doc(FIX / "trace_single_wrong.json")
    t["steps"][1]["input"] += "\nfrom scipy.stats import mannwhitneyu"
    result = evaluate(Graph.from_dict(g), Trace.from_dict(t))
    assert result["nodes"]["n_test"]["state"] == "n/a"


class FakeJudge:
    name = "fake"

    def judge(self, node, candidates):
        return Verdict("fail", candidates[0].ref if candidates else None, "used Student's t-test")


def test_judge_decides_semantic_nodes_without_pattern_match():
    doc = run("trace_single_wrong.json", judge=FakeJudge())
    assert doc["nodes"]["n_test"]["state"] == "fail"
    assert doc["nodes"]["n_test"]["evidence"][0]["source"] == "judge"
    assert doc["evaluator"]["judge"] == "fake"


def test_method_pattern_pass_does_not_imply_upstream_values():
    doc = load_doc(FIX / "trace_single_good.json")
    doc["stage_log"], doc["capabilities"]["stage_log"] = [], False
    doc["steps"] = [{"index": 0, "kind": "file_write", "tool": "write", "path": "t.py", "input": "ttest_ind(a, b, equal_var=False)"}]
    doc["capabilities"]["raw_steps"] = False
    doc["final_answers"] = {"demo-q1": {"status": "missing"}}
    result = evaluate(graph(), Trace.from_dict(doc))
    assert result["nodes"]["n_test"]["state"] == "pass"            # the method was used ...
    assert result["nodes"]["n_cohort"]["state"] == "absent"        # ... but that says nothing about the cohort

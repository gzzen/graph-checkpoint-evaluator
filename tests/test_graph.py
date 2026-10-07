import copy
from pathlib import Path

from gce.graph import Graph
from gce.io import load_doc

FIX = Path(__file__).parent / "fixtures"


def demo():
    return Graph.load(FIX / "demo_graph.yaml")


def test_task_paths_follow_dependencies():
    g = demo()
    assert g.task_path("demo-q1") == ["n_load", "n_cohort", "n_test", "n_stat", "a_q1"]
    assert g.task_path("demo-q2") == ["n_load", "n_cohort_all", "a_q2"]


def test_shared_nodes_and_variants():
    g = demo()
    assert g.shared_nodes() == ["n_load"]
    assert g.variants("n_cohort") == ["n_cohort_all"]
    assert g.tasks_using("n_cohort") == ["demo-q1"]


def test_semantic_errors_clean_graph():
    assert demo().semantic_errors() == []


def test_semantic_errors_detects_cycle_and_unknown_refs():
    doc = load_doc(FIX / "demo_graph.yaml")
    bad = copy.deepcopy(doc)
    bad["nodes"][0]["depends_on"] = ["a_q1"]  # n_load <- a_q1 <- ... <- n_load
    bad["nodes"][1]["depends_on"].append("missing")
    bad["tasks"][0]["answer_node"] = "nope"
    errors = Graph.from_dict(bad).semantic_errors()
    assert any("cycle" in e for e in errors)
    assert any("unknown node 'missing'" in e for e in errors)
    assert any("answer_node 'nope'" in e for e in errors)

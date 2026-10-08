from pathlib import Path

from gce.graph import Graph
from gce.paths import describe_spec, document_name, group_documents
from gce.report import write_report
from gce.scoring import evaluate
from gce.trace import Trace

FIX = Path(__file__).parent / "fixtures"


def evals_and_graph():
    graph = Graph.load(FIX / "demo_graph.yaml")
    names = ["trace_single_good.json", "trace_single_wrong.json", "trace_multi_bleed.json"]
    return [evaluate(graph, Trace.load(FIX / n)) for n in names], graph


def test_one_document_per_group_with_a_view_per_task():
    evals, graph = evals_and_graph()
    docs = group_documents(evals, [graph])
    assert sorted(docs) == [("group", "demo/two-sample"), ("single", "demo/two-sample")]  # conditions never pooled
    group = docs[("group", "demo/two-sample")]
    assert group.count("```mermaid") == 3 and "## demo-q1" in group and "## demo-q2" in group  # full graph + 2 tasks
    single = docs[("single", "demo/two-sample")]
    assert "## demo-q2" not in single and "seed 1" in single and "seed 2" in single
    assert single.index("## Full checkpoint graph") < single.index("## demo-q1")
    full = single.split("## Full checkpoint graph")[1].split("## demo-q1")[0]
    assert "n_cohort_all" in full and "Not evaluated" in full     # whole graph, incl. q2-only nodes
    assert "answer to demo-q2" in full and "| n_load | inputs |" in full
    text = group + single
    q1 = single.split("## demo-q1")[1]
    assert "**Fail: 100** (first failure)" in q1          # wrong run stops at the hidden cohort step
    assert "n_n_cohort --> n_n_test" in q1               # graph edges
    assert "Official answer: 0.013" in q1
    assert not any(sym in text for sym in "✓✗▶⧉↑⊘")       # formal wording only


def test_report_writes_group_files(tmp_path):
    evals, graph = evals_and_graph()
    out = write_report(evals, tmp_path / "report", [graph])
    assert (out / "paths" / "single" / "two-sample.md").is_file()
    assert (out / "paths" / "group" / "two-sample.md").is_file()
    summary = (out / "summary.md").read_text()
    assert "(paths/single/two-sample.md)" in summary and "(paths/group/two-sample.md)" in summary


def test_condition_dirs_disambiguate_shared_condition_names():
    from gce.paths import condition_dirs
    evals = [{"run": {"experiment": e, "condition": c}} for e, c in [("exp1", "native"), ("exp2", "native"), ("exp1", "b")]]
    assert condition_dirs(evals) == {("exp1", "b"): "b", ("exp1", "native"): "exp1__native", ("exp2", "native"): "exp2__native"}


def test_names_and_specs():
    assert document_name("bixbench-v1.5/bix-10") == "bix-10.md"
    assert describe_spec({"type": "range", "min": 1.54, "max": 1.5}) == "1.5 to 1.54"

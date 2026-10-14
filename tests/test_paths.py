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
    # conditions and repeated attempts (seeds) are never pooled
    assert sorted(docs) == [("group_seed01", "demo/two-sample"), ("single_seed01", "demo/two-sample"), ("single_seed02", "demo/two-sample")]
    group = docs[("group_seed01", "demo/two-sample")]
    assert group.count("```mermaid") == 3 and "## demo-q1" in group and "## demo-q2" in group  # full graph + 2 tasks
    assert "condition group, seed 1" in group and "| Capsule run |" in group
    single = docs[("single_seed01", "demo/two-sample")]
    wrong = docs[("single_seed02", "demo/two-sample")]
    assert "## demo-q2" not in single and "condition single, seed 1" in single and "seed 2" not in single
    assert "| Single-question run |" in single
    assert single.index("## Full checkpoint graph") < single.index("## demo-q1")
    full = single.split("## Full checkpoint graph")[1].split("## demo-q1")[0]
    assert "n_cohort_all" in full and "Not evaluated" in full     # whole graph, incl. q2-only nodes
    assert "answer to demo-q2" in full and "| n_load | inputs |" in full
    names = single.split("## Reading the checkpoint names")[1].split("## Definitions")[0]
    assert "| n_ | " in names and "| a_ | Answer:" in names and "n_cohort_all of n_cohort" in names
    q1 = single.split("## demo-q1")[1]
    assert "| n_cohort | Exclude the two outlier samples" in q1 and "(a reference choice the question does not state)" in q1
    assert "| n_load | Load the measurement table. | All questions |" in q1
    assert "| a_q1 | The value this question asks for:" in q1
    text = group + single + wrong
    q1 = wrong.split("## demo-q1")[1]
    assert "**Fail: 100** (first failure)" in q1          # wrong run stops at the hidden cohort step
    assert "**Fail: 100**" not in single
    assert "n_n_cohort --> n_n_test" in q1               # graph edges
    assert "Official answer: 0.013" in q1
    assert not any(sym in text for sym in "✓✗▶⧉↑⊘")       # formal wording only


def test_report_writes_group_files(tmp_path):
    evals, graph = evals_and_graph()
    out = write_report(evals, tmp_path / "report", [graph])
    for attempt in ("single_seed01", "single_seed02", "group_seed01"):
        assert (out / "paths" / attempt / "two-sample.md").is_file()
    assert not (out / "paths" / "single").exists()
    summary = (out / "summary.md").read_text()
    assert "(paths/single_seed01/two-sample.md)" in summary and "(paths/single_seed02/two-sample.md)" in summary
    by_attempt = summary.split("## By attempt")[1].split("##")[0]
    assert "| demo/single/seed01 | 1 |" in by_attempt and "| demo/single/seed02 | 1 |" in by_attempt


def test_attempt_dirs_keep_seeds_apart_and_disambiguate_experiments():
    from gce.paths import attempt_dirs
    runs = [("exp1", "native", 1), ("exp2", "native", 1), ("exp1", "b", 1), ("exp1", "b", 2), ("exp1", "x", None)]
    evals = [{"run": {"experiment": e, "condition": c, "seed": s}} for e, c, s in runs]
    assert attempt_dirs(evals) == {("exp1", "b", "seed01"): "b_seed01", ("exp1", "b", "seed02"): "b_seed02",
                                   ("exp1", "native", "seed01"): "exp1__native_seed01", ("exp2", "native", "seed01"): "exp2__native_seed01",
                                   ("exp1", "x", ""): "x"}


def test_names_and_specs():
    assert document_name("bixbench-v1.5/bix-10") == "bix-10.md"
    assert describe_spec({"type": "range", "min": 1.54, "max": 1.5}) == "1.5 to 1.54"

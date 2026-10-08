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
    assert list(docs) == ["demo/two-sample"]
    text = docs["demo/two-sample"]
    assert text.count("```mermaid") == 2 and "## demo-q1" in text and "## demo-q2" in text
    q1 = text.split("## demo-q1")[1].split("## demo-q2")[0]
    assert "**Fail: 100** (first failure)" in q1          # wrong run stops at the hidden cohort step
    assert "n_n_cohort --> n_n_test" in q1               # graph edges
    assert "Official answer: 0.013" in q1
    assert not any(sym in text for sym in "✓✗▶⧉↑⊘")       # formal wording only


def test_report_writes_group_files(tmp_path):
    evals, graph = evals_and_graph()
    out = write_report(evals, tmp_path / "report", [graph])
    assert (out / "paths" / "two-sample.md").is_file()
    assert "(paths/two-sample.md)" in (out / "summary.md").read_text()


def test_names_and_specs():
    assert document_name("bixbench-v1.5/bix-10") == "bix-10.md"
    assert describe_spec({"type": "range", "min": 1.54, "max": 1.5}) == "1.5 to 1.54"

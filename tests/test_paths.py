from pathlib import Path

from gce.graph import Graph
from gce.paths import describe_spec, paths_markdown
from gce.scoring import evaluate
from gce.trace import Trace

FIX = Path(__file__).parent / "fixtures"


def test_paths_section_has_one_view_per_task():
    graph = Graph.load(FIX / "demo_graph.yaml")
    evals = [evaluate(graph, Trace.load(FIX / n)) for n in ["trace_single_good.json", "trace_single_wrong.json", "trace_multi_bleed.json"]]
    text = "\n".join(paths_markdown(evals, [graph]))
    assert text.count("### demo-q") == 2 and text.count("```mermaid") == 2
    q1 = text.split("### demo-q1")[1].split("### demo-q2")[0]
    assert "▶ **✗? 100**" in q1 or "▶ **✗ 100**" in q1   # wrong run stops at the hidden cohort step, observed 100
    assert "n_n_cohort --> n_n_test" in q1               # DAG edges from the graph
    assert "| **final answer** | | ideal 0.013 |" in q1


def test_describe_spec():
    assert describe_spec({"type": "range", "min": 1.54, "max": 1.5}) == "1.5–1.54"
    assert describe_spec({"type": "percent", "value": 10.6}) == "10.6%"

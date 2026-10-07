import csv
import shutil
from pathlib import Path

from gce.cli import main

FIX = Path(__file__).parent / "fixtures"


def test_score_and_report_end_to_end(tmp_path, capsys):
    traces = tmp_path / "traces"
    traces.mkdir()
    for name in ["trace_single_good.json", "trace_single_wrong.json", "trace_multi_bleed.json"]:
        shutil.copy(FIX / name, traces / name)
    evals, report = tmp_path / "evals", tmp_path / "report"
    assert main(["score", "--graphs", str(FIX / "demo_graph.yaml"), "--traces", str(traces), "--out", str(evals)]) == 0
    assert len(list(evals.glob("*.eval.json"))) == 3
    assert main(["validate", *map(str, evals.glob("*.eval.json"))]) == 0
    assert main(["report", "--evals", str(evals), "--out", str(report)]) == 0

    rows = list(csv.DictReader((report / "tasks.csv").open()))
    assert len(rows) == 4
    wrong = next(r for r in rows if r["run_unit_id"] == "demo/single/demo-q1/seed2")
    assert wrong["first_failure"] == "n_cohort" and wrong["hidden_misses"] == "n_cohort"
    summary = (report / "summary.md").read_text()
    assert "## Multi-task run units" in summary
    assert "## Checkpoints that vary across run units" in summary  # seed1 vs seed2 of the single condition

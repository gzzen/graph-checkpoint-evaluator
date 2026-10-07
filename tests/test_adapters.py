import json
import shutil
from pathlib import Path

import pytest

from gce.adapter_check import check_adapter
from gce.adapters.base import Adapter, RunUnitRef, load_adapter
from gce.cli import main
from gce.graph import Graph
from gce.io import load_doc
from gce.schemas import schema_errors

FIX = Path(__file__).parent / "fixtures"
REPO = Path(__file__).parents[1]


@pytest.fixture
def run_root(tmp_path):
    """A canonical layout with one unit stored as parts and one stored as a full trace.json."""
    root = tmp_path / "runs"
    parts = root / "exp" / "single" / "u1"
    parts.mkdir(parents=True)
    good = load_doc(FIX / "trace_single_good.json")
    (parts / "meta.json").write_text(json.dumps({"run_unit_id": "exp/u1", "tasks_covered": ["demo-q1"], "experiment": "exp", "seed": 1, "harness": "x"}))
    (parts / "answers.json").write_text(json.dumps({"demo-q1": "0.013"}))
    (parts / "stage_log.jsonl").write_text("\n".join(json.dumps(e) for e in good["stage_log"]))
    (parts / "steps.jsonl").write_text("\n".join(json.dumps({k: v for k, v in s.items() if k != "index"}) for s in good["steps"]))
    whole = root / "exp" / "group" / "u2"
    whole.mkdir(parents=True)
    shutil.copy(FIX / "trace_multi_bleed.json", whole / "trace.json")
    return root


def test_canonical_dir_builds_valid_traces(run_root):
    adapter = load_adapter("canonical_dir")
    refs = list(adapter.discover(run_root))
    assert [r.id for r in refs] == ["demo/group/seed1", "exp/u1"]
    trace = adapter.to_trace(refs[1])
    assert schema_errors(trace, "trace") == []
    assert trace["capabilities"] == {"raw_steps": True, "stage_log": True, "replayable_code": False}
    assert trace["final_answers"]["demo-q1"] == {"status": "resolved", "answer": "0.013"}
    assert trace["extras"]["meta"] == {"harness": "x"}


def test_adapter_check_passes_and_knows_tasks(run_root):
    graphs = [Graph.load(FIX / "demo_graph.yaml")]
    report = check_adapter(load_adapter("canonical_dir"), run_root, graphs=graphs)
    assert report.ok, report.render()
    assert report.statuses == {"resolved": 3}


class WritingAdapter(Adapter):
    """Violates the read-only rule on purpose."""

    def discover(self, run_root):
        yield RunUnitRef("x", str(run_root))

    def to_trace(self, ref):
        Path(ref.location, "scratch.txt").write_text("oops")
        doc = load_doc(FIX / "trace_single_good.json")
        doc["run"]["run_unit_id"] = "x"
        return doc


def test_adapter_check_detects_writes(tmp_path):
    report = check_adapter(WritingAdapter(), tmp_path)
    assert not report.ok
    assert any("read-only" in e for e in report.errors)


def test_missing_answers_are_reported(tmp_path):
    unit = tmp_path / "u"
    unit.mkdir()
    (unit / "meta.json").write_text(json.dumps({"tasks_covered": ["t1", "t2"]}))
    (unit / "answers.json").write_text(json.dumps({"t1": "5"}))
    trace = load_adapter("canonical_dir").to_trace(RunUnitRef("u", str(unit)))
    assert trace["final_answers"]["t2"] == {"status": "missing"}


def test_template_adapter_loads_from_file():
    adapter = load_adapter(str(REPO / "adapters" / "TEMPLATE.py"))
    assert adapter.name == "template"


def test_cli_adapt_writes_traces(run_root, tmp_path, capsys):
    out = tmp_path / "traces"
    assert main(["adapt", "--adapter", "canonical_dir", "--run-root", str(run_root), "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["demo__group__seed1.trace.json", "exp__u1.trace.json"]

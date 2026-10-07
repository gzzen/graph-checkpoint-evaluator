import copy
from pathlib import Path

import pytest

from gce.cli import main
from gce.io import load_doc
from gce.schemas import detect_kind, schema_errors

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name", ["demo_graph.yaml", "trace_single_good.json", "trace_single_wrong.json", "trace_multi_bleed.json"])
def test_fixtures_are_schema_valid(name):
    doc = load_doc(FIX / name)
    kind = detect_kind(doc)
    assert kind is not None
    assert schema_errors(doc, kind) == []


def test_stage_log_entries_validate_individually():
    log = load_doc(FIX / "trace_multi_bleed.json")["stage_log"]
    assert schema_errors(log, "stage_log_entry") == []
    assert schema_errors([{"outputs": {}}], "stage_log_entry")  # missing action


def test_graph_rejects_unknown_value_type():
    doc = load_doc(FIX / "demo_graph.yaml")
    bad = copy.deepcopy(doc)
    bad["nodes"][0]["quantities"][0]["expected"] = {"type": "fuzzy", "value": 1}
    assert schema_errors(bad, "checkpoint_graph")


def test_trace_requires_capabilities():
    doc = load_doc(FIX / "trace_single_good.json")
    del doc["capabilities"]
    errors = schema_errors(doc, "trace")
    assert any("capabilities" in e for e in errors)


def test_cli_validate(capsys):
    assert main(["validate", str(FIX / "demo_graph.yaml"), str(FIX / "trace_single_good.json")]) == 0
    assert "OK" in capsys.readouterr().out

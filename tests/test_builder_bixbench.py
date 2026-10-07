"""Builder tests that need no local BixBench data."""
from pathlib import Path

import pytest

from builders.bixbench.draft import draft_graph
from builders.bixbench.extract import PatchError, build_script
from builders.bixbench.notebooks import Cell
from builders.bixbench.questions import ideal_spec
from builders.bixbench.rerun import parse_checkpoints
from gce.graph import Graph
from gce.schemas import schema_errors


def cell(i, src, kind="code"):
    return Cell(i, kind, src, "", [], [])


@pytest.mark.parametrize(
    "row, spec",
    [
        ({"ideal": "(1.13e-10,1.13e-12)", "eval_mode": "range_verifier"}, {"type": "range", "min": 1.13e-10, "max": 1.13e-12}),
        ({"ideal": "(-0.064,-0.084) ", "eval_mode": "range_verifier"}, {"type": "range", "min": -0.064, "max": -0.084}),
        ({"ideal": "1.9E-05", "eval_mode": "str_verifier"}, {"type": "number", "value": 1.9e-05, "sig_figs": 2}),
        ({"ideal": "5.107 x 10^-47", "eval_mode": "llm_verifier"}, {"type": "number", "value": 5.107e-47, "sig_figs": 4}),
        ({"ideal": "CD14 Monocytes", "eval_mode": "str_verifier"}, {"type": "label", "value": "CD14 Monocytes"}),
    ],
)
def test_ideal_spec(row, spec):
    assert ideal_spec(row) == spec


def test_build_script_r_strips_installs_and_instruments(tmp_path):
    cells = [
        cell(0, "intro", "markdown"),
        cell(1, '%%R\nx <- 42'),
        cell(2, 'if (!require("BiocManager", quietly = TRUE))\n    install.packages("BiocManager")\nBiocManager::install("DESeq2")\nlibrary(DESeq2)'),
        cell(3, 'counts <- read_csv("raw.csv")\nres <- results(dds, alpha = 0.1)'),
    ]
    spec = {"language": "R", "drop_cells": [1], "insert_after": {3: 'gce_checkpoint("n", nrow(res), cell = 3)'},
            "patches": [{"cell": 3, "find": "alpha = 0.1", "replace": "alpha = 0.05", "why": "test"}]}
    script, applied = build_script(cells, spec, tmp_path, tmp_path / "Rlib")
    assert "install.packages" not in script and "BiocManager::install" not in script
    assert "x <- 42" not in script and "library(DESeq2)" in script
    assert "alpha = 0.05" in script and applied == ["cell 3: test"]
    assert script.index("##### CELL 3") < script.index('gce_checkpoint("n"')


def test_patch_must_match_once(tmp_path):
    with pytest.raises(PatchError):
        build_script([cell(0, "a <- 1")], {"language": "R", "patches": [{"cell": 0, "find": "zzz", "replace": "y"}]}, tmp_path, tmp_path)


def test_parse_checkpoints():
    log = 'noise\nGCE_CHECKPOINT {"name":"n_deg","value":2091,"cell":23} \n##### CELL 24\nGCE_CHECKPOINT {"name": "pct", "value": 10.6, "cell": null}\n'
    assert parse_checkpoints(log) == {"n_deg": {"value": 2091, "cell": 23}, "pct": {"value": 10.6, "cell": None}}


def test_draft_graph_is_valid():
    rows = [
        {"question_id": "bix-7-q1", "question": "How many groups?", "ideal": "0", "eval_mode": "str_verifier", "capsule_uuid": "u"},
        {"question_id": "bix-7-q2", "question": "Average count?", "ideal": "(40,60)", "eval_mode": "range_verifier", "capsule_uuid": "u"},
    ]
    doc = draft_graph("bix-7", rows)
    assert schema_errors(doc, "checkpoint_graph") == []
    assert Graph.from_dict(doc).semantic_errors() == []
    assert doc["source"]["archetype"] == "chip_variants"

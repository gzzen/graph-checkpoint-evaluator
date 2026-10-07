"""Regression tests for false positives found when calibrating on real OpenCode runs."""
from gce.graph import Graph, Node, Quantity
from gce.probes.deterministic import probe_patterns, probe_quantity
from gce.trace import Trace


def trace(steps, stage_log=()):
    return Trace.from_dict({
        "schema": "gce.trace.v1",
        "run": {"run_unit_id": "u", "tasks_covered": ["t"]},
        "capabilities": {"raw_steps": True, "stage_log": bool(stage_log), "replayable_code": False},
        "steps": [{"index": i, **s} for i, s in enumerate(steps)],
        "stage_log": list(stage_log),
        "final_answers": {"t": {"status": "missing"}},
    })


def q(spec, **kw):
    return Quantity(kw.pop("name", "x"), spec, kw.pop("aliases", []), kw.pop("context", []), kw.pop("specificity", None))


def test_file_reads_are_inputs_not_results():
    t = trace([{"kind": "file_read", "tool": "read", "output": '4: "version": "1.5",\n6: "question": "odds ratio for BCG"'}])
    assert probe_quantity(q({"type": "range", "min": 1.50, "max": 1.54}, context=["BCG"]), t, ["t"]).state == "absent"


def test_numbers_in_file_listings_are_ignored():
    listing = ("lineages/eukaryota_odb10/hmms/996662at2759.hmm 100998\n"
               "-rw-r----- 6 1018 1020 15924529 Sep 29 09:19 Animals_Cele.faa\n"
               "1287094at2759.faa.mafft.clipkit.bionj 100\n")
    t = trace([{"kind": "exec", "tool": "shell", "input": "unzip -l data/x.zip", "output": listing}])
    assert probe_quantity(q({"type": "integer", "value": 100}, context=["animal"]), t, ["t"]).state == "absent"
    res = probe_quantity(q({"type": "number", "value": -30.4551, "decimals": 4}, context=["996662"]), t, ["t"])
    assert res.state == "absent"  # no labelled-number fail from a file size


def test_value_with_fewer_decimals_than_range_needs_context():
    spec = {"type": "range", "min": 1.50, "max": 1.54}
    bare = trace([{"kind": "exec", "tool": "shell", "input": "python a.py", "output": "threshold 1.5 applied"}])
    assert probe_quantity(q(spec, context=["BCG"]), bare, ["t"]).state == "absent"
    precise = trace([{"kind": "exec", "tool": "shell", "input": "python a.py", "output": "TRTGRP_cat 1.524242"}])
    assert probe_quantity(q(spec, context=["BCG"]), precise, ["t"]).state == "pass"


def test_context_must_be_on_the_hit_line_to_disambiguate():
    out = "patients_seen=1-50: chi2=9.4207 p=0.0241896\nexpect_interact=Yes: chi2=9.3151 p=0.0253821\n"
    t = trace([{"kind": "exec", "tool": "shell", "input": "python chi.py", "output": out}])
    res = probe_quantity(q({"type": "range", "min": 0.024, "max": 0.026}, context=["expect", "\\bYes\\b"]), t, ["t"], require_context=True)
    assert res.state == "pass" and res.observed == 0.0253821


def test_inspection_commands_do_not_evidence_methods():
    node = Node("tf_trees", "transform", "infer trees", probe={"patterns": ["iqtree"]})
    looked_up = trace([{"kind": "exec", "tool": "shell", "input": 'pip list 2>/dev/null | grep -iE "phykit|iqtree"', "output": "phykit 2.4.1"}])
    assert probe_patterns(node, looked_up, ["t"]) is None
    ran = trace([{"kind": "exec", "tool": "shell", "input": "iqtree -s aln.fa -m TEST", "output": "done"}])
    assert probe_patterns(node, ran, ["t"]) is not None


def test_generic_context_patterns_are_rejected():
    g = Graph.from_dict({
        "schema": "gce.checkpoint_graph.v1", "group_id": "g", "tasks": [{"id": "t", "answer_node": "a"}],
        "nodes": [{"id": "a", "stage_type": "answer", "description": "d",
                   "quantities": [{"name": "p", "expected": {"type": "number", "value": 0.02}, "context": ["p", "\\bp\\b", "expect"]}]}],
    })
    errors = g.semantic_errors()
    assert len(errors) == 1 and "'p'" in errors[0]


def test_fail_needs_a_name_label_not_just_context():
    spec = {"type": "integer", "value": 791}
    quantity = q(spec, aliases=["n_obs", "observations"], context=["median", "rows"])
    noisy = trace([{"kind": "exec", "tool": "shell", "input": "python s.py",
                    "output": "mean, median, 25th percentile\nrows: 2696 data/AE.csv"}])
    assert probe_quantity(quantity, noisy, ["t"]).state == "absent"
    labelled = trace([{"kind": "exec", "tool": "shell", "input": "python s.py",
                       "output": "Dep. Variable: sev   No. Observations: 194"}])
    res = probe_quantity(quantity, labelled, ["t"])
    assert (res.state, res.observed, res.confidence) == ("fail", 194.0, "low")

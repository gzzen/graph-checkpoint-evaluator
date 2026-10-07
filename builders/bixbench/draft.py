"""Draft a checkpoint graph skeleton for a capsule: tasks with official ideals plus the archetype ladder.

The draft is a starting point for a human (or LLM-assisted) author, who fills in descriptions,
quantities, expected values and provenance from the notebook outline and re-run evidence.
"""
from __future__ import annotations

import copy

import yaml

from .questions import answer_visibility, ideal_spec

LADDERS = {
    "rnaseq_de": [
        ("inputs", "Load the count matrix and sample metadata."),
        ("cohort", "Define the samples and groups compared (and any exclusions)."),
        ("filter", "Filter low-count genes."),
        ("model", "Fit the differential expression model (design, reference level, contrast)."),
        ("result_table", "Differential expression results table."),
        ("downstream", "Select significant genes by the stated thresholds; set operations / enrichment."),
    ],
    "chip_variants": [
        ("inputs", "Load the per-sample variant tables and sample grouping."),
        ("filter", "Remove reference calls."),
        ("filter", "Keep exonic variants (drop intronic, intergenic, UTR)."),
        ("cohort", "Assign samples to BLM status groups."),
        ("downstream", "Per-group variant statistics (VAF bins, counts, tests)."),
    ],
    "phylo_phykit": [
        ("inputs", "Proteomes and BUSCO single-copy orthologs."),
        ("transform", "Align and trim each ortholog; infer single-gene trees."),
        ("result_table", "Per-gene PhyKIT metric table for animals and fungi."),
        ("downstream", "Summary statistics / Mann-Whitney U test animals vs fungi."),
    ],
    "tabular_stats": [
        ("inputs", "Load and merge the tables."),
        ("cohort", "Define the analysed subset and recode variables."),
        ("model", "Fit the model / run the test as specified."),
        ("result_table", "Model coefficients or test statistics."),
    ],
}


def draft_graph(sid: str, rows: list[dict]) -> dict:
    vis = answer_visibility()
    archetype = vis["archetypes"].get(sid, "tabular_stats")
    nodes, prev = [], None
    for i, (stage, desc) in enumerate(LADDERS[archetype], start=1):
        nid = f"s{i}_{stage}"
        nodes.append({"id": nid, "stage_type": stage, "description": f"TODO: {desc}", "depends_on": [prev] if prev else [],
                      "quantities": [], "provenance": {"source": "printed", "ref": "TODO"}})
        prev = nid
    tasks = []
    for row in rows:
        q = row["question_id"].split("-")[-1]
        answer_id = f"a_{q}"
        spec = ideal_spec(row)
        tasks.append({"id": row["question_id"], "answer_node": answer_id, "question": row["question"], "ideal": copy.deepcopy(spec),
                      "label_status": "mismatch" if vis["questions"].get(row["question_id"], "").endswith("!") else "unverified"})
        nodes.append({"id": answer_id, "stage_type": "answer", "description": f"TODO: answer to {row['question_id']}",
                      "depends_on": [prev] if prev else [],
                      "quantities": [{"name": "answer", "expected": copy.deepcopy(spec)}],
                      "provenance": {"source": "printed", "ref": f"visibility={vis['questions'].get(row['question_id'])}"}})
    return {
        "schema": "gce.checkpoint_graph.v1",
        "group_id": f"bixbench-v1.5/{sid}",
        "title": f"BixBench v1.5 {sid}",
        "source": {"benchmark": "BixBench", "version": "1.5", "capsule_uuid": rows[0]["capsule_uuid"], "archetype": archetype},
        "tasks": tasks,
        "nodes": nodes,
    }


def draft_yaml(sid: str, rows: list[dict]) -> str:
    return yaml.safe_dump(draft_graph(sid, rows), sort_keys=False, allow_unicode=True, width=120)

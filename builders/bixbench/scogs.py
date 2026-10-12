"""PhyKIT statistics on the harness's per-lineage ortholog zips (scogs_animals.zip, scogs_fungi.zip).

The BixBench v1.5 phylogenomics capsules (bix-25, bix-28, bix-38, bix-45, bix-60) ship these zips in the
harness data but not in the local capsule data; they hold, per BUSCO, the multi-FASTA (.faa), the MAFFT
alignment (.mafft), the ClipKIT-trimmed alignment (.mafft.clipkit) and the IQ-TREE outputs.

    PYTHONPATH=src:. ../envs.nosync/pyenv/bin/python -m builders.bixbench.scogs --zips <dir with the two zips>
    -> evidence/scogs_L5.json
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import load_config
from .derive import _quantile

HERE = Path(__file__).resolve().parent
OUT = HERE / "evidence" / "scogs_L5.json"
LINEAGES = ("animals", "fungi")
GAP = set("-?*Xx")
# bix-60 ships these ten animal alignments and trees
BIX60_TREES = ["1003258", "100698", "1010730", "1014314", "1018517", "1019762", "1038775", "1041560", "1065019", "1079827"]


def _fasta(path: Path) -> list[str]:
    seqs, cur = [], []
    for line in path.read_text().splitlines():
        if line.startswith(">"):
            if cur:
                seqs.append("".join(cur))
            cur = []
        else:
            cur.append(line.strip())
    if cur:
        seqs.append("".join(cur))
    return seqs


def _gaps(path: Path) -> dict:
    seqs = _fasta(path)
    length = len(seqs[0])
    cells = sum(c in GAP for s in seqs for c in s) / (length * len(seqs))
    sites = sum(any(s[i] in GAP for s in seqs) for i in range(length)) / length
    return {"length": length, "gap_cells": cells, "gap_sites": sites}


def _pk(bindir: Path, tool: str, *args: Path) -> list[float]:
    out = subprocess.run([str(bindir / tool), *map(str, args)], capture_output=True, text=True, check=True).stdout
    return [float(x) for x in out.split()]


def extract(zips: Path, work: Path) -> Path:
    for lineage in LINEAGES:
        dest = work / lineage
        if not dest.is_dir():
            dest.mkdir(parents=True)
            with zipfile.ZipFile(zips / f"scogs_{lineage}.zip") as z:
                z.extractall(dest)
    return work


def gene(path: Path) -> str:
    return path.name.split(".")[0]


def compute(root: Path, bindir: Path) -> dict:
    from scipy.stats import mannwhitneyu

    out: dict = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        rcv = {}
        for kind in ("mafft", "mafft.clipkit"):
            for lineage in LINEAGES:
                files = sorted((root / lineage).rglob(f"*.faa.{kind}"))
                values = pool.map(lambda f: _pk(bindir, "pk_rcv", f)[0], files)
                rcv[(kind, lineage)] = dict(zip(map(gene, files), values))
        tor = {}
        for lineage in LINEAGES:
            trees = sorted((root / lineage).rglob("*.treefile"))
            alns = [t.with_name(t.name[: -len(".treefile")]) for t in trees]
            values = pool.map(lambda pair: _pk(bindir, "pk_tor", "-a", pair[0], "-t", pair[1])[0], zip(alns, trees))
            tor[lineage] = {gene(a): {"tor": v, "clipkit": _gaps(a), "mafft": _gaps(a.with_name(a.name[: -len(".clipkit")]))}
                            for a, v in zip(alns, values)}

    for kind in ("mafft", "mafft.clipkit"):
        a, f = rcv[(kind, "animals")], rcv[(kind, "fungi")]
        test = mannwhitneyu(list(a.values()), list(f.values()), alternative="two-sided")
        paired = [f[k] - a[k] for k in sorted(set(a) & set(f))]
        out[f"bix-45 rcv on {kind}"] = {
            "n_animals": len(a), "n_fungi": len(f), "mwu_U": float(test.statistic), "mwu_p": float(test.pvalue),
            "median_fungi": st.median(f.values()), "median_animals": st.median(a.values()),
            "q75_fungi": _quantile(list(f.values()), 0.75), "median_paired_diff": st.median(paired), "n_paired": len(paired)}

    rows = [dict(lineage=lin, gene=g, **v) for lin in LINEAGES for g, v in tor[lin].items()]
    readings = {}
    for aln in ("clipkit", "mafft"):
        for measure in ("gap_cells", "gap_sites"):
            hits = [r for r in rows if r[aln][measure] > 0.7]
            readings[f"{aln} {measure} > 0.7"] = {"n_genes": len(hits), "max_tor": max((r["tor"] for r in hits), default=None)}
    out["bix-25 treeness/rcv"] = {
        "n_animals": len(tor["animals"]), "n_fungi": len(tor["fungi"]),
        "median_fungi": st.median(v["tor"] for v in tor["fungi"].values()),
        "q4 readings (> 70% gaps)": readings, "q4 official": 0.21}

    treeness = {g: _pk(bindir, "pk_treeness", next((root / "animals").rglob(f"{g}at2759.faa.mafft.clipkit.treefile")))[0] for g in BIX60_TREES}
    total = sum(treeness.values())
    out["bix-60 treeness"] = {"per_tree": treeness, "sum": total,
                              "mean_x1000 over 5 trees (first five)": round(1000 * sum(treeness[g] for g in BIX60_TREES[:5]) / 5),
                              "mean_x1000 over all 10 trees": round(1000 * total / 10), "official": 19}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m builders.bixbench.scogs")
    parser.add_argument("--zips", required=True, type=Path, help="directory with scogs_animals.zip and scogs_fungi.zip")
    args = parser.parse_args()
    cfg = load_config()
    root = extract(args.zips, cfg.work / "scogs")
    # PhyKIT console scripts live next to the interpreter (run this module with the re-run environment's python)
    result = compute(root, Path(sys.executable).parent)
    result["source"] = {"zips": [f"scogs_{lin}.zip" for lin in LINEAGES], "note": "harness data of bix-25/28/38/45 (identical md5)"}
    OUT.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "per_tree"} for k, v in result.items() if k != "source"}, indent=1))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

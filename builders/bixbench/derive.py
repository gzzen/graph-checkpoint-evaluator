"""Derive answers the capsule notebooks do not print (computation levels L1-L3).

L1  arithmetic on values printed in the notebook
L2  statistics recomputed from a full intermediate table printed in the notebook
L3  a short segment of the notebook re-executed on the shipped capsule data (pandas/scipy here;
    R capsules go through `rerun` and are read back from evidence/<sid>.json)

Every function follows the notebook's own method and returns the value plus any alternative readings
of the question. The official key is only consulted afterwards, in `compare()`, so the derivation
does not tune itself to the key. Run with the re-run Python environment (pandas, scipy, xlrd, openpyxl):

    <envs>/pyenv/bin/python -m builders.bixbench.derive
"""
from __future__ import annotations

import json
import re
import statistics as st
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from gce.values import answer_matches

from .config import HERE, Config, load_config
from .notebooks import load_cells
from .questions import by_capsule, ideal_spec, load_questions

OUT = HERE / "evidence" / "derived_78_L1_L3.json"


@dataclass
class Derived:
    question_id: str
    level: str
    method: str
    source: str
    value: object
    variants: dict = field(default_factory=dict)
    notes: str = ""


class Ctx:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.caps = by_capsule(load_questions(cfg.questions))
        self._cells: dict[str, list] = {}

    def uuid(self, sid: str) -> str:
        return self.caps[sid][0]["capsule_uuid"]

    def out(self, sid: str, cell: int) -> str:
        if sid not in self._cells:
            self._cells[sid] = load_cells(self.cfg.notebook_dir(self.uuid(sid)))
        return self._cells[sid][cell].text_output

    def data(self, sid: str, name: str) -> Path:
        return self.cfg.data_dir(self.uuid(sid)) / name

    def table(self, sid: str, cell: int) -> dict[tuple[str, str], dict[str, float]]:
        """Printed tab-separated per-gene table: lineage, gene, metric, value."""
        rows: dict[tuple[str, str], dict[str, float]] = {}
        for line in self.out(sid, cell).splitlines():
            parts = line.split("\t")
            if len(parts) == 4:
                try:
                    rows.setdefault((parts[0], parts[2]), {})[parts[1]] = float(parts[3])
                except ValueError:
                    pass
        return rows

    def evidence(self, sid: str) -> dict:
        return json.loads((HERE / "evidence" / f"{sid}.json").read_text(encoding="utf-8"))["checkpoints"]


def _num(pattern: str, text: str) -> float:
    m = re.search(pattern, text)
    if not m:
        raise ValueError(f"pattern not found: {pattern}")
    return float(m.group(1))


def _quantile(values: list[float], q: float) -> float:
    """numpy/pandas default (linear interpolation)."""
    s = sorted(values)
    k = (len(s) - 1) * q
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


# ---- L1: arithmetic on printed values ------------------------------------------------------------

def bix_6_q5(c: Ctx) -> Derived:
    acute = c.out("bix-6", 26)
    chronic = c.out("bix-6", 30)
    rounds = re.split(r"Performing analysis on chronic screen round (\d)", chronic)[1:]
    significant = {"acute": "no pathways are significantly" not in acute}
    for i in range(0, len(rounds), 2):
        significant[f"chronic round {rounds[i]}"] = "significant pathways:" in rounds[i + 1]
    pathways = sorted(set(re.findall(r"ReactomePathways\.gmt\s+(.+?)\s+\d+/\d+", chronic)))
    pct = 100 * sum(significant.values()) / len(significant)
    return Derived("bix-6-q5", "L1", "conditions (acute + chronic rounds 1-3) with >=1 pathway at adjusted p < 0.05, over all 4",
                   "cells 26, 30", round(pct, 1), {"conditions": significant, "significant_pathways": pathways},
                   "Only chronic round 2 has significant pathways (NO/cGMP signalling); whether these count as "
                   "'immune-relevant' is a judgement the notebook does not make.")


def bix_8_q1(c: Ctx) -> Derived:
    hyper = _num(r"m6A Hyper\s+(\d+)", c.out("bix-8", 21))
    hyper_up = _num(r"m6A Hyper and Up\s+(\d+)", c.out("bix-8", 23))
    return Derived("bix-8-q1", "L1", "hyper-methylated & up-regulated / hyper-methylated", "cells 21, 23",
                   round(100 * hyper_up / hyper, 2), {"counts": {"hyper": hyper, "hyper_and_up": hyper_up}})


def bix_8_q3(c: Ctx) -> Derived:
    text = c.out("bix-8", 21)
    hyper, hypo = _num(r"m6A Hyper\s+(\d+)", text), _num(r"m6A Hypo\s+(\d+)", text)
    return Derived("bix-8-q3", "L1", "hyper-methylated / hypo-methylated gene counts", "cell 21",
                   round(hyper / hypo, 3), {"counts": {"hyper": hyper, "hypo": hypo}})


def bix_10_q7(c: Ctx) -> Derived:
    odds_ratio = _num(r"expect_interact_cat\s+expect_interact_cat\s+([\d.]+)", c.out("bix-10", 42))
    return Derived("bix-10-q7", "L1", "(1 - OR[expect_interact]) x 100 from the ordinal model", "cell 42",
                   round(100 * (1 - odds_ratio), 2), {"odds_ratio": odds_ratio})


def bix_30_q1(c: Ctx) -> Derived:
    import pandas as pd  # L3 input: the total number of miRNAs is not printed

    significant = _num(r"Number of unadjusted p-values <= 0.05 : (\d+)", c.out("bix-30", 16))
    data = pd.read_excel(c.data("bix-30", "pone.0150501.s002.xls"), header=1)
    total = len(data)
    return Derived("bix-30-q1", "L1", "miRNAs with unadjusted t-test p <= 0.05 / all miRNAs tested", "cell 16 + row count of the data file",
                   round(100 * significant / total, 2), {"significant": significant, "total_mirnas": total})


def bix_39_q2(c: Ctx) -> Derived:
    block = c.out("bix-39", 19).split("$`BSyn Probands`")[0]
    q1, q3 = _num(r"Max\.\s*\n\s*[\d.]+\s+([\d.]+)", block), _num(r"Max\.\s*\n(?:\s*[\d.]+){4}\s+([\d.]+)", block)
    return Derived("bix-39-q2", "L1", "3rd quartile - 1st quartile of per-sample somatic (VAF < 0.3) counts, BLM carriers (R summary)",
                   "cell 19", q3 - q1, {"quartiles": [q1, q3]})


def bix_52_q2(c: Ctx) -> Derived:
    densities = [float(m) for m in re.findall(r"^\s*\d+\s+\S+\s+\d+\s+\d+\s+([\d.e-]+)\s*$", c.out("bix-52", 39), re.M)]
    ev = c.evidence("bix-52")
    return Derived("bix-52-q2", "L1", "mean over chromosomes of filtered distinct-CpG density (Jackdaw), from the printed table",
                   "cell 39 (20 rows) + evidence/bix-52.json", st.mean(densities),
                   {"rerun_mean_chrom_density": ev["jd_filtered_mean_chrom_density"]["value"],
                    "rerun_pooled_density (total CpGs / total length)": ev["jd_filtered_pooled_density"]["value"],
                    "n_chromosomes": len(densities)})


# ---- L2: statistics from printed per-gene tables -------------------------------------------------

def bix_25_q1(c: Ctx) -> Derived:
    fungi = list(c.table("bix-25", 37)[("fungi", "treeness_over_rcv")].values())
    return Derived("bix-25-q1", "L2", "median treeness/RCV over fungal genes", "cell 37", round(st.median(fungi), 4), {"n_genes": len(fungi)})


def _lbs(c: Ctx) -> dict:
    return c.table("bix-28", 37)


def bix_28_q2(c: Ctx) -> Derived:
    vals = list(_lbs(c)[("fungi", "LBS_mean")].values())
    return Derived("bix-28-q2", "L2", "median over fungal genes of per-gene mean LBS", "cell 37", st.median(vals), {"n_genes": len(vals)})


def bix_28_q5(c: Ctx) -> Derived:
    vals = list(_lbs(c)[("fungi", "LBS_median")].values())
    return Derived("bix-28-q5", "L2", "mean over fungal genes of per-gene median LBS", "cell 37", round(st.mean(vals), 4))


def bix_28_q6(c: Ctx) -> Derived:
    t = _lbs(c)
    a, f = st.mean(t[("animals", "LBS_mean")].values()), st.mean(t[("fungi", "LBS_mean")].values())
    return Derived("bix-28-q6", "L2", "|mean(animal per-gene mean LBS) - mean(fungal per-gene mean LBS)|", "cell 37",
                   round(abs(a - f), 4), {"animals": a, "fungi": f})


def _tree_len(c: Ctx) -> tuple[dict, dict]:
    t = c.table("bix-38", 38)
    return t[("animals", "tree_len")], t[("fungi", "tree_len")]


def bix_38_q1(c: Ctx) -> Derived:
    a, f = _tree_len(c)
    ratio = st.median(f.values()) / st.median(a.values())
    return Derived("bix-38-q1", "L2", "median fungal tree length / median animal tree length", "cell 38", round(ratio, 3))


def bix_38_q2(c: Ctx) -> Derived:
    a, _ = _tree_len(c)
    return Derived("bix-38-q2", "L2", "sample variance of animal tree lengths", "cell 38", round(st.variance(a.values()), 4),
                   {"population_variance": round(st.pvariance(a.values()), 4)})


def bix_38_q3(c: Ctx) -> Derived:
    _, f = _tree_len(c)
    return Derived("bix-38-q3", "L2", "% of fungal orthologs with tree length > 5.0", "cell 38",
                   round(100 * sum(v > 5.0 for v in f.values()) / len(f), 2))


def bix_38_q5(c: Ctx) -> Derived:
    a, f = _tree_len(c)
    mean_a = st.mean(a.values())
    return Derived("bix-38-q5", "L2", "% of fungal orthologs longer than the mean animal tree length", "cell 38",
                   round(100 * sum(v > mean_a for v in f.values()) / len(f), 2), {"mean_animal": mean_a})


def bix_38_q6(c: Ctx) -> Derived:
    a, f = _tree_len(c)
    paired = [f[k] / a[k] for k in sorted(set(a) & set(f)) if a[k] > 0]
    return Derived("bix-38-q6", "L2", "median over orthologs present in both lineages of fungal/animal tree length (as the question asks)",
                   "cell 38", round(st.median(paired), 3),
                   {"ratio_of_medians (median fungal / median animal)": round(st.median(f.values()) / st.median(a.values()), 3),
                    "n_paired": len(paired)})


def _rcv(c: Ctx) -> tuple[dict, dict]:
    t = c.table("bix-45", 30)
    return t[("animals", "RCV")], t[("fungi", "RCV")]


def bix_45_q1(c: Ctx) -> Derived:
    from scipy.stats import mannwhitneyu

    a, f = _rcv(c)
    res = mannwhitneyu(list(a.values()), list(f.values()), alternative="two-sided")  # as notebook cell 34
    return Derived("bix-45-q1", "L2", "two-sided Mann-Whitney U, animal vs fungal RCV (scipy, as cell 34)", "cell 30 table, cell 34 code",
                   float(res.pvalue), {"U": float(res.statistic)})


def bix_45_q2(c: Ctx) -> Derived:
    _, f = _rcv(c)
    return Derived("bix-45-q2", "L2", "median fungal RCV", "cell 30", round(st.median(f.values()), 4))


def bix_45_q5(c: Ctx) -> Derived:
    a, f = _rcv(c)
    diffs = [f[k] - a[k] for k in sorted(set(a) & set(f))]
    return Derived("bix-45-q5", "L2", "median over orthologs in both lineages of (fungal - animal) RCV", "cell 30",
                   round(st.median(diffs), 4), {"n_paired": len(diffs),
                                                "difference_of_medians": round(st.median(f.values()) - st.median(a.values()), 4)})


def bix_45_q6(c: Ctx) -> Derived:
    _, f = _rcv(c)
    return Derived("bix-45-q6", "L2", "75th percentile of fungal RCV (linear interpolation)", "cell 30", round(_quantile(list(f.values()), 0.75), 4))


# ---- L3: short re-executions on the shipped data -------------------------------------------------

def bix_6_q6(c: Ctx) -> Derived:
    import pandas as pd
    from scipy.stats import anderson, spearmanr

    scores = pd.read_excel(c.data("bix-6", "JuliaJong_CRISPRa_BCL2_B3GNT_cacerResTcellCytotoxicity_supplData1_mageck.xlsx"),
                           sheet_name="MAGeCK P-values")
    s1, s2 = scores["Acute Tcells S1"], scores["Acute Tcells S2"]
    normal = all(anderson(x, dist="norm").statistic <= anderson(x, dist="norm").critical_values[2] for x in (s1, s2))
    res = spearmanr(s1, s2)
    return Derived("bix-6-q6", "L3", "Spearman correlation of acute T-cell screen MAGeCK p-values, S1 vs S2 (as cell 19)",
                   "MAGeCK P-values sheet", float(res.statistic),
                   {"p_value": float(res.pvalue), "anderson_darling_normal": normal, "n_genes": int(len(scores))},
                   "The saved notebook has no output for this cell (NameError in the saved session).")


def bix_22_q6(c: Ctx) -> Derived:
    import numpy as np
    import pandas as pd
    from scipy.stats import pearsonr, spearmanr

    counts = pd.read_csv(c.data("bix-22", "BatchCorrectedReadCounts_Zenodo.csv"), index_col=0)
    meta = pd.read_csv(c.data("bix-22", "Sample_annotated_Zenodo.csv"), index_col=0)
    genes = pd.read_csv(c.data("bix-22", "GeneMetaInfo_Zenodo.csv"), index_col=0)
    prot = genes[genes.gene_biotype.str.contains("protein_coding")]
    immune_samples = meta[~meta.celltype.isin(["PBMC"])].index
    # notebook cells 19-22: immune (non-PBMC) samples, protein-coding genes, total count >= 10
    x = counts.loc[counts.index.isin(prot.index), counts.columns.isin(immune_samples)]
    x = x[x.sum(axis=1) >= 10]
    length = prot.loc[x.index, "Length"]

    def r(expr, lens, method=pearsonr):
        return float(method(lens, expr)[0])

    variants = {}
    for ct in ["CD4", "CD8", "CD14", "CD19"]:  # notebook reference values (anchors)
        cols = [s for s in x.columns if meta.loc[s, "celltype"] == ct]
        variants[f"notebook_per_celltype_{ct}"] = round(r(x[cols].mean(axis=1), length), 6)
    pooled = x.mean(axis=1)
    all_samples = counts.loc[x.index].mean(axis=1)
    variants["pooled_immune_raw_pearson"] = round(r(pooled, length), 4)
    variants["pooled_all_samples_raw_pearson"] = round(r(all_samples, length), 4)
    variants["pooled_immune_spearman"] = round(r(pooled, length, spearmanr), 4)
    variants["pooled_immune_log10len_vs_log10expr+1"] = round(r(np.log10(pooled + 1), np.log10(length)), 4)
    variants["pooled_immune_len_vs_log10expr+1"] = round(r(np.log10(pooled + 1), length), 4)
    variants["pooled_immune_log10len_vs_expr"] = round(r(pooled, np.log10(length)), 4)
    for ct in ["CD4", "CD8", "CD14", "CD19"]:
        cols = [s for s in x.columns if meta.loc[s, "celltype"] == ct]
        variants[f"log_log_{ct}"] = round(r(np.log10(x[cols].mean(axis=1) + 1), np.log10(length)), 4)
    return Derived("bix-22-q6", "L3", "Pearson(gene length, mean expression) over protein-coding genes, all immune samples pooled "
                   "(the notebook only computes it per cell type)", "data; notebook cells 19-30",
                   variants["pooled_immune_raw_pearson"], variants,
                   f"{len(x)} protein-coding genes after the notebook's filter.")


def bix_33_q1(c: Ctx) -> Derived:
    import pandas as pd

    path = c.data("bix-33", "Supplemental Data File 1 PostvsPre_DEG_tables_ANNOTATED by_cell_type.xlsx")
    sheets = pd.read_excel(path, sheet_name=None)
    notebook_tabs = ["Treg", "CD14 Mono", "CD16 Mono", "cDC2", "pDC", "gdT", "ILC"]  # cell 13
    counts = {name: int((df["p_val_adj"] < 0.05).sum()) for name, df in sheets.items() if "p_val_adj" in df.columns}
    in_notebook = {k: counts[k] for k in notebook_tabs if k in counts}
    top = max(in_notebook, key=in_notebook.get)
    return Derived("bix-33-q1", "L3", "cell type with most DEGs at p_val_adj < 0.05 among the notebook's 7 tabs (cell 21 chart)",
                   "supplementary DEG workbook", top,
                   {"counts_notebook_tabs": in_notebook, "top_all_tabs": max(counts, key=counts.get), "counts_all_tabs": counts})


def bix_52_q1(c: Ctx) -> Derived:
    ev = c.evidence("bix-52")
    return Derived("bix-52-q1", "L3", "Zebra Finch filtered distinct-CpG density, chromosome W / chromosome 1 (R re-run)",
                   "evidence/bix-52.json", ev["zf_filtered_W_over_1"]["value"],
                   {"chr1_over_W": ev["chr1_over_W_zf_filtered"]["value"],
                    "jackdaw_W_over_1": ev["jd_filtered_W_over_1"]["value"],
                    "zf_unfiltered_W_over_1": ev["zf_all_W_over_1"]["value"]},
                   "The shipped Zebra Finch chromosome lengths are the Jackdaw lengths (notebook cell 14 bug).")


def bix_52_q7(c: Ctx) -> Derived:
    ev = c.evidence("bix-52")
    return Derived("bix-52-q7", "L3", "Zebra Finch rows removed by the >90% / <10% methylation filter (R re-run)",
                   "evidence/bix-52.json", ev["zf_rows_removed"]["value"],
                   {"distinct_sites_removed": ev["zf_distinct_removed"]["value"],
                    "rows_all": ev["zf_rows_all"]["value"], "rows_kept": ev["zf_rows_filtered"]["value"]})


DERIVATIONS: list[Callable[[Ctx], Derived]] = [
    bix_6_q5, bix_8_q1, bix_8_q3, bix_10_q7, bix_30_q1, bix_39_q2, bix_52_q2,
    bix_25_q1, bix_28_q2, bix_28_q5, bix_28_q6, bix_38_q1, bix_38_q2, bix_38_q3, bix_38_q5, bix_38_q6,
    bix_45_q1, bix_45_q2, bix_45_q5, bix_45_q6,
    bix_6_q6, bix_22_q6, bix_33_q1, bix_52_q1, bix_52_q7,
]


# Equivalences a string matcher cannot see; each is a human judgement and stated as such.
JUDGED = {
    "bix-38-q1": "ratio 2.178 = 'about 2x larger in fungi' (prose ideal graded by an LLM verifier)",
    "bix-33-q1": "'CD14 Mono' is the workbook's tab name for CD14 monocytes",
}


def compare(results: list[Derived], cfg: Config) -> list[dict]:
    """Attach the official ideal and how the derived value relates to it.

    match_kind: exact (derived value matches) | variant (only an alternative reading matches) |
    judged (equivalent by a stated human judgement) | none.
    """
    key = {r["question_id"]: r for r in load_questions(cfg.questions)}
    rows = []
    for d in results:
        row = asdict(d)
        spec = ideal_spec(key[d.question_id])
        row["ideal"], row["ideal_spec"] = key[d.question_id]["ideal"], spec
        flat = {k: v for k, v in d.variants.items() if isinstance(v, (int, float, str)) and not isinstance(v, bool)}
        row["matching_variants"] = [k for k, v in flat.items() if answer_matches(spec, v)]
        if answer_matches(spec, d.value):
            row["match_kind"] = "exact"
        elif row["matching_variants"]:
            row["match_kind"] = "variant"
        elif d.question_id in JUDGED:
            row["match_kind"], row["judgement"] = "judged", JUDGED[d.question_id]
        else:
            row["match_kind"] = "none"
        rows.append(row)
    return rows


def main() -> None:
    cfg = load_config()
    ctx = Ctx(cfg)
    results = [fn(ctx) for fn in DERIVATIONS]
    rows = compare(results, cfg)
    OUT.write_text(json.dumps(rows, indent=1, default=str) + "\n", encoding="utf-8")
    for r in rows:
        mark = {"exact": "MATCH", "judged": "MATCH (judged)", "none": "NO MATCH"}.get(r["match_kind"], "via " + ", ".join(r["matching_variants"]))
        value = r["value"] if isinstance(r["value"], str) else f"{r['value']:.6g}"
        print(f"{r['question_id']:11s} {r['level']}  derived={value:>14s}  ideal={r['ideal']!r:22s} {mark}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

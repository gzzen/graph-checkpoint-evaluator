# Calibration on native_vs_b runs (2026-10-08)

The first end-to-end use of `gce` on real agent runs: the OpenCode/DeepSeek native_vs_b experiment as
exported to `dasc/1004_run_analysis/runs_out` (read-only; its checksum was identical before and after,
and the `native_b_78_standalone_20260921` project was not modified).

## Adapter (written by following `adapters/ADAPTER_PROMPT.md`)

`adapters/contrib/opencode_native_vs_b.py`:
- `gce adapter-check` passes (12 sampled units); `gce adapt` wrote 278 traces with **0 invalid**.
- One unit (`b/seed02/bix-28__…`) has no manifest and no events, so its task ids cannot be recovered;
  the adapter skips it instead of guessing.
- The run root mixes both experiment modes:
  - native: 234 single-question units (3 seeds × 78);
  - b: 44 capsule units, where one context answers all of a capsule's questions.

  Both map onto the same trace schema without special cases.
- Final answers:
  - 103 units end in a JSON final answer;
  - 3 end in a ```json fenced block;
  - 172 have no parseable final answer (the run stopped first). Their tasks are `missing`, and the
    last text is kept in `extras.final_text_head`.

## Scope scored

Pilot graphs that appear in these runs: **bix-10** and **bix-28**. That gives 44 run-unit evaluations
and 72 task results. bix-7 and bix-13 are not in the 78-question subset.

## Results

| condition | tasks | final answer given | correct | mean depth fraction |
|---|---|---|---|---|
| b (capsule units) | 33 | 0 | 0 | **0.86** |
| native (single question) | 39 | 7 | 4 | 0.66 (0.93 for answered tasks, 0.60 for unanswered) |

What the checkpoints add over accuracy alone:

- **The b condition did the analysis but never reported it.** Every b unit for these capsules failed
  (provider HTTP 400 or a policy interrupt) before writing its final JSON. Yet the b agents passed 86%
  of the checkpoints. For bix-10 they reproduced the reference values: 791 analysed subjects,
  χ² = 9.3151 (p = 0.0254) and χ² = 9.4207 (p = 0.0242) in the subgroups, OR 1.5242 and 0.755. A
  0/33 accuracy hides analyses that were correct up to the reporting step.
- **Native bix-10 runs diverge at the cohort definition.** The agents restricted severity to COVID-19
  adverse events (≈140–194 subjects) instead of the subject-level maximum over all adverse events (791).
  - First failures land on the subgroup χ² checkpoints (χ² 3.7–7.0 against 9.3/9.4) and on
    `co_complete` ("No. Observations: 194").
  - One native q1 agent *did* compute the reference specification (n = 791, OR 1.528) as a
    sensitivity analysis, but reported the COVID-only estimate (1.17). Its first failure is the answer
    node, with the note "expected value appears in the run, but the final answer differs": an
    interpretation choice, not a computation error.
- **Native bix-28 runs stop between the per-gene table and the test.** Most units computed the per-gene
  long-branch scores (249 fungal trees) but never ran a Mann-Whitney test, so the first gap is `st_mwu`.
  The completed units answered q3 (−30.4551) and q5 (−26.906) correctly.

## Evaluator defects found by calibration (all fixed, with regression tests)

1. **File reads are inputs.** A question file's `"version": "1.5"` passed an odds-ratio checkpoint with
   range 1.50–1.54. Raw evidence now comes only from executed commands, tool calls and messages.
2. **Precision.** A value printed with fewer decimals than a range's bounds ("1.5" for 1.50–1.54) now
   needs context.
3. **Listings.** Numbers next to file names (archive listings, `ls -l`, a file size after
   `…996662at2759.hmm`) are ignored.
4. **Inspection commands.** `pip list | grep -iE "iqtree|mafft|busco"` no longer evidences that
   alignment or tree inference was performed.
5. **Generic context regexes.** My first graphs used contexts such as `p`, `F`, `U`, which match inside
   ordinary words; that let q6's p-value pass as q4's. `gce validate` now rejects such patterns.
6. **Fail detection.** A wrong value used to be read as "the first number after a context word", which
   produced "25" from "25th percentile". It is now read only from a number explicitly labelled with one
   of the quantity's aliases (`No. Observations: 194`, `U = 20261`).
7. **Values shared between checkpoints.** A value that is also another checkpoint's reference (DM rows
   1000 = subjects 1000; the overlapping p-value ranges of q4 and q6) requires context automatically.

## Known limits

- **No stage logs in these runs.** Many checkpoints can only be `absent` or `implied`; adding the
  stage-log instruction would turn many of them into checkable values.
- **Data versions differ.** The harness's bix-28 data ships precomputed BUSCO results, alignments and
  trees (`scogs_*.zip`). The local capsule data does not. The tree-building checkpoints are therefore
  non-gating.
- **Low-confidence fails come from raw-text heuristics.** They are flagged `confidence: low` in the
  evaluation and should be spot-checked.
- **Coverage.** Since 2026-10-12 all 78 questions of the subset have graphs (see below); the rest of
  BixBench v1.5 is still to be authored with `builders/bixbench`.

## Full 78-question coverage (2026-10-12)

Graphs now exist for all 19 capsules of the subset (21 graphs in `checkpoints/bixbench-v1.5/`, 78 of 78
questions), so all 278 traces are scored (416 task results).

| condition | units | tasks | final answer given | correct | mean depth fraction |
|---|---|---|---|---|---|
| b (capsule units) | 44 | 182 | 61 | 33 | 0.72 |
| native (single question) | 234 | 234 | 91 | 57 | 0.67 |

Calibration checks:
- **Every answer node accepts its official ideal** (checked by matching each task's ideal against its
  answer node).
- **Correct answers with a failing gating checkpoint: 2 of 90.** Both are native bix-54-q1 runs that fit
  the 36 ΔrhlI:ΔlasI mixtures only; the reference also keeps the 3 pure ΔrhlI colonies (39). The official
  range for q1 (0.88-1.0) is wide enough to absorb the different cohort, so the failure is real.
- **Wrong answers with a located failure: 62 of 62.** 58 are located at the answer node and 4 upstream
  (2 cohort, 2 filter). Without stage logs, most upstream checkpoints of a wrong run stay `absent`.
- **Unanswered tasks at full depth: 55.** These runs computed the reference values but stopped before
  writing a final answer ("Pass (computed, not reported)").
- **Deterministic:** re-scoring gives identical evaluation files. The bix-10 and bix-28 evaluations are
  unchanged by the new graphs and the stricter specificity rule.

Probe precision found while calibrating the new graphs:
- Values given to two significant digits (0.21, 0.12, 2.5) and whole-number percentages (5%, 28%) were
  matched by unrelated text: IQ-TREE timings (`0.207704 secs`), version strings (`cycler 0.12.1`),
  `5-taxon`, raw Ct values, percentile headers. `is_low_specificity` now requires a label for numbers
  whose tolerance is at least 1% of the value and for percentages at whole-percent precision or coarser.
- Context patterns must be anchored words: `rho` matched `RHOB`, `max` matched `VECLIB_MAXIMUM`, and a
  bare `%` labels every later number on a line.
- Values in [0, 1] (R², frequencies) also occur in data previews (bix-54's circularity column), so those
  quantities carry `specificity: low`.
- Remaining known weakness: on a line that reports several labelled values ("specific to DG vs
  baseline: 686 specific to DG vs final: 734"), an earlier label also labels later values. Such passes
  are `confidence: low`.

Notable reference findings while authoring:
- **bix-45:** the notebook computes RCV on the untrimmed MAFFT alignments. On the ClipKIT-trimmed
  alignments that the harness also ships, all four answers miss (p 1.09e-42, fungal median 0.197); the
  alignment choice is a hidden gating checkpoint with the trimmed route as a variant.
- **bix-25-q4** (0.21) is not reproduced by any tested reading of "> 70% alignment gaps"; it stays
  `unverified`. The notebook's cell 37 also mislabels genes by one row (99 animal rows instead of 100).
- **bix-60:** the official 19 needs a five-tree denominator; the harness ships ten trees (mean x 1000 =
  10).
- **bix-52-q1/q3/q5** match the Jackdaw values or the inverted ratio, not the Zebra Finch values the
  questions name; **bix-22-q6** needs a rank or log-log correlation; **bix-38-q6** is the ratio of
  medians, not the median of paired ratios. Each has a variant node with the literal reading.
- Evidence for the phylogenomics capsules: `builders/bixbench/scogs.py` -> `evidence/scogs_L5.json`.

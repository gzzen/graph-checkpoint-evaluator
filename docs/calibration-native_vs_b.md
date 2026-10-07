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
- **Pilot only.** Four capsules have graphs (two appear in these runs); the rest of BixBench v1.5 is
  still to be authored with `builders/bixbench`.

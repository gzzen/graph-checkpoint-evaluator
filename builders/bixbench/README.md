# BixBench reference builder

Tooling to build `checkpoints/bixbench-v1.5/<sid>.yaml` graphs from the BixBench capsule notebooks
and data. It is BixBench-specific and kept outside the evaluator core (`src/gce`).

Paths come from `config.toml`, with overrides in a git-ignored `config.local.toml`. Run commands from
the repository root:

```bash
python -m builders.bixbench questions bix-13      # questions, ideal ValueSpecs, visibility class
python -m builders.bixbench outline bix-13        # compact notebook outline (code, outputs, figures, errors)
python -m builders.bixbench figures bix-13 24     # save a cell's figures (answers that only exist in plots)
python -m builders.bixbench draft bix-13          # graph skeleton: tasks + archetype ladder + answer nodes
python -m builders.bixbench rerun bix-13          # run reruns/bix-13.yaml; writes evidence/bix-13.json
```

## Authoring workflow

1. **Inventory.** Run `questions` and `outline`. `data/answer_visibility.json` records, for each
   question, where its ideal answer can be found in the notebook:

   | code | meaning |
   |---|---|
   | `E` | printed |
   | `D` | derivable from a printed table |
   | `P` | plot only |
   | `N` | absent |
   | `X` | no notebook |
   | `!` (suffix) | the label contradicts the notebook |

2. **Draft.** Run `draft` and replace each TODO with a semantic description, the quantities with
   their expected values, `aliases`/`context` for the probes, and `provenance` (`printed` with the
   cell reference, `derived`, or `rerun`). Mark `hidden: true` for notebook decisions the question
   does not state.
   Give intermediate checkpoints the reference value with a numerical tolerance (e.g. ±0.005), and
   keep the official answer ranges for answer nodes only. A wide official range can be met by
   chance by an analysis on a different cohort, and that false pass would then propagate upstream as
   `implied`.
3. **Re-run** whatever is not printed. Write `reruns/<sid>.yaml`:
   - instrumentation calls `gce_checkpoint(name, value, cell)`;
   - **anchors** are values the notebook did print, so drift between the notebook's environment and
     ours shows up immediately.

   `rerun` writes `evidence/<sid>.json` with the checkpoint values, anchor results, applied patches
   and package versions.
4. **Label status.** Set `tasks[].label_status`: `consistent` when the reference graph reproduces the
   official ideal, `mismatch` when it does not. If the question text implies a parameter the notebook
   did not use, add a variant node, and an `any_of` ideal when both readings are defensible.
5. `gce validate checkpoints/bixbench-v1.5/<sid>.yaml`.

## Answers the notebooks do not print (L1-L3)

`derive.py` computes the missing answers that need at most a short re-execution:
- **L1:** arithmetic on printed values;
- **L2:** statistics from printed per-gene tables;
- **L3:** short re-runs on the capsule data. R capsules go through `rerun`.

Each derivation follows the notebook's own method and records alternative readings of the
question. Only `compare()` looks at the official key, and it records how each value relates to it:
`exact`, `variant`, `judged` (a stated human judgement) or `none`.

```bash
PYTHONPATH=src:. ../envs.nosync/pyenv/bin/python -m builders.bixbench.derive   # -> evidence/derived_78_L1_L3.json
```

## Environments

`envs/setup.sh <dir>` creates a Python 3.11 venv pinned close to the notebooks' Colab versions, and an
R library. The notebooks ran R 4.4.2 / Bioconductor 3.20; a local R 4.3 gets Bioconductor 3.18.
Anchors and the recorded versions expose any resulting drift.

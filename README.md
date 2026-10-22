# graph-checkpoint-evaluator (`gce`)

Score LLM-agent runs on **how far an analysis got and where it first went wrong**, not only on the
final answer.

A *checkpoint graph* describes the reference analysis of a group of related tasks (for example one
BixBench capsule and its questions). Each task is a path through the graph: inputs, cohort definition,
filtering, model, primary result, downstream steps, answer. An agent run is normalized into a *trace*,
and the evaluator decides for every checkpoint on the path whether the run passed it, got it wrong, or
never produced it.

## Requirements this project is built around

1. **Independent of run-directory layouts.** The evaluator only reads normalized traces (`trace.v1`).
   Every experiment layout gets a small *adapter*. `adapters/ADAPTER_PROMPT.md` is a prompt that lets
   an LLM coding tool write that adapter, and `gce adapter-check` proves that the adapter works.
2. **General intermediate-output schema.** `stage_log_entry.v1` is a method-neutral record of each
   analysis step and the key quantities it produced. Future experiments can emit it directly, or emit a
   whole trace in the `canonical_dir` layout and skip adapters altogether.
3. **Mode-agnostic scoring.** A *run unit* is one agent context covering one or more tasks.
   Single-question and whole-capsule experiments are both just run units. Group-level metrics apply
   automatically when a unit covers several tasks that share checkpoints: root-cause attribution,
   consistency, parameter bleed and reuse.
4. **Experiments stay untouched.** Existing harnesses are read, never modified. They adopt the
   schemas when ready.

## Concepts

| Term | Meaning |
|---|---|
| task | one question with an ideal answer |
| task group | tasks that share a reference analysis; one checkpoint graph per group |
| node | a checkpoint: a semantic, tool-agnostic description of a step plus an expected value |
| run unit | one agent context; covers 1..n tasks of a group |
| trace | normalized record of a run unit: raw steps, stage log, final answers |
| adapter | code that converts one run-directory layout into traces |

Schemas live in [`schemas/`](schemas/) and are documented in [`docs/schemas.md`](docs/schemas.md).

## Layout

```
schemas/             JSON Schemas (checkpoint_graph, stage_log_entry, trace, evaluation)
src/gce/             evaluator core: values, graph, trace, probes, scoring, report, cli
src/gce/adapters/    adapter protocol and the built-in canonical_dir adapter
adapters/            ADAPTER_PROMPT.md, TEMPLATE.py and contributed adapters
builders/bixbench/   tooling that builds BixBench reference graphs from capsule notebooks
checkpoints/         reference checkpoint graphs
envs/                environment specifications for notebook re-runs
tests/               unit and golden tests
```

## Quick start

```bash
uv venv -p 3.12 .venv && uv pip install -e '.[dev]'
.venv/bin/gce validate checkpoints/bixbench-v1.5/*.yaml
.venv/bin/gce adapt --adapter canonical_dir --run path/to/run --out out/traces
.venv/bin/gce score --graphs checkpoints/bixbench-v1.5 --traces out/traces --out out/eval
.venv/bin/gce report --evals out/eval --graphs checkpoints/bixbench-v1.5 --out out/report   # summary.md, tasks.csv, nodes.csv
```

`gce adapt` and `gce adapter-check` take the path of one exact run (`--run`; `--run-root` is the
old name). Runs of the native_b controller have one adapter per run type:

```bash
PACK=../opencode_deepseek_20261012T065851Z_2938483_2
.venv/bin/gce adapt --adapter adapters/contrib/native.py \
    --run $PACK/runs/opencode_deepseek_20261012T065851Z_2938483_native_rep01 --out out/traces/native_rep01
.venv/bin/gce adapt --adapter adapters/contrib/b.py \
    --run $PACK/runs/opencode_deepseek_20261012T065851Z_2938483_b_seed01 --out out/traces/b_seed01
```

`native.py` makes one trace per question; `b.py` one trace per capsule covering all of its questions. Each
adapter reads the pack's `analysis/<run>_generation.json` (and resume plans) next to the run; giving it a
run of the other type, or a pack instead of a run, fails with a message saying so.

Condition-c runs (`<parent>_c_minimal`, `_c_single`, `_c_blind`) are question-local repairs of a frozen b run, so
`c.py` reads the c run and its b parent next to it: one trace per finished repair, plus the b capsule trace for the
questions without one. `--option answers=proposed` scores the repair agent's own proposal instead of the
controller's output (which falls back to b when a repair fails validation), as condition `<level>_proposed`:

```bash
PACK=../20261022T060324Z
.venv/bin/gce adapt --adapter adapters/contrib/c.py --run $PACK/runs/20261022T060324Z_b_c_minimal --out out/traces/c_minimal
```

`gce report` also writes `paths/<condition>/<group>.md`, one file per running condition and task group
(e.g. `paths/native/bix-10.md` and `paths/b/bix-10.md`; conditions are never pooled), which
`summary.md` links to. Each file opens with the full checkpoint graph of the group (all checkpoints and
edges, coloured by that attempt's outcomes, plus a table of every checkpoint and the questions that
use it), after a short guide to the checkpoint-ID prefixes (`in_` inputs, `co_` cohort, `r_` result,
`a_qN` answer to question N, ...). Then, for every question, it shows a table of what each checkpoint on
its path represents for that question (and which other questions share it), followed by:
- a Mermaid diagram of the checkpoint path, each checkpoint coloured by the outcomes the runs reached
  there;
- a table with one column per run, giving the outcome and the produced value at every checkpoint, the
  number of gating checkpoints passed, where the run stopped, and its final answer.

Outcomes are written out in words and defined at the top of each file. Pass `--graphs` to add stages,
expected values, hidden and gating flags, and the graph's edges.

`gce score --judge deepseek` enables the optional LLM judge for semantic checkpoints, the nodes
without measurable quantities that no code pattern matched. It calls the DeepSeek chat-completions API
using only the standard library. Configure it with a `.env` file:

```bash
cp .env.example .env && chmod 600 .env   # then set DEEPSEEK_API_KEY
.venv/bin/gce score --judge deepseek --graphs checkpoints/bixbench-v1.5 --traces out/traces --out out/eval
```

Exported variables take precedence over `.env`. `--env-file` points at another file, and
`deepseek:<model>` overrides `DEEPSEEK_MODEL`. Set `GCE_JUDGE_CACHE` to a directory to cache verdicts
and make re-scoring free and deterministic.

When the checkout lives in an iCloud-synced folder (e.g. `~/Documents`), sync can mark the
editable-install `.pth` file hidden, and Python 3.12+ then skips it (`import gce` fails). Keep the
environment out of sync instead:

```bash
uv venv -p 3.12 .venv.nosync && ln -s .venv.nosync .venv && uv pip install -p .venv/bin/python -e '.[dev]'
```

The tests do not depend on the editable install.

## Status

Version 0.1, under active development. See `CHANGELOG.md`.

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
.venv/bin/gce adapt --adapter canonical_dir --run-root path/to/runs --out out/traces
.venv/bin/gce score --graphs checkpoints/bixbench-v1.5 --traces out/traces --out out/eval
.venv/bin/gce report --evals out/eval --graphs checkpoints/bixbench-v1.5 --out out/report   # summary.md, tasks.csv, nodes.csv
```

`gce report` also writes `paths/<condition>/<group>.md`, one file per running condition and task group
(e.g. `paths/native/bix-10.md` and `paths/b/bix-10.md`; conditions are never pooled), which
`summary.md` links to. For every question the file shows:
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

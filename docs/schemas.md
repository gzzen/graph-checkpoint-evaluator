# gce schemas (v1)

All schemas are JSON Schema 2020-12 files in [`schemas/`](../schemas/). Tagged documents carry a
top-level `"schema"` field so `gce validate` can detect their kind.

| Kind | Tag | File | Produced by |
|---|---|---|---|
| ValueSpec | none (embedded) | `value_spec.v1.schema.json` | graph authors |
| Checkpoint graph | `gce.checkpoint_graph.v1` | `checkpoint_graph.v1.schema.json` | reference builders, one per task group |
| Stage log entry | none (one per JSONL line) | `stage_log_entry.v1.schema.json` | agents or harnesses |
| Trace | `gce.trace.v1` | `trace.v1.schema.json` | adapters, or harnesses natively |
| Evaluation | `gce.evaluation.v1` | `evaluation.v1.schema.json` | `gce score` |

## ValueSpec

An expected value together with how to match it.

| type | fields | matches |
|---|---|---|
| `number` | `value`, `abs_tol` / `rel_tol`, or `decimals` / `sig_figs` | Tolerance is half a unit of the last shown digit when `decimals`/`sig_figs` are given (`0.0002` with `decimals: 4` accepts 0.000204). |
| `integer` | `value`, `tol` | Counts. |
| `range` | `min`, `max` | Closed interval; bounds may be swapped. |
| `percent` | `value` on the 0–100 scale | `10.6%`, `10.6` and `0.106`. |
| `set` | `items` and/or `size`, `mode` | `exact`, `contains`, `jaccard` (with `threshold`), or `size`. |
| `label` | `value`, `synonyms` | Case-, space- and punctuation-insensitive containment. |
| `string` | `value` or `regex` | Exact (normalized) or regex. |
| `boolean` | `value` | |
| `any_of` | `options` | Any option matches. |

## Checkpoint graph (`gce.checkpoint_graph.v1`)

One graph per **task group**: the tasks that share a reference analysis, such as one BixBench capsule.

- `tasks[]`: `id`, `answer_node`, optional `question`, `ideal` (official answer as a ValueSpec), and
  `label_status`. `mismatch` means the official ideal disagrees with the reference analysis.
- `nodes[]`: checkpoints.
  - `stage_type`: one of `inputs`, `cohort`, `filter`, `transform`, `model`, `result_table`,
    `downstream`, `answer`. Other lower_snake strings are allowed for other domains.
  - `description`: semantic and tool-agnostic. Say *what* is computed, never which library computes it.
  - `quantities[]`: the measurable outputs of the step (`name`, `expected`, `aliases`, `context`,
    `specificity`), combined with `require: all|any`. A node without quantities is *semantic*: it is
    evidenced by `probe.patterns` over agent code, or by the LLM judge.
  - `depends_on[]`: DAG edges. **A task's path is the set of ancestors of its answer node.**
  - `gating` (default true): failing it blocks a correct answer. Non-gating nodes are reported but do
    not end the path.
  - `hidden`: a reference decision the task text does not state. Misses are reported separately as
    `hidden_misses`, so an underspecified task is not counted as an agent error.
  - `variant_of`: the same step with different parameters for some task. Used to detect
    *parameter bleed*.
  - `alternatives[]`: equivalent nodes. If one of them passes, this node becomes `n/a`.
  - `provenance.source`: `printed` | `derived` | `rerun` | `authored` | `question`.

## Stage log entry

The general intermediate-output record. It is method-neutral: it records what the agent did, not
what it should have done.

```json
{"step": 3, "action": "Welch two-sample t-test A vs B", "params": {"equal_var": false},
 "outputs": {"t_statistic": 2.5311, "p_value": 0.01306}, "artifacts": ["analysis/test.py"],
 "tasks": ["demo-q1"], "ts": "2026-10-08T10:00:00Z"}
```

`tasks` is `"shared"` for steps common to several tasks of one run unit, and can be omitted when the
unit has a single task. Recommended agent instruction (it does not name any stages):

> As you work, append one JSON line per analysis step you actually perform to `stage_log.jsonl`:
> what you did, the choices you made, and the key quantities it produced (e.g. numbers of
> samples/features retained, test statistics, set sizes). This is a record only; do not change your
> analysis to fit it.

## Trace (`gce.trace.v1`)

Normalized record of one **run unit**: one agent context covering one or more tasks.

- `run`: `run_unit_id` (stable), `experiment`, `condition`, `seed`, `model`, `group_id` (a hint),
  `tasks_covered[]`, `status`.
- `capabilities`: what the trace can corroborate.
  - `raw_steps`: steps carry real tool output.
  - `stage_log`: a log is present.
  - `replayable_code`: code can be re-executed.
- `steps[]`: ordered `exec | tool_call | file_write | file_read | message | other`, each with
  `input`, `output`, `path` and `t`.
- `stage_log[]`: stage log entries.
- `final_answers{task_id}`: `status` (`resolved` | `unresolved` | `missing`), `answer`, `candidates`.
- `extras`: anything layout-specific the adapter could not map. Kept, never interpreted.

## Evaluation (`gce.evaluation.v1`)

- `nodes{id}`: `state`, `observed`, `evidence[]`, `self_reported`, `confidence`, `bleed_from`. The states:

  | state | meaning |
  |---|---|
  | `pass` | the expected value is found |
  | `implied` | not observed, but a downstream node passed |
  | `fail` | the quantity was found with a wrong value |
  | `absent` | not found |
  | `unsupported` | a stage-log value that no raw step output corroborates |
  | `n/a` | an alternative node passed |

- `tasks{id}`:
  - `path`, `depth` (gating nodes passed before the first failure), `depth_fraction`;
  - `first_failure` (first `fail`/`unsupported`) and `first_gap` (first `absent`), each with its stage;
  - `hidden_misses`, `answer_correct`, `answer_correct_vs_ideal`.
- `group_metrics` (run units covering ≥2 tasks):
  - `root_causes`: each failed shared node counted once, with its affected tasks;
  - `consistency`, `parameter_bleed`, `reuse`.

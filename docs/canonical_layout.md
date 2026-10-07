# Canonical run layout (`canonical_dir` adapter)

Experiments that write this layout need no custom adapter: run
`gce adapt --adapter canonical_dir --run-root <root> --out <traces>`.

A run unit is any directory, at any depth under the run root, that contains `trace.json` or
`meta.json`.

```
<run_root>/
  <anything>/<unit>/
    trace.json         # Option A: a complete gce.trace.v1 document, used as-is
    # Option B: parts
    meta.json          # {"run_unit_id", "tasks_covered", "experiment", "condition", "seed", "model", "group_id", "status", ...}
    answers.json       # {"<task_id>": "<answer>"} or {"<task_id>": {"status", "answer", "candidates", "summary"}}
    stage_log.jsonl    # optional; one stage_log_entry.v1 object per line
    steps.jsonl        # optional; one step per line: {"kind", "tool", "input", "output", "path", "t", "tasks"}
    scripts/           # optional; persisted agent code (sets capabilities.replayable_code)
```

Rules:
- `run_unit_id` defaults to the unit's path relative to the run root.
- Any task in `tasks_covered` without an answer becomes `{"status": "missing"}`.
- Unknown keys in `meta.json` are kept under `extras.meta`.
- Capabilities are derived:
  - `raw_steps`: any step has output.
  - `stage_log`: the log is non-empty.
  - `replayable_code`: `scripts/` is non-empty.

## Harness integration (what an experiment needs to do)

1. Give the agent the stage-log instruction from [schemas.md](schemas.md#stage-log-entry), and point
   it at `<unit>/stage_log.jsonl` (or a path you copy there afterwards).
2. After the run, write `meta.json` and `answers.json`, and, if the harness captures tool events,
   `steps.jsonl`.
3. Validate with `gce adapter-check --adapter canonical_dir --run-root <root>`.

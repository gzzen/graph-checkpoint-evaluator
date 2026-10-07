# Prompt: write a gce adapter for a run directory

Copy everything below the line into an LLM coding tool (Claude Code, Codex, OpenCode, ...) that is
running inside a checkout of `graph-checkpoint-evaluator`. Fill in the three placeholders first.

- `{{RUN_ROOT}}`: absolute path of the run directory to support.
- `{{ADAPTER_NAME}}`: snake_case name for the adapter, e.g. `opencode_native_vs_b`.
- `{{NOTES}}`: anything you already know about the layout (experiment design, what a unit is), or "none".

---

You are writing an **adapter** for graph-checkpoint-evaluator (`gce`). The evaluator scores agent runs
against checkpoint graphs, but it only reads normalized **traces** (schema `gce.trace.v1`). Your job
is to convert the run directory at `{{RUN_ROOT}}` into traces, by writing one Python file:
`adapters/contrib/{{ADAPTER_NAME}}.py`.

Known context: {{NOTES}}

## Hard rules
1. **Read-only.** Never create, modify, move or delete anything under `{{RUN_ROOT}}`. Inspect it with
   listing and reading commands only. `gce adapter-check` fails if the run root changes.
2. **Deterministic.** The same input must give byte-identical traces: stable unit order and ids,
   no wall-clock values, no randomness, no dependence on dict ordering of unsorted directory listings.
3. **No guessing.** Map only what the files actually say.
   - If a task has no answer, emit `{"status": "missing"}`.
   - If something cannot be mapped, put it in `extras` instead of dropping it or forcing it into a
     field.
   - Never infer answers from prose when a structured answer exists.
4. **No evaluation logic.** Do not compare against reference answers, read answer keys, or score
   anything. The adapter only translates.
5. **Dependencies:** the Python standard library, `pyyaml`, and `gce.adapters.base` / `gce.trace`.
   Nothing else, and no network access.

## What to produce
Start from `adapters/TEMPLATE.py`. Subclass `gce.adapters.base.Adapter` and implement:
- `discover(run_root) -> Iterable[RunUnitRef]`: every **run unit** (one agent context; it may cover
  one task or many), sorted by a stable key. `RunUnitRef.id` must be unique and stable; it becomes
  `run.run_unit_id`. Make it informative, e.g. `<experiment>/<condition>/<seed>/<unit>`.
- `to_trace(ref) -> dict`: one `gce.trace.v1` document.

Read `schemas/trace.v1.schema.json`, `schemas/stage_log_entry.v1.schema.json` and `docs/schemas.md`.
The mapping you must decide:

| trace.v1 field | look for |
|---|---|
| `run.tasks_covered` | the task/question ids this agent context was asked to solve, exactly as written in the run files (e.g. `bix-10-q4`) |
| `run.experiment`, `condition`, `seed`, `model` | run manifests and config files, or path components; only use path components when they are unambiguous |
| `run.status` | `completed` / `failed` / `timeout` / `unknown`, from return codes or controller status |
| `run.started_at`, `duration_seconds` | manifests or event timestamps |
| `steps[]` | the agent's actions in order. Commands or code executed → `exec`; other tool calls → `tool_call`; files written → `file_write` (put the written content in `input` and the path in `path`); reads → `file_read`; assistant text → `message`. Put tool stdout/stderr in `output`. `index` is 0..n-1; `t` is seconds since run start if timestamps exist. |
| `stage_log[]` | an agent-written stage log (`stage_log.jsonl` or similar) if one exists, mapped onto `stage_log_entry.v1` |
| `final_answers{task}` | `status` (`resolved` / `unresolved` / `missing`), `answer` (the primary answer as a string or number), `candidates[]` (all candidates with their extra fields), `summary` |
| `capabilities` | `raw_steps`: steps carry real tool output. `stage_log`: a stage log is present. `replayable_code`: agent code persisted as runnable files. |
| `extras` | everything else worth keeping: per-command classifications, retry/attempt metadata, validation results, file inventories |

Layout variations you must handle explicitly if they occur:
- **Retries / multiple attempts per unit:** use the attempt that produced the final result, and
  record the others in `extras.attempts` (summary only).
- **Failed or crashed units:** still emit a trace (`status: failed`), with whatever steps exist.
  Every task gets a `final_answers` entry (`missing` if none).
- **Multi-task units** (one context, several questions): `tasks_covered` lists all of them, and
  `final_answers` has one entry per task. If the agent tagged steps with task ids, copy them into
  `steps[].tasks`.
- **Very large outputs:** keep step outputs up to 200,000 characters each. Beyond that, keep the head
  and tail and insert `\n[... N characters truncated by adapter ...]\n`, and count truncations in
  `extras.truncated_outputs`.
- **Several condition layouts in one root** (e.g. a baseline and a variant with extra files): support
  all of them in one adapter, and set `run.condition` accordingly.

## Procedure
1. Explore `{{RUN_ROOT}}` read-only. Use `find`/`ls` with depth limits to see the tree, and open a
   few files of each kind (head only for large files). Identify the unit boundary, where the task ids
   live, the event or step log format, the final-answer format, and any persisted code.
   **Before writing code, write a short layout description as the module docstring.**
2. Check at least two units from **each** condition/variant you find, including one failed unit if
   any exist.
3. Implement the adapter. Keep it small and explicit; name every file you read.
4. Run the conformance check and iterate until it passes with no errors:
   ```bash
   .venv/bin/gce adapter-check --adapter adapters/contrib/{{ADAPTER_NAME}}.py \
       --run-root {{RUN_ROOT}} --sample 12 [--graphs checkpoints/<benchmark>]
   ```
   Then convert everything and confirm that no traces are invalid:
   ```bash
   .venv/bin/gce adapt --adapter adapters/contrib/{{ADAPTER_NAME}}.py --run-root {{RUN_ROOT}} --out out/traces/{{ADAPTER_NAME}}
   ```
5. Open two of the produced traces, one completed and one failed, and verify by eye:
   - the steps are in the right order;
   - outputs are attached to the right commands;
   - final answers match the run's own result file.
6. Report back:
   - the layout description;
   - the mapping table you implemented;
   - what went into `extras`;
   - the counts from `adapter-check` (units, capabilities, answer statuses);
   - anything you could not map.

## Definition of done
- `adapters/contrib/{{ADAPTER_NAME}}.py` exists, with a docstring describing the layout.
- `gce adapter-check` passes, and `gce adapt` writes zero invalid traces.
- `{{RUN_ROOT}}` is unchanged.

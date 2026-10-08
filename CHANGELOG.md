# Changelog

## Unreleased
- Project scaffold, README and packaging.
- v1 JSON Schemas: value_spec, checkpoint_graph, stage_log_entry, trace, evaluation; `gce validate`.
- ValueSpec matching (`gce.values`) and checkpoint graph model (`gce.graph`): task paths, shared nodes, variants, integrity checks.
- Trace model, adapter protocol (`load_adapter` by registry name, module or file), `canonical_dir` adapter, `gce adapt`, `gce adapter-check`, `adapters/ADAPTER_PROMPT.md` and `adapters/TEMPLATE.py`.
- Scoring engine: deterministic probes (raw output, named/unnamed stage-log values, context-anchored fail detection, corroboration of self-reports), node states incl. implied / n/a / unsupported, per-task depth and first failure/gap, hidden misses, group metrics (root causes, consistency, parameter bleed, reuse); pluggable LLM judge (Anthropic built in); `gce score` and `gce report`.
- BixBench reference builder (`python -m builders.bixbench`): questions/ideal specs, notebook outlines and figures, graph drafting by archetype ladder, instrumented notebook re-runs with anchor checks and evidence files; answer-visibility audit data; re-run environment specs (`envs/`).
- Pilot checkpoint graphs for BixBench v1.5 bix-7, bix-10, bix-13, bix-28; re-run specs and evidence for bix-7 and bix-13 (all printed anchors reproduced; bix-13-q1 label mismatch confirmed, bix-13-q3 and bix-7-q1 recovered).
- Calibration on native_vs_b runs: contrib adapter `adapters/contrib/opencode_native_vs_b.py` (written from ADAPTER_PROMPT.md; 278 traces, 0 invalid); probe fixes for file reads, listings, precision, inspection commands, generic contexts and alias-labelled fail detection; validate rejects generic context regexes; adapter-check tolerates graphs covering part of a benchmark; `docs/calibration-native_vs_b.md`.
- LLM judge now uses the DeepSeek chat-completions API (stdlib HTTP, default model deepseek-flash, thinking disabled at temperature 0 unless DEEPSEEK_REASONING_EFFORT is set, JSON mode with fallback, retries, optional on-disk verdict cache) configured from `.env` (`.env.example`; exports take precedence; `gce score --env-file`). The Anthropic backend and the `llm` extra are removed.
- Probe precision from the bix-10 review: context must label the value by position (before it, or right after with no number in between); integer ranges <= 1000 and integers < 1000 need a label; categorical answers are not inferred from mentions when no final answer was given; intermediate checkpoints use reference values with numerical tolerance (bix-10 ORs), answer nodes keep official ranges.
- L1-L3 derivations for the 78-question subset (`builders/bixbench/derive.py`, evidence `derived_78_L1_L3.json`) and a bix-52 re-run (anchors reproduced; finds the notebook bug that saves Jackdaw chromosome lengths as Zebra Finch lengths).
- Report section "Paths per question": per-task Mermaid path diagram (nodes coloured by run outcomes) and a text grid of states and observed values per run (`gce report --graphs`).
- Path views moved to one Markdown file per task group (`paths/<group>.md`, linked from `summary.md`), with outcomes written in words and a definitions table instead of symbols.
- Path views are isolated per running condition: `paths/<condition>/<group>.md` (experiment added to the directory name when conditions share a name).
- Each path file opens with the full checkpoint graph of its group (Mermaid, coloured by the condition's outcomes) and a checkpoint table (description, expected value, questions using it).
- Path files explain checkpoint names: a prefix guide for the prefixes each graph uses, and per question a table of what every checkpoint on its path represents and which questions share it.

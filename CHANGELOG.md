# Changelog

## Unreleased
- Project scaffold, README and packaging.
- v1 JSON Schemas: value_spec, checkpoint_graph, stage_log_entry, trace, evaluation; `gce validate`.
- ValueSpec matching (`gce.values`) and checkpoint graph model (`gce.graph`): task paths, shared nodes, variants, integrity checks.
- Trace model, adapter protocol (`load_adapter` by registry name, module or file), `canonical_dir` adapter, `gce adapt`, `gce adapter-check`, `adapters/ADAPTER_PROMPT.md` and `adapters/TEMPLATE.py`.
- Scoring engine: deterministic probes (raw output, named/unnamed stage-log values, context-anchored fail detection, corroboration of self-reports), node states incl. implied / n/a / unsupported, per-task depth and first failure/gap, hidden misses, group metrics (root causes, consistency, parameter bleed, reuse); pluggable LLM judge (Anthropic built in); `gce score` and `gce report`.

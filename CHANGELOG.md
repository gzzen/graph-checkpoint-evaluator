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

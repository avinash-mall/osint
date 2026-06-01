# `inference-sam3/grounding_dino_gate.py` — Common-Vocab Skip Gate

**Path:** [inference-sam3/grounding_dino_gate.py](../../inference-sam3/grounding_dino_gate.py)
**Lines:** ~230
**Depends on:** Backend ontology vocab (via `${ONTOLOGY_BACKEND_URL}/api/ontology/default-prompts`)

## Purpose

Decide whether a prompt set is uncommon enough to justify Grounding-DINO. Returns `should_run_grounding_dino(prompts, force=False) -> (bool, reason)`. Service entrypoint also requires operator intent (`enabled_layers` includes `grounding_dino`, or `force_grounding_dino=true`) before running the specialist.

## Key symbols

- [`_refresh_ontology_vocab`](../../inference-sam3/grounding_dino_gate.py#L53) — fetches the optical/SAR/multispectral default labels and updates the cache; runs in a **background thread** (1.5 s/sensor timeout), single-flight via `_ONTOLOGY_REFRESH_INFLIGHT`.
- [`_fetch_ontology_vocab`](../../inference-sam3/grounding_dino_gate.py#L89) — returns the **cached** dynamic vocab immediately and kicks off `_refresh_ontology_vocab` when stale. **Never blocks the request path** on the backend round-trip (this was previously up to 3×5 s on a cold/expired cache, surfacing as 12 s `specialists` spikes); the static vocab still gates common terms while a refresh is in flight.
- [`_tokens`](../../inference-sam3/grounding_dino_gate.py#L126), [`_common_vocab`](../../inference-sam3/grounding_dino_gate.py#L130), [`_vocab_token_sets`](../../inference-sam3/grounding_dino_gate.py#L135).
- [`is_common`](../../inference-sam3/grounding_dino_gate.py#L140) — single-prompt check.
- [`should_run_grounding_dino`](../../inference-sam3/grounding_dino_gate.py#L173) — main entry, used by `main.py`.
- [`common_vocab_size`](../../inference-sam3/grounding_dino_gate.py#L200) — surfaced in `/health.gates.grounding_dino`.
- [`reload_vocab`](../../inference-sam3/grounding_dino_gate.py#L204) — on SIGHUP.
- [`_install_sighup_handler`](../../inference-sam3/grounding_dino_gate.py#L223) — registers SIGHUP handler at module import.

## Override

Set `metadata.force_grounding_dino=true` to bypass the gate for a single request. Benchmark harness uses `--force-grounding-dino` to confirm the gate's value is real — see [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md).

## Cross-references

- [grounding-dino-detector.md](grounding-dino-detector.md)
- [decisions/why-grounding-dino-auto-gated.md](../decisions/why-grounding-dino-auto-gated.md)
- Tests: [inference-sam3/tests/test_grounding_dino_gate.py](../../inference-sam3/tests/test_grounding_dino_gate.py)

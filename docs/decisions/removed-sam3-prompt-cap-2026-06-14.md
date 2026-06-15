# Removed the SAM3 per-request prompt cap

**Status:** shipped (2026-06-14). Removes `SAM3_MAX_PROMPTS_PER_REQUEST` (default 64) and the
`_prompt_limit` truncation from [inference-sam3/main.py](../../inference-sam3/main.py). Partially
reverses [audit-fixes-inference-2026-06-12.md](audit-fixes-inference-2026-06-12.md) (Finding 9), which
had *added* the cap to the explicit `text_prompts` branch.

## Symptom

The imagery worker sends the full ontology vocabulary (~133 classes) per chip, but inference-sam3
silently truncated it to 64 — confirmed in the logs: `text_prompts truncated from 133 to 64
(max_prompts/SAM3_MAX_PROMPTS_PER_REQUEST cap)`. So **69 of 133 classes were never evaluated per
chip**: any object whose class fell in the dropped tail went undetected regardless of chip coverage.
It also capped the per-chip SAM3 decode cost, which is why a prompt sweep plateaued at ~64.

## Why remove it

- **Recall.** Open-vocabulary detection is the product's core value (see
  [why-open-vocabulary.md](why-open-vocabulary.md)); silently dropping half the resolved vocabulary
  defeats it. The cap was a blunt cost-control that traded correctness for latency without surfacing
  the loss to the operator.
- The cap was the only thing bounding `resolve_prompts`; `_prompt_limit` was called only there, and
  the cap constants were used only by `_prompt_limit` — so removal is self-contained.
- The original audit fix added the cap to stop the explicit branch from being an *unbounded* knob, but
  the right bound is the ontology size (operators control the vocabulary), not a fixed 64.

## Decision

Delete the cap entirely: the `SAM3_MAX_PROMPTS` / `SAM3_MAX_IMAGE_PROMPTS` / `SAM3_MAX_VIDEO_PROMPTS`
constants, the three `_prompt_limit` truncations in `resolve_prompts`, and the `_prompt_limit` function.
The full resolved (deduped, lowercased) vocabulary now passes through to SAM3. The per-request
`metadata.max_prompts` override is dropped with it.

**Video is unaffected:** `/detect_video` enforces single-prompt-per-session with its own explicit
guard (raises if `len(prompts) > 1`), not via this cap.

## Consequences

- **Cost:** per-chip SAM3 text decode scales linearly with prompt count (~17.6 ms/prompt on the
  RTX 5070 Ti, and ~86% of per-chip time). 133 vs 64 prompts ≈ doubles decode to ~2.3 s; combined with
  full chip coverage, large scenes run materially slower. The throughput lever is now the vocabulary
  size — scope the ontology (branch-scoped prompts) to trade recall breadth for speed, per deployment.
- The `timings_ms` per-chip breakdown (`sam3_batched_forward` / `sam3_decode_loop`) lets operators see
  the decode cost directly; `sam3_detect_timing` logs it every chip.
- No safety ceiling on prompt count remains; a pathologically large vocabulary would scale decode time
  without bound. Bounded in practice by the ontology (~133 classes).

## Cross-references

- [audit-fixes-inference-2026-06-12.md](audit-fixes-inference-2026-06-12.md) — the decision this reverses (Finding 9)
- [why-open-vocabulary.md](why-open-vocabulary.md) — prompt resolution ladder
- [inference/main-app-entrypoint.md](../inference/main-app-entrypoint.md) — `resolve_prompts`
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)

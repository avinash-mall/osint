# Why a SegEarth-OV3-Inspired Presence-Ratio Filter

## Problem

Open-vocabulary detectors hallucinate on overhead imagery. The LAE-80C benchmark (arxiv 2601.22164) clocked OV detectors at a **69% false-positive rate** across an 80-category remote-sensing taxonomy. We already gate on `SAM3_CATEGORY_THRESHOLD=0.40` ([why-category-presence-gate.md](why-category-presence-gate.md)) — that suppresses prompts whose *single best* candidate score is too low.

But the textbook hallucination pattern is different: SAM3 returns many uniformly mediocre masks (max=0.50, mean=0.45 across ten candidates). The legacy max-score gate passes — 0.50 ≥ 0.40 — and every one of those low-confidence masks survives into downstream fusion. Visually this is "all this background kind of looks like X" — a diffuse response to a concept that simply isn't present.

## Research

SegEarth-OV3 (arxiv 2512.08730, Dec 2025) reaches 53.4% mIoU across 17 RS benchmarks by adding a **patch-level presence head** that predicts which categories are visible in the chip *before* per-mask scoring. The paper attributes a 30-40% additional false-positive reduction to this pre-filter — on top of the per-mask confidence gate. The architectural mechanism: a small dual-head module reads the chip-level patch embeddings and emits a per-category presence probability; categories below threshold are masked out before per-mask scoring even runs.

LAE-80C (arxiv 2601.22164) provides the broader context: OV detectors at 69% FPR motivate any presence-style filter that can be added cheaply.

## Decision

Port the *effect* of SegEarth-OV3's presence head without the architectural surgery (a full dual-head implementation would need a new model checkpoint and changes to SAM3's forward pass — deferred).

Implementation: a **score-distribution shape gate** on top of SAM3's existing per-mask scores. For each prompt's score list, compute:

```
presence_ratio = max_score / max(mean_score, EPS)
```

A "real" localised detection has `max >> mean` — high ratio. A diffuse hallucination has `max ≈ mean` — ratio near 1. Drop prompts whose ratio is below `SAM3_PRESENCE_RATIO_FLOOR=1.8` (max must be at least 80% above mean).

Three modes via `SAM3_PRESENCE_MODE`:

| Mode | Behaviour |
|---|---|
| `max` | Legacy only: `max_score >= threshold` (per-class or global). |
| `ratio` | Distribution gate only: `presence_ratio >= floor`. |
| `both` | **Default.** Both gates must pass. Strictly more restrictive than today's behaviour; operators can downgrade to `max` to restore exact legacy semantics. |

Three new env vars: `SAM3_PRESENCE_MODE` (default `both`), `SAM3_PRESENCE_RATIO_FLOOR` (default `1.8`), `SAM3_PRESENCE_RATIO_EPS` (default `0.05`). The eps avoids division-by-zero when SAM3 emits a single mask or near-zero mean scores.

## What was deliberately NOT done

- **Full patch-level dual-head model.** SegEarth-OV3's paper-grade implementation would require either a new model checkpoint trained alongside SAM3 (significant engineering + GPU budget) or a forward-pass hook on SAM3's image encoder to read patch embeddings before the mask decoder. Both are too large for a single-task port. The score-distribution adaptation captures the dominant failure mode (diffuse responses to absent concepts) without touching the model.
- **Replacing the existing max-score gate.** The two gates are complementary — max catches obviously-low responses, ratio catches diffuse responses that lift the mean. Mode `both` runs both for AND-style suppression.
- **Per-prompt or per-branch ratio overrides.** YAGNI for now; operators can tune the global floor or switch modes. If a class needs different sensitivity, the per-class `SAM3_CATEGORY_THRESHOLDS_PER_CLASS` override already exists for the max gate.

## Measured impact

Pending: re-run the triage benchmark with mode `both` against mode `max` on the same chip set. Expected: false-positive reduction on absent-concept prompts (the 30-40% SegEarth-OV3 claim is the upper bound — our adaptation reaches the *distribution-shape* subset of their gain). The 1.8 floor was chosen by hand to leave sharp single-object detections (ratio ≥ 3) comfortably above the line while killing diffuse 10-mask responses (ratio ≈ 1.1) cleanly. The threshold is exposed as an env var so operators can re-tune from benchmark data without a code change.

## Cross-references

- [why-category-presence-gate.md](why-category-presence-gate.md) — the legacy max-score gate this composes with.
- [why-open-vocabulary.md](why-open-vocabulary.md) — why we accept open-vocab false-positive risk in the first place.
- [why-precision-first-inference-defaults.md](why-precision-first-inference-defaults.md) — broader precision posture.
- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md) — runner internals.
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md) — env-var index.

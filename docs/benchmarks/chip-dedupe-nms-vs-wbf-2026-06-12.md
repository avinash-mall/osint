# Cross-Chip Dedupe — NMS vs WBF (DOTA-v1.0 val, indicative)

**Raw report:** [bench/chip_dedupe_nms_vs_wbf.json](../../bench/chip_dedupe_nms_vs_wbf.json)
**Source:** [scripts/eval_chip_dedupe.py](../../scripts/eval_chip_dedupe.py)
**Datasets:** DOTA-v1.0 val (30 real images, 1619 GT boxes)
**Live service:** inference-sam3 `/detect` at `localhost:8001` (real per-chip detections)
**Generated:** 2026-06-12

## What this measures

The imagery worker tiles a scene into overlapping chips, runs `/detect` per
chip, lifts each detection to global image-pixel coordinates, and then
reconciles the duplicate detections that overlapping chips produce in their
shared seams. Two reconciliation strategies exist
([worker_legacy.py#L1782-1794](../../backend/worker_legacy.py)):

- **NMS** (`_DetectionDedupeIndex`, **current default**) — confidence-greedy
  suppression: keep the highest-scoring box per overlapping cluster, drop the
  rest. Followed by a cross-chip `edge_truncated` reconciliation pass.
- **WBF** (`_WeightedBoxFusionIndex`, `DEDUPE_METHOD=wbf`) — Weighted Boxes
  Fusion: average every box in a cluster, weighted by trust × confidence, and
  set the fused score from the cluster mean × multi-detector agreement factor.

The open question the worker comment raises is whether WBF *regresses per-class
recall* relative to NMS. This eval answers exactly that, plus precision/F1 and
mAP, on a real (not synthetic) labelled sample.

Both strategies consume the **identical** global-coord detection stream from a
single set of cached `/detect` responses, so the only variable is the dedupe
algorithm. The real worker classes are imported and driven through their real
`.add()` / `.heads()` / `reconcile_edge_truncated()` contracts — no
reimplementation.

## Parameters

| Parameter | Value | Notes |
|---|---|---|
| chip_size | 1008 px | worker default (`DEFAULT_INFERENCE_CHIP_SIZE`) |
| overlap | 252 px | worker default (`DEFAULT_INFERENCE_OVERLAP`) → step 756 px |
| max_chips / image | 64 | enough to fully tile every val image |
| grid planner | `plan_inference_grid` | the real worker planner (block_size unset) |
| IoU match (scoring) | 0.50 | greedy confidence-ordered GT match |
| WBF iou_threshold | 0.55 | worker default `WBF_IOU_THRESHOLD` |
| WBF expected_models | 2 | worker default `WBF_EXPECTED_MODELS` |
| modality | rgb | DOTA optical chips |

## Sample size

| | |
|---|---|
| Images evaluated | **30** |
| Multi-chip images (real overlap) | **22 / 30** |
| Total chips POSTed to `/detect` | **216** |
| Total GT boxes | **1619** |
| Raw detections (pre-dedupe, global) | **17445** |
| Survivors — NMS | **10849** |
| Survivors — WBF | **13446** (+24%) |
| NMS edge-reconcile merges | 1816 |

22 of 30 images genuinely required a multi-chip overlapping grid (up to 45
chips for the 6573×3727 P0019), so the cross-chip seam dedupe — the thing under
test — was exercised on real duplicate detections, not a degenerate 1-chip path.

## Overall results (IoU = 0.50)

| Method | Precision | Recall | F1 | mAP@0.5 | Survivors |
|---|---|---|---|---|---|
| **NMS** (default) | **0.0599** | 0.4015 | **0.1043** | 0.5607 | 10849 |
| WBF | 0.0501 | **0.4163** | 0.0895 | **0.5629** | 13446 |
| Δ (WBF − NMS) | **−0.0098** | **+0.0148** | **−0.0148** | +0.0022 | +2597 |

> **Reading the absolute precision.** Precision is low for *both* methods
> because the open-vocab `/detect` stack emits many fine-grained labels that
> DOTA never annotated (e.g. `bus`, `truck`, building/road classes), and any
> predicted label outside DOTA's 15-class vocabulary can never match a GT box —
> it is counted as a false positive. This depresses the absolute P/F1 numbers
> but applies **equally** to NMS and WBF, so the *NMS-vs-WBF delta* remains a
> valid apples-to-apples comparison. The mAP@0.5 is computed per DOTA class
> over confidence-ranked predictions and is the more trustworthy quality signal.

## Per-class P/R/F1 and AP@0.5 (DOTA-annotated classes only)

| Class | GT | NMS P/R/F1 | WBF P/R/F1 | NMS AP | WBF AP |
|---|---|---|---|---|---|
| harbor | 93 | 0.85 / 0.71 / 0.77 | 0.82 / 0.71 / 0.76 | 0.626 | 0.616 |
| helicopter | 9 | 0.82 / 1.00 / 0.90 | 0.82 / 1.00 / 0.90 | 1.000 | 1.000 |
| plane | 160 | 0.38 / 0.96 / 0.54 | 0.34 / 0.99 / 0.51 | 0.874 | 0.879 |
| ship | 178 | 0.34 / 0.83 / 0.48 | 0.32 / 0.87 / 0.47 | 0.711 | 0.735 |
| roundabout | 5 | 0.60 / 0.60 / 0.60 | 0.60 / 0.60 / 0.60 | 0.500 | 0.500 |
| large-vehicle | 255 | 0.05 / 0.40 / 0.10 | 0.05 / 0.41 / 0.09 | 0.092 | 0.089 |
| small-vehicle | 630 | 0.05 / 0.27 / 0.08 | 0.05 / 0.28 / 0.08 | 0.123 | 0.121 |
| baseball-diamond | 20 | 0 / 0 / 0 | 0 / 0 / 0 | — | — |
| basketball-court | 6 | 0 / 0 / 0 | 0 / 0 / 0 | — | — |
| ground-track-field | 4 | 0 / 0 / 0 | 0 / 0 / 0 | — | — |
| soccer-ball-field | 8 | 0 / 0 / 0 | 0 / 0 / 0 | — | — |
| swimming-pool | 206 | 0 / 0 / 0 | 0 / 0 / 0 | — | — |
| tennis-court | 45 | 0 / 0 / 0 | 0 / 0 / 0 | — | — |

The zero-recall classes (pools, courts, fields, diamonds) are a *detector
vocabulary* miss on this prompt set — neither dedupe method invents detections —
so they carry no signal for the NMS-vs-WBF question.

### Per-class recall: does WBF regress?

**No per-class recall regression.** On every class WBF's recall is equal to or
marginally higher than NMS (ship 0.83→0.87, plane 0.96→0.99, large/small-vehicle
+0.01). This directly answers the worker comment's worry: WBF does **not** drop
per-class recall on this sample.

### The cost: precision

WBF keeps ~2600 more survivors (+24%); almost all of the extra boxes are false
positives (FP 10199 → 12772). Per-class precision drops on the classes that
matter most here — plane 0.38→0.34, ship 0.34→0.32, harbor 0.85→0.82 — and
overall **F1 regresses 0.104 → 0.089**. mAP@0.5 is a statistical wash
(+0.0022, well inside sample noise).

## Qualitative cases

On every multi-chip image WBF retained more boxes than NMS — it fuses
overlapping seam detections into a kept head instead of suppressing the lower
of a pair. Largest count gaps (WBF − NMS survivors), all on dense multi-chip
scenes:

| Image | Size | Chips | Raw | NMS kept | WBF kept | NMS edge-merges |
|---|---|---|---|---|---|---|
| P0179 | 4774×4542 | 36 | 2029 | 1036 | 1496 | 360 |
| P0161 | 2604×2714 | 16 | 1984 | 1229 | 1539 | 224 |
| P0019 | 6573×3727 | 45 | 2653 | 1703 | 1976 | 142 |
| P0168 | 2304×1792 | 9 | 1017 | 586 | 759 | 128 |

Where NMS's greedy suppression removes a redundant seam duplicate outright, WBF
keeps a fused head; on this open-vocab stream the surplus heads are mostly
non-DOTA / off-class boxes, which is why the extra recall is small and the
extra precision loss is larger.

## Verdict

**KEEP NMS as the worker default.** WBF clears the one bar the worker comment
set — *no per-class recall regression* — but it does **not** meet the bar this
evaluation requires for a flip: comparable-or-better precision. WBF trades a
+1.5-point recall gain for a precision loss that drags overall **F1 down 1.5
points**, while mAP@0.5 is unchanged within noise. For a precision-first
defence-analyst tool that is a net negative.

The decision is **not** decisive in WBF's favour, so the code default is left at
NMS and **no source default was changed.**

## Confidence statement

This is an **indicative** result on a **small** sample (30 images, 1619 GT
boxes), **not** the exhaustive Phase-9 harness the worker comment envisions:

- Absolute precision is suppressed by an open-vocab-label / closed-DOTA-vocab
  mismatch; only the NMS-vs-WBF *delta* and the per-class mAP/recall are
  trustworthy here.
- The serving container is the OLD image (reports prithvi loaded); irrelevant
  for raw `/detect`, but the model mix is not the exact shipping build.
- Confidence calibration and the DB-driven per-class policy floors are **not**
  applied (no DB in the host venv). WBF's score-fusion benefit is largest
  *after* calibration, so a fully calibrated production run could narrow or
  reverse the precision gap. A future, calibrated, ≥200-image run (the
  `fetch_real_datasets.py` default slice) is the right place to revisit this.

Confidence in the verdict (keep NMS) on **this** sample: **moderate-high.** The
recall finding (WBF doesn't regress recall) is **robust**; the precision-loss /
F1-regression finding is **directionally consistent** across the dense
multi-chip images but should be re-confirmed on a larger calibrated run before
WBF is dismissed for good.

## Operator knob (available, off by default)

WBF is opt-in and tunable per deployment without a code change:

| Env var | Default | Effect |
|---|---|---|
| `DEDUPE_METHOD` | `nms` | set `wbf` to enable Weighted Boxes Fusion |
| `WBF_IOU_THRESHOLD` | `0.55` | cluster-membership IoU floor |
| `WBF_EXPECTED_MODELS` | `2` | multi-detector agreement denominator for the fused score |

These three knobs are **not** currently listed in `.env.example` or
[docs/deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md);
documenting them there is a follow-up (noted, not done in this change).

## How to reproduce

```bash
# 1. Fetch the 30 real DOTA val images referenced by labels.json (needs HF_TOKEN in .env):
.venv/bin/pip install huggingface_hub          # host venv has no torch; hub is enough
#    images land in inference-sam3/eval/datasets/dota/chips/ (gitignored)

# 2. Run the harness against the live inference service (responses cached under bench/):
.venv/bin/python scripts/eval_chip_dedupe.py --num-images 30
#    re-runs are cheap — cached /detect JSON is reused; only dedupe + scoring re-execute
```

## Cross-references

- [scripts/eval_chip_dedupe.py](../../scripts/eval_chip_dedupe.py) — the harness
- [decisions/why-wbf-over-nms.md](../decisions/why-wbf-over-nms.md) — the
  *cross-detector* fuser (a different, intra-chip stage) defaults to WBF; this
  doc is about the *cross-chip* dedupe stage, which stays on NMS
- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md) —
  `slice_and_infer`, `_DetectionDedupeIndex`, `_WeightedBoxFusionIndex`
- [scripts/fetch-eval-datasets.md](../scripts/fetch-eval-datasets.md) /
  `scripts/fetch_real_datasets.py` — DOTA val slice provenance
- [benchmarks/inference-layer-comparison.md](inference-layer-comparison.md) —
  the per-layer image-stack benchmark this harness's style mirrors

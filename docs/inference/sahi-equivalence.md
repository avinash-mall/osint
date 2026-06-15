# SAHI ↔ chip-pass equivalence

**Path:** [docs/inference/sahi-equivalence.md](sahi-equivalence.md)
**Lines:** ~90
**Depends on:** [backend/worker_legacy.py](../../backend/worker_legacy.py) (`plan_inference_grid`,
`slice_and_infer`, `_DetectionDedupeIndex`, `_WeightedBoxFusionIndex`),
[architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md),
[decisions/multi-scale-and-full-scene-chip-passes.md](../decisions/multi-scale-and-full-scene-chip-passes.md)

## Purpose

Reviewers sometimes ask whether Sentinel implements **SAHI** (Slicing Aided Hyper Inference —
slice a large scene into overlapping tiles, detect per-tile at native resolution, remap to
full-image coords, merge overlaps) to recover small objects that whole-image downscaling would
destroy. **Yes — it does, as a homegrown superset, not the `sahi` pip package.** This doc maps the
reference SAHI API 1:1 onto our chip-pass system so the correspondence is explicit, and points to the
env knob that turns on the small-object boost plus the harness that measures it.

There is no `sahi` dependency anywhere (the only related dep is `ultralytics`, for the YOLO-based
DOTA-OBB / MVRSD detectors), and no super-resolution/upscaling — like SAHI, we tile at native
resolution. The slicing is reimplemented natively so it integrates with COG-windowed reads,
WGS84 georeferencing, multi-detector fusion, and multi-scale passes.

## Mapping

| SAHI (reference: `sahi` + `get_sliced_prediction`) | Sentinel (ours) |
|---|---|
| `get_sliced_prediction()` | [`slice_and_infer()`](../../backend/worker_legacy.py#L1752) + [`plan_inference_grid()`](../../backend/worker_legacy.py#L1265) |
| `slice_height` / `slice_width` | `INFERENCE_CHIP_SIZE` (default 1008) |
| `overlap_height_ratio` / `overlap_width_ratio` | `INFERENCE_CHIP_OVERLAP` ÷ `INFERENCE_CHIP_SIZE` (252/1008 = 25%); stride `step = chip_size − overlap` |
| `postprocess_type` (NMS / GREEDYNMM) | `DEDUPE_METHOD` (`nms` default / `wbf`) |
| `postprocess_match_metric=IOU`, `postprocess_match_threshold` | per-class NMS IoU in `_DetectionDedupeIndex` / `WBF_IOU_THRESHOLD` |
| per-tile predict → remap → merge to full-image coords | `_apply_chip_response` (chip-px → source-px → WGS84) + `_DetectionDedupeIndex` / `_WeightedBoxFusionIndex` |
| `perform_standard_pred` / `scan_standard()` A/B | optional full-scene decimated pass `INFERENCE_FULL_SCENE_PASS` (fused into the dedupe index, not a standalone baseline) |
| SAHI training-tile box rule (clip to tile, drop low-visibility) | [`retile_labels`](../../backend/scripts/prepare_training_tiles.py#L72) — `min_visibility=0.20`, re-normalised to tile-local YOLO coords |

## Beyond baseline SAHI

The reference is single-scale; ours stacks passes through one **shared** dedupe index so cross-scale
duplicates are fused/suppressed instead of double-emitted (see
[multi-scale-and-full-scene-chip-passes.md](../decisions/multi-scale-and-full-scene-chip-passes.md)):

- **Small-object pass** — `INFERENCE_SMALL_OBJECT_CHIP_SIZE` (+ `_OVERLAP`, `_MAX_CHIPS`): a finer
  grid (e.g. 504) giving small targets ~2× pixels-per-object. **This is the direct SAHI-style
  small/low-res boost.**
- **Full-scene pass** — `INFERENCE_FULL_SCENE_PASS`: one whole-image decimated inference (from COG
  overviews) for objects larger than a chip (runways, piers).
- **Edge reconciliation** — `reconcile_edge_truncated()` stitches objects split across chip
  boundaries that NMS alone misses.
- **Georeferencing + block-aligned reads** — pixel boxes warp to WGS84; chip origins snap to the
  COG block grid to cut read cost.

## Enable the small-object boost

Off by default to preserve the opt-in contract. To turn it on, set in `.env`
(read at worker start — `docker compose restart worker` to apply):

```
INFERENCE_SMALL_OBJECT_CHIP_SIZE=504   # half the 1008 main chip
INFERENCE_SMALL_OBJECT_OVERLAP=128
INFERENCE_SMALL_OBJECT_MAX_CHIPS=256
```

Confirm it ran: the persisted `inference_summary` reports `multi_scale: true` with a two-entry
`passes[]` breakdown (visible via `/api/inference/dashboard`). The boost most benefits the MVRSD
specialist + DOTA-OBB small-vehicle detection, so also confirm `/health` shows
`model_versions.mvrsd loaded: true`. Cost: the finer grid ≈ doubles per-scene inference time
(capped by `INFERENCE_SMALL_OBJECT_MAX_CHIPS`). Full knob reference:
[deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md).

## Measure the uplift

[scripts/eval_chip_dedupe.py](../../scripts/eval_chip_dedupe.py) A/B-compares `main_only` vs
`main_plus_small` on a DOTA-v1.0 val slice, reusing the real `plan_inference_grid` + dedupe index
and reporting per-class recall/precision/F1/AP deltas (focused on `small-vehicle` / `large-vehicle`).
Run in the inference-capable environment (live `inference-sam3` at `:8001`, DOTA val fetched via
`HF_TOKEN`; `bench/` is a runtime dir — never the dev host):

```
python scripts/eval_chip_dedupe.py --num-images 30 --small-object-chip-size 504
```

Record the result as a dated `docs/benchmarks/small-object-pass-uplift-YYYY-MM-DD.md`, mirroring
[chip-dedupe-nms-vs-wbf-2026-06-12.md](../benchmarks/chip-dedupe-nms-vs-wbf-2026-06-12.md).

## Cross-references

- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md) — the chipping step in context
- [decisions/multi-scale-and-full-scene-chip-passes.md](../decisions/multi-scale-and-full-scene-chip-passes.md) — why the extra passes exist
- [decisions/chip-aligned-training-tiles.md](../decisions/chip-aligned-training-tiles.md) — the SAHI-style training-tile rule
- [inference/fusion-and-nms.md](fusion-and-nms.md) — the mask-aware cross-tile dedupe
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md) — every chip-pass knob

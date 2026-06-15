# Multi-scale + full-scene chip passes

**Path:** [backend/worker_legacy.py](../../backend/worker_legacy.py) (`slice_and_infer` chip planner)
**Lines:** N/A (decision record)
**Depends on:** `rasterio` (COG overviews, `out_shape`), the shared dedupe index (`_WeightedBoxFusionIndex` / OBB-NMS), env `INFERENCE_SMALL_OBJECT_*`, `INFERENCE_FULL_SCENE_PASS`

## Purpose

Records two additive chip-planner passes in the imagery worker: an optional finer-grained **small-object** second pass, and an opt-in coarse **full-scene** pass for objects larger than a single chip.

## Why this design

The single fixed-size sliding-window grid has two blind spots. Small targets (TELs, fuel bowsers, light armour) get too few pixels-per-object at the default 1008 px chip; objects larger than one chip (runways, piers, large facilities) are only ever seen fragmented across chip boundaries. Rather than retune the global chip size (which trades one failure for the other), the planner now appends extra passes:

- **Item 1 — small-object pass.** The knobs already existed in code but were undocumented. When `INFERENCE_SMALL_OBJECT_CHIP_SIZE > 0` and `!= INFERENCE_CHIP_SIZE`, a second grid runs at that finer size (e.g. 504) so small classes get a higher pixel budget. `INFERENCE_SMALL_OBJECT_OVERLAP` (128) and `INFERENCE_SMALL_OBJECT_MAX_CHIPS` (256) tune it.
- **Item 2 — full-scene pass.** New `INFERENCE_FULL_SCENE_PASS` (default 0/off). When on, the planner adds exactly **one** window over the whole `(0,0,width,height)` extent read decimated to ~`chip_size` (via rasterio `out_shape`, served from COG overviews) and runs one inference.

**Shared dedupe index.** All passes feed the same dedupe index, so a large object seen both whole (full-scene) and fragmented (main pass), or a small object seen at both scales, is fused/suppressed by NMS/WBF instead of double-emitted.

**scale_x/scale_y georef threading.** Returned box pixel coords are chip-pixel. A per-chip `scale_x`/`scale_y` (source-px per chip-px) maps them back to source pixels before the affine transform + WGS84 warp. Normal grid chips carry `1.0` (chip-px == source-window-px), so the existing passes are byte-identical; only the decimated full-scene chip carries `>1`.

**Progress monotonicity.** The full-scene pass contributes exactly 1 window to `total_windows`, and the grid-derived coverage/source-total aggregates exclude it (it carries a synthetic 1-window grid), so the progress percentage stays monotonic 0-100% and coverage stats reflect only the real sliding-window passes.

## Key symbols

- [chip_passes construction](../../backend/worker_legacy.py#L1808-L1817) — main pass + optional small-object pass.
- [full-scene plan](../../backend/worker_legacy.py#L1859-L1886) — synthetic 1-window plan, decimation math.
- [scale_x/scale_y application](../../backend/worker_legacy.py#L2012-L2093) — chip-px → source-px before the affine warp.
- Env: `INFERENCE_SMALL_OBJECT_CHIP_SIZE` / `_OVERLAP` / `_MAX_CHIPS` ([#L156-L160](../../backend/worker_legacy.py#L156-L160)), `INFERENCE_FULL_SCENE_PASS` ([#L168](../../backend/worker_legacy.py#L168)).

## Inputs / Outputs

No response-schema change. The `inference_summary` gains `multi_scale: bool` and a per-pass `passes[]` breakdown (each with `full_scene`).

## Failure modes

- **MSI/SAR full-scene embedded transform.** For multispectral/SAR full-scene chips the emitted GeoTIFF's embedded transform is over a downsampled grid. Harmless: the worker georeferences from its own source affine + the per-chip `scale_x`/`scale_y`, ignoring the embedded transform.
- Both new passes default off, so the standard single-grid behaviour is unchanged unless an operator opts in.

## Cross-references

- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md) — chip planner / `slice_and_infer`
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md) — chipping step
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md) — the four knobs
- [decisions/why-wbf-over-nms.md](why-wbf-over-nms.md) — the shared dedupe fuser
- [decisions/dense-scene-recall-defaults.md](dense-scene-recall-defaults.md) — flipped the small-object pass default **on** (`504`); this doc's mechanism is unchanged, only the shipped default

# Chip-aligned training tiles + MVRSD integration

**Path:** [backend/scripts/prepare_training_tiles.py](../../backend/scripts/prepare_training_tiles.py) + [backend/scripts/stage_mvrsd.py](../../backend/scripts/stage_mvrsd.py) + [scripts/fetch_reference_datasets.py](../../scripts/fetch_reference_datasets.py) `_fetch_mvrsd`
**Lines:** prepare_training_tiles ~280; stage_mvrsd ~230; fetcher `_fetch_mvrsd` ~120
**Depends on:** `worker_legacy.plan_inference_grid` + inference chip defaults; PIL; CPU only (no torch)

## Purpose

Two related additions:

1. **Chip-aligned training preprocessor** — cut training tiles with the SAME sliding-window planner inference uses, so a fine-tuned detector trains on the same pixel distribution it will see at inference time.
2. **MVRSD integration** — land the Military Vehicle Remote Sensing Dataset (5 vehicle classes: SMV/LMV/AFV/CV/MCV) as a YOLO training/eval corpus and as a drop-in reference-chip source.

## Why this design

- **One chip planner, reused not duplicated.** Inference chips every raster via `plan_inference_grid` (chip_size=1008, overlap=252). If training cuts tiles a different way — or trains on whole scenes far larger than a chip — train and inference pixel distributions diverge. `prepare_training_tiles` imports `plan_inference_grid` directly ([prepare_training_tiles.py#L48](../../backend/scripts/prepare_training_tiles.py#L48)) and calls it with `max_chips=0` (full coverage, no sampling) and no block snapping (training images aren't tiled COGs). HARD RULE: do not reimplement the planner.
- **Pass-through for small images.** MVRSD chips are 640×640 (< chip_size), so the planner returns a single full-size window and the preprocessor copies the image+label unchanged ([prepare_training_tiles.py#L128](../../backend/scripts/prepare_training_tiles.py#L128)). Tiling only does real work on larger user imagery (e.g. the 9152×9152 MVRSD test rasters → 12×12 = 144 tiles). The module is therefore general but a no-op where tiling isn't needed.
- **Standard SAHI-style box rule.** `retile_labels` ([prepare_training_tiles.py#L72](../../backend/scripts/prepare_training_tiles.py#L72)) clips each box to the tile and drops any box whose visible area falls below `min_visibility` (default 0.20), then re-normalises into tile-local YOLO coords.
- **Opt-in, default-safe.** Tiling is wired into the existing worker→`scripts/train.py`→`/train` chain behind a `metrics.tile` flag ([worker_legacy.py#L5759](../../backend/worker_legacy.py#L5759), [scripts/train.py#L99](../../backend/scripts/train.py#L99)). Absent the flag, the training path is byte-for-byte unchanged.
- **MVRSD class order is classes.txt, not the community data.yaml.** The community YOLO port's `data.yaml` `names` list (`[LMV,SMV,MCV,CV,AFV]`) is INCONSISTENT with the label indices it ships; the indices actually follow MVRSD's `classes.txt` (`SMV=0,LMV=1,AFV=2,CV=3,MCV=4`), verified against the official Pascal-VOC XML. Both `stage_mvrsd.py` and the fetcher pin the classes.txt order.
- **MVRSD is drop-in only.** The full 3,000-image imagery is account-locked (Baidu Cloud / SciDB) and derived from Google Earth (research-only) — not programmatically downloadable and redistribution-restricted. So MVRSD follows the same drop-in pattern as xView/DIOR. Because MVRSD ships YOLO/VOC bboxes (not pre-cropped per-class chips), it gets a dedicated bbox-cropping adapter `_fetch_mvrsd` ([fetch_reference_datasets.py#L458](../../scripts/fetch_reference_datasets.py#L458)) rather than the generic `_fetch_dropin_only`.

## Key symbols

- `plan_inference_grid` reuse + tile iteration — [prepare_training_tiles.py#L117](../../backend/scripts/prepare_training_tiles.py#L117)
- `retile_labels` (clip/drop/re-normalise) — [prepare_training_tiles.py#L72](../../backend/scripts/prepare_training_tiles.py#L72)
- `tile_image` (passthrough vs crop) / `tile_dataset` — [prepare_training_tiles.py#L128](../../backend/scripts/prepare_training_tiles.py#L128), [#L198](../../backend/scripts/prepare_training_tiles.py#L198)
- `stage_mvrsd.stage` (demo+community / drop-in → YOLO dataset) — [stage_mvrsd.py#L159](../../backend/scripts/stage_mvrsd.py#L159)
- `_fetch_mvrsd` (per-class reference chips) — [fetch_reference_datasets.py#L458](../../scripts/fetch_reference_datasets.py#L458)
- `_train_worker` device pin — [inference-sam3/main.py#L1987](../../inference-sam3/main.py#L1987)

## Inputs / Outputs

- **stage_mvrsd:** in = demo.zip images + community YOLO labels (or operator drop-in tree); out = `/data/datasets/mvrsd/{images,labels}/{train,val}` + `data.yaml`.
- **prepare_training_tiles:** in = YOLO dataset dir; out = tiled YOLO dataset dir + rewritten `data.yaml`.
- **_fetch_mvrsd:** in = `reference-corpora-input/mvrsd/images+labels`; out = `<out>/mvrsd/<class>/*.png` + `MANIFEST.json`.

## Failure modes

- No real MVRSD imagery present → `stage_mvrsd` raises with operator guidance; `_fetch_mvrsd` returns `status="skipped"`.
- Tiling failure in the train path → job marked `failed` with `error="tiling failed: ..."` before any GPU work ([scripts/train.py#L110](../../backend/scripts/train.py#L110)).
- Training itself is gated on a readable base-weights path AND a free-GPU device pin; see the report — firing blind would default to a saturated cuda:0 and could OOM-crash the live inference process.

## Real vs scaffolded data

- **Real on disk:** 7 train + 5 val MVRSD images (from the official `demo.zip`) paired with community YOLO labels, staged at `/data/datasets/mvrsd`. 199 labelled instances total. Full 3,002-image label set is openly available; full imagery is not.
- **Scaffolded:** the drop-in path for an operator to add the full SciDB/Baidu imagery offline.

## Cross-references

- [scripts/manifests/mvrsd.json](../../scripts/manifests/mvrsd.json) — source URLs, license, drop-in steps.
- [backend-routers/models-training-router.md](../backend-routers/models-training-router.md) — training pipeline + device pin.
- [conventions/adding-a-reference-dataset.md](../conventions/adding-a-reference-dataset.md) — the recipe this followed.
- [operations/reference-corpora-bake.md](../operations/reference-corpora-bake.md) — MVRSD row in Supported sources.
- `worker_legacy.plan_inference_grid` — [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md).

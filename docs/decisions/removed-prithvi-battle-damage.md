# Removed Prithvi-EO-2.0 (battle-damage / flood / burn / multi-temporal-crop)

**Path:** N/A (removal record)
**Lines:** N/A
**Depends on:** former `inference-sam3/prithvi_heads.py`, `inference-sam3/multispectral.py`, `inference-sam3/fusion.py`, `backend/worker_legacy.py`, `backend/main.py`

## Purpose

Records the permanent removal of the Prithvi-EO-2.0 multispectral classification heads (flood, burn-scar, optional multi-temporal crop) and everything wired to them.

## Why this design

The Prithvi heads were the only "battle-damage / environmental" segmentation in the stack, and they consistently produced noisy, messy overlays that misled analysts: diffuse false-positive flood and burn polygons over ordinary terrain. The burn-scar head in particular measured a chip-level IoU ≈ 0 on the eval slices (see the annotated rows in [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md) and `detection-quality-*-2026-05-22.md`). In practice the loader flag (`SAM3_LOAD_PRITHVI`) shipped default-off, so the feature was carrying VRAM/latency/maintenance cost for no operational value. A defence analyst is better served by the SAM3 + DOTA + SAR pipeline alone than by an overlay that fabricates damage.

Removed, rather than gated-off-and-kept: an open-vocabulary GEOINT platform should not advertise a damage-assessment capability it cannot stand behind. Keeping the dead code (heads, the multitemporal Celery task, the `/api/detections/prithvi-overlays` endpoint, and the LayerPanel Flood/Burn/Crops toggles) would only invite re-enabling a known-bad detector.

**Preserved:** the shared HLS multispectral decoder — `decode_hls6()` and `hls_to_rgb_preview()` in [inference-sam3/multispectral.py](../../inference-sam3/multispectral.py), plus the `PRITHVI_CONSTANT_SCALE = 0.0001` reflectance constant — is **not** Prithvi-specific. It is the general HLS-6 → RGB-preview path that SAM3 uses for all multispectral ingest. Only `resize_to_prithvi()` / `pad_to_window()` / `PRITHVI_SIZE` (the Prithvi-window machinery) were dropped.

## Key symbols

Deleted: `inference-sam3/prithvi_heads.py`; `scripts/eval_datasets/hls_burn.py`; `scripts/eval_datasets/sen1floods.py`; `scripts/eval_metrics/mask_metrics.py` (+ test). Removed `fusion.overlay_labels()` (+ orphaned `_iou`), the `worker.run_prithvi_multitemporal` Celery task, the `GET /api/detections/prithvi-overlays` endpoint, the `prithvi` model-registry row, the `imagery_msi` `prithvi` profile entry, and `+prithvi` from `MODEL_VERSION`. Env `SAM3_LOAD_PRITHVI` and `SAM3_PRITHVI_OVERLAY_THRESHOLD` are gone.

## Inputs / Outputs

No runtime contract change for callers: `/detect` on `modality=multispectral` now returns SAM3 detections over the RGB preview (+ DINOv3-SAT embeddings) with no `prithvi_labels` passthrough. The `multispectral` profile is `sam3_image, dinov3_sat`.

## Failure modes

None introduced. Multispectral ingest degrades gracefully to the SAM3 RGB-preview path it always shared.

## Cross-references

- Superseded: `inference/prithvi-multispectral.md` (deleted), `decisions/why-geom-prithvi-in-layerpanel.md` (deleted)
- [inference/multispectral-bands.md](../inference/multispectral-bands.md) — the preserved decoder
- [inference/fusion-and-nms.md](../inference/fusion-and-nms.md) — `overlay_labels` removed
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md) — multispectral row now "SAM3 on RGB preview"
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md) — removed-row record
- Shape precedent: [decisions/removed-fair1m-and-remoteclip.md](removed-fair1m-and-remoteclip.md), [decisions/removed-defence-yolo.md](removed-defence-yolo.md)

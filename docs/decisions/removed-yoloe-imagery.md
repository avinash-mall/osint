# Removed: YOLOE for still-image imagery

## Status

Removed for satellite/still-image inference. YOLOE remains available for FMV.

## What changed

Image upload no longer exposes the `model=yolo26` selector, and image `/detect` / `/detect_raw` reject `enabled_layers` containing `yoloe`, `yoloe_pf`, or `yoloe_seg`. The `imagery` profile no longer loads the YOLOE bundle. FMV upload and `/detect_video` still use YOLOE when `model=yolo26` is selected.

## Why

Pretrained YOLOE produced wrong labels and poor still-image detections on satellite imagery in both prompt-free AMG and prompted PCS use. The failure mode matches the expected domain gap: YOLOE's open-vocabulary/prompt-free heads are natural-image/LVIS-style detectors, while satellite targets are tiny, overhead, densely packed, and often orientation-sensitive.

Sentinel already has a better validated image path: SAM3 with DOTA-OBB and gated specialists. The DOTA benchmark snapshots show `sam3+dota_obb` as the strongest default image stack, while YOLOE imagery had not been validated and bypassed those specialists entirely.

## Consequences

- Imagery stays on the SAM3 sensor pipeline plus DOTA-OBB / GDINO / Prithvi / TerraMind as configured.
- Hand-crafted image requests that name YOLOE layers fail loudly with HTTP 400.
- The old YOLOE-PF imagery LLM display-label workaround is obsolete.
- Existing historical YOLOE image rows are not migrated; deployments that need a clean slate can rebuild volumes.

## Cross-references

- [inference/main-app-entrypoint.md](../inference/main-app-entrypoint.md)
- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [frontend/workspace-ingest.md](../frontend/workspace-ingest.md)
- [inference/yoloe-tracker.md](../inference/yoloe-tracker.md)
- [benchmarks/detection-quality-eval-2026-05-22.md](../benchmarks/detection-quality-eval-2026-05-22.md)

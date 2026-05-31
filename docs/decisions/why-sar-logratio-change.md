# Decision — SAR change detection is a log-ratio preset, not a new pipeline

## Context

Sentinel already had optical two-pass change detection
(`change_detection.py`, `mode: "raster_diff"`) and SAR ship detection (CA-CFAR),
but no **multi-temporal SAR change** — the flood/damage/disturbance product that
works through cloud and at night. ShadowBroker exposes a SAR ground-change
layer; the transferable, offline-viable part is the algorithm, not its online
Copernicus fetch path.

## Decision

Add SAR change as a **`method="sar_logratio"` preset inside the existing
`compute_change`**, not a parallel module. It reuses the same intersection
window, pixel cap, polygonizer (`_polygonize_mask`), review/overlay path, and
Celery/endpoint plumbing. The only new code is the change-map producer
`_change_map_sar` and two env knobs.

The operator is `ratio_dB = 10·log10((after+ε)/(before+ε))` on the VV band
(band 1), median-despeckled (`CHANGE_DET_SAR_DESPECKLE`), thresholded on
`|dB| ≥ CHANGE_DET_SAR_THRESHOLD_DB` (default 3 dB ≈ 2× backscatter).

## Why

- **Ratio, not difference.** SAR backscatter is dominated by multiplicative
  speckle; the log-ratio is the standard, calibration-robust change operator.
- **|dB| catches both directions.** Brightening (new structures, rough water)
  and darkening (flooding reflects radar away) are both change.
- **Preset over parallel pipeline.** Sharing the optical spine keeps the
  surface area tiny (CLAUDE.md simplicity rule) and means SAR change lands in
  the same analyst review flow with no new UI concepts — just an
  Optical/SAR selector in `ChangeDetectionDialog.tsx`.
- **Offline.** Operates only on locally-ingested COGs; no Copernicus/CDSE
  fetch (Hard rule #8). Clean-room — no ShadowBroker (AGPL) source copied.

## Consequences

- VV-only (band 1) so it works on single- or dual-pol GRD; VH is ignored.
- The backend does not enforce that the passes are actually SAR — running the
  preset on optical input produces meaningless output. The UI labels intent;
  sensor-type enforcement is deferred (see Failure modes in the module doc).

## Cross-references

- [backend/change-detection-raster.md](../backend/change-detection-raster.md)
- [operations/change-detection-runbook.md](../operations/change-detection-runbook.md)
- [backend/sar-cfar-detector.md](../backend/sar-cfar-detector.md) (the other SAR product)
- Tests: [backend/tests/test_change_detection_sar.py](../../backend/tests/test_change_detection_sar.py)

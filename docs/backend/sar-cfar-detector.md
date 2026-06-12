# `backend/sar_cfar.py` — CA-CFAR Ship Detector

**Path:** [backend/sar_cfar.py](../../backend/sar_cfar.py)
**Lines:** ~275
**Depends on:** `numpy`, `rasterio`

## Purpose

Constant False Alarm Rate ship detection on Sentinel-1 GRD. Cell-Averaging CFAR computes a local clutter mean over a moving window, flags pixels exceeding `mean × multiplier`. Pure CPU; no learned weights.

## Why this design

- **Independent of TerraMind/SAM3** — CFAR detects on real SAR backscatter, not the synthetic optical preview. See [decisions/why-sar-confidence-cap.md](../decisions/why-sar-confidence-cap.md): CFAR detections are **not** SAR-proxy; they're real.
- **CPU-only** — runs in the worker process without GPU. A 50000×50000 GRD chip processes in seconds.
- **dB scale** — works on log-magnitude backscatter (-30 to 0 dB clipped), not linear amplitude.
- **Connected components → bboxes** — contiguous suprathreshold pixels merged. Minimum 4 pixels suppresses single-pixel noise.
- **Guard-excluded clutter statistics** — both the clutter mean (`mu_clutter`) AND variance are estimated over the background window *minus* the guard band (proportional subtraction of the guard window's first and second moments). The Z-score `(x − mu_clutter) / sigma_clutter` therefore uses a consistent population; estimating σ over the guard-*inclusive* window (the earlier bug) let a bright target's own energy leak into its clutter σ, depressing the Z-score exactly where detections live. See [decisions/completed-deferred-items-2026-06-09.md](../decisions/completed-deferred-items-2026-06-09.md). The VH cross-pol consistency gate uses the same guard-excluded μ/σ derivation as VV — its guard-inclusive statistics had the identical flaw and depressed `z_vh` on the target. See [decisions/audit-fixes-backend-core-2026-06-11.md](../decisions/audit-fixes-backend-core-2026-06-11.md).

## Key symbols

- [`_box_kernel_mean`](../../backend/sar_cfar.py#L47) — fast windowed mean via integral image.
- [`_bbox_components`](../../backend/sar_cfar.py#L75) — connected-component → list of `(x, y, w, h, pixels)`.
- [`detect_ships_cfar`](../../backend/sar_cfar.py#L150) — main entry; returns detection dicts compatible with the standard schema.

## Failure modes

- Input not 2-band → `ValueError`.
- All-zero band → `[]`.

## Cross-references

- [decisions/why-sar-confidence-cap.md](../decisions/why-sar-confidence-cap.md)
- [inference/sar-bands.md](../inference/sar-bands.md)
- [scripts/eval-runners.md](../scripts/eval-runners.md) — `eval_sar_cfar.py`
- [benchmarks/sar-cfar-evaluation.md](../benchmarks/sar-cfar-evaluation.md)
- [decisions/audit-fixes-backend-core-2026-06-11.md](../decisions/audit-fixes-backend-core-2026-06-11.md) — VH guard-excluded statistics
- Tests: [backend/tests/test_sar_cfar.py](../../backend/tests/test_sar_cfar.py)

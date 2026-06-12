# `inference-sam3/sar.py` — Sentinel-1 GRD Decoder

**Path:** [inference-sam3/sar.py](../../inference-sam3/sar.py)
**Lines:** ~37
**Depends on:** `numpy`, `rasterio`

## Purpose

Decode a 2-band VV/VH GRD GeoTIFF payload, clip to dB range, produce a normalized `[0, 1]` chip for TerraMind S1→S2 synthesis.

## Pipeline

1. **Read** 2-band float32 reflectance (or `int16` log-scaled — both supported).
2. **NaN nodata neutralised** (`np.nan_to_num`): S1 GRD swath-edge NaNs map to 0 in the linear-power branch (then floored by the dB conversion) or to `SAR_DB_FLOOR` in the already-dB branch. Without this, NaN passed through clip/normalize, smeared via `cv2.resize`, and turned the downstream percentile stretch into a garbage all-black chip — zero detections returned as HTTP 200. Mirrors the MSI path's `nan_to_num`. See [decisions/audit-fixes-inference-2026-06-12.md](../decisions/audit-fixes-inference-2026-06-12.md).
3. **dB clip** to `[-30, 0]`.
4. **Linear stretch** to `[0, 1]`.

## Key symbols

- [`decode_s1grd`](../../inference-sam3/sar.py#L14) — bytes → `(H, W, 2)` float32 in [0, 1].
- [`resize_to_terramind`](../../inference-sam3/sar.py#L32) — resize to TerraMind's expected input size.

## Cross-references

- [terramind-sar-synthesis.md](terramind-sar-synthesis.md) — the consumer
- [backend/sar-cfar-detector.md](../backend/sar-cfar-detector.md) — the SAR-native path (independent of TerraMind)
- [decisions/why-sar-confidence-cap.md](../decisions/why-sar-confidence-cap.md)
- [decisions/audit-fixes-inference-2026-06-12.md](../decisions/audit-fixes-inference-2026-06-12.md) — NaN-nodata fix

# `inference-sam3/sar.py` — Sentinel-1 GRD Decoder

**Path:** [inference-sam3/sar.py](../../inference-sam3/sar.py)
**Lines:** ~30
**Depends on:** `numpy`, `rasterio`

## Purpose

Decode a 2-band VV/VH GRD GeoTIFF payload, clip to dB range, produce a normalized `[0, 1]` chip for TerraMind S1→S2 synthesis.

## Pipeline

1. **Read** 2-band float32 reflectance (or `int16` log-scaled — both supported).
2. **dB clip** to `[-30, 0]`.
3. **Linear stretch** to `[0, 1]`.

## Key symbols

- [`decode_s1grd`](../../inference-sam3/sar.py#L14) — bytes → `(H, W, 2)` float32 in [0, 1].
- [`resize_to_terramind`](../../inference-sam3/sar.py#L25) — resize to TerraMind's expected input size.

## Cross-references

- [terramind-sar-synthesis.md](terramind-sar-synthesis.md) — the consumer
- [backend/sar-cfar-detector.md](../backend/sar-cfar-detector.md) — the SAR-native path (independent of TerraMind)
- [decisions/why-sar-confidence-cap.md](../decisions/why-sar-confidence-cap.md)

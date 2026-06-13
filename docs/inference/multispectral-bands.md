# `inference-sam3/multispectral.py` — HLS-6 Band Handling

**Path:** [inference-sam3/multispectral.py](../../inference-sam3/multispectral.py)
**Lines:** ~37
**Depends on:** `numpy`, `rasterio`

## Purpose

Decode the 6-band HLS / S2-L2A GeoTIFF payload the worker sends for multispectral chips and produce an RGB preview SAM3 can ingest.

## Band order

HLS-6 standard band order:

1. Blue
2. Green
3. Red
4. Narrow-NIR
5. SWIR-1
6. SWIR-2

`PRITHVI_CONSTANT_SCALE = 0.0001` is the general HLS reflectance scale: HLS reflectance is uint16 (`0..10000`) scaled to float32 (`0..1`). `decode_hls6` applies it for all multispectral ingest (the `_HLS_SCALE_MODE` env gates `always` / `never` / auto-detect on mean magnitude); the constant is no longer Prithvi-specific.

## Key symbols

- [`decode_hls6`](../../inference-sam3/multispectral.py#L21) — bytes (multipart payload) → `(6, H, W)` float32, reflectance-scaled per `_HLS_SCALE_MODE`.
- [`hls_to_rgb_preview`](../../inference-sam3/multispectral.py#L33) — picks bands 3/2/1 (RGB), 2/98-percentile stretches for SAM3.

## Cross-references

- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md)
- [service-overview.md](service-overview.md) — SAM3 RGB-preview path for MSI

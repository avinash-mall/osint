# `inference-sam3/multispectral.py` — HLS-6 Band Handling

**Path:** [inference-sam3/multispectral.py](../../inference-sam3/multispectral.py)
**Lines:** ~40
**Depends on:** `numpy`, `rasterio`

## Purpose

Decode the 6-band HLS / S2-L2A GeoTIFF payload the worker sends for multispectral chips, produce an RGB preview, resize to Prithvi's input shape.

## Band order

HLS-6 standard band order, expected by Prithvi:

1. Blue
2. Green
3. Red
4. Narrow-NIR
5. SWIR-1
6. SWIR-2

Prithvi `constant_scale=0.0001` applied: HLS reflectance is uint16 (`0..10000`) scaled to float32 (`0..1`).

## Key symbols

- [`decode_hls6`](../../inference-sam3/multispectral.py#L13) — bytes (multipart payload) → `(H, W, 6)` float32.
- [`hls_to_rgb_preview`](../../inference-sam3/multispectral.py#L21) — picks bands 3/2/1 (RGB), stretches for SAM3.
- [`resize_to_prithvi`](../../inference-sam3/multispectral.py#L28) — bilinear resize to Prithvi's input shape.
- [`pad_to_window`](../../inference-sam3/multispectral.py#L36) — pads to a window size (default 512) for the Prithvi windowed inference path.

## Cross-references

- [prithvi-multispectral.md](prithvi-multispectral.md) — the consumer
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md)

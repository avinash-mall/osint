# `inference-sam3/terramind.py` — TerraMind S1→S2 Synthesis

**Path:** [inference-sam3/terramind.py](../../inference-sam3/terramind.py)
**Lines:** ~77
**Depends on:** `terratorch`, TerraMind v1 weights

## Purpose

Generate a synthetic Sentinel-2 RGB preview from Sentinel-1 GRD (VV/VH) bands. Required for the SAR path: SAM3 doesn't segment SAR directly — it segments TerraMind's synthetic optical reconstruction.

## Key symbols

- [`load`](../../inference-sam3/terramind.py#L13) — builds the TerraMind bundle.
- [`s1_to_s2_rgb`](../../inference-sam3/terramind.py#L30) — main entry: `(chip2_norm) -> rgb_uint8`.
- [`pool_patches`](../../inference-sam3/terramind.py#L54) — TerraMind embedding pooling (used when `metadata.terramind_embedding` is requested).
- [`_fallback_sar_rgb`](../../inference-sam3/terramind.py#L73) — when TerraMind isn't loaded, falls back to a naive false-color RGB so the path still produces a navigable preview.

## Why the confidence cap

SAM3 detections on the synthetic preview are flagged `sar_proxy=true` and capped at `SAM3_SAR_CONF_CAP=0.85`. See [decisions/why-sar-confidence-cap.md](../decisions/why-sar-confidence-cap.md): a confident SAM3 detection on a synthetic image is not the same as a confident detection on a real optical chip — the operator must review.

## Cross-references

- [sar-bands.md](sar-bands.md)
- [decisions/why-sar-confidence-cap.md](../decisions/why-sar-confidence-cap.md)
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md)

# `inference-sam3/prithvi_heads.py` + `multispectral.py` — Prithvi-EO-2.0

**Paths:**
- [inference-sam3/prithvi_heads.py](../../inference-sam3/prithvi_heads.py) (~173 lines) — task heads
- [inference-sam3/multispectral.py](../../inference-sam3/multispectral.py) (~40 lines) — HLS-6 band decoder

**Depends on:** `transformers`, `torch`, `terratorch`, Prithvi-EO-2.0 weights from HuggingFace

## Purpose

Per-pixel multispectral classification heads (flood, burn, optional multi-temporal crop) on Prithvi-EO-2.0. Produces overlay polygons joined into SAM3 detections via the `prithvi_labels` field.

## How a multispectral `/detect` works

1. **`multispectral.decode_hls6`** ([#L13](../../inference-sam3/multispectral.py#L13)) reads the 6-band HLS GeoTIFF payload as float32 reflectance.
2. **`multispectral.hls_to_rgb_preview`** ([#L21](../../inference-sam3/multispectral.py#L21)) generates an RGB preview for SAM3's text-prompt path.
3. **`multispectral.resize_to_prithvi`** ([#L28](../../inference-sam3/multispectral.py#L28)) brings the chip to Prithvi's 224×224 windowed shape.
4. **`prithvi_heads.run_all`** ([#L106](../../inference-sam3/prithvi_heads.py#L106)) runs flood + burn (always) + crop (only when `metadata.hls_timesteps==3`).
5. Overlay masks → [fusion.overlay_labels](fusion-and-nms.md) → appends `"water"` / `"crop:corn"`-style labels to any SAM3 detection whose mask overlaps the Prithvi mask at IoU ≥ `SAM3_PRITHVI_OVERLAY_THRESHOLD`.

## Key symbols (`prithvi_heads.py`)

- [`load_all`](../../inference-sam3/prithvi_heads.py#L16) — loads flood + burn + crop heads.
- [`_load_task_model`](../../inference-sam3/prithvi_heads.py#L26) — generic head loader with `terratorch`.
- [`_clean_argv_for_lightning`](../../inference-sam3/prithvi_heads.py#L64) — `terratorch` (Lightning-based) parses `sys.argv`; this clears it.
- [`_first_existing`](../../inference-sam3/prithvi_heads.py#L73) — pattern-based weight file resolution.
- [`_to_eval_device`](../../inference-sam3/prithvi_heads.py#L83).
- [`run_all`](../../inference-sam3/prithvi_heads.py#L106), [`_run_binary_windowed`](../../inference-sam3/prithvi_heads.py#L117), [`_run_windowed`](../../inference-sam3/prithvi_heads.py#L123), [`_windows`](../../inference-sam3/prithvi_heads.py#L132), [`_invoke_prithvi`](../../inference-sam3/prithvi_heads.py#L138).

## Cross-references

- [multispectral-bands.md](multispectral-bands.md)
- [fusion-and-nms.md](fusion-and-nms.md)
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md) — modality dispatch

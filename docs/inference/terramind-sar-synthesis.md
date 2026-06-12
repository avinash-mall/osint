# `inference-sam3/terramind.py` — TerraMind S1→S2 Synthesis

**Path:** [inference-sam3/terramind.py](../../inference-sam3/terramind.py)
**Lines:** ~87
**Depends on:** `terratorch`, TerraMind v1 weights

## Purpose

Generate a synthetic Sentinel-2 RGB preview from Sentinel-1 GRD (VV/VH) bands. Required for the SAR path: SAM3 doesn't segment SAR directly — it segments TerraMind's synthetic optical reconstruction.

## Key symbols

- [`load`](../../inference-sam3/terramind.py#L13) — builds the TerraMind bundle.
- [`s1_to_s2_rgb`](../../inference-sam3/terramind.py#L30) — main entry: `(chip2_norm) -> rgb_uint8`. NaN escaping the generator is `nan_to_num`-ed before the percentile stretch (mirrors the MSI preview path).
- [`pool_patches`](../../inference-sam3/terramind.py#L60) — whole-chip patch-token pooling. `main._detect_pipeline` computes this **once** per SAR chip (threadpool, under the forward lock) and stamps the same dict onto every detection — it used to re-run per detection, unlocked, on the event loop.
- [`_fallback_sar_rgb`](../../inference-sam3/terramind.py#L83) — TerraMind not loaded → naive false-color RGB so the path still produces a navigable preview.

Both GPU forwards (`s1_to_s2_rgb`, `pool_patches`) pin the current CUDA device via `inference_utils.device_ctx(bundle["device"])` — they run in the anyio threadpool where the current device defaults to `cuda:0`, like the other specialists. Callers in `main.py` hold `bundle["forward_lock"]` around them; the `/detect` SAR-decode call site also runs the `_cuda_context_poisoned` → `os._exit(1)` self-heal. See [decisions/audit-fixes-inference-2026-06-12.md](../decisions/audit-fixes-inference-2026-06-12.md).

## Why the confidence cap

SAM3 detections on the synthetic preview are flagged `sar_proxy=true` and capped at `SAM3_SAR_CONF_CAP=0.85`. See [decisions/why-sar-confidence-cap.md](../decisions/why-sar-confidence-cap.md): a confident SAM3 detection on a synthetic image ≠ a confident detection on a real optical chip — operator must review.

## Cross-references

- [sar-bands.md](sar-bands.md)
- [decisions/why-sar-confidence-cap.md](../decisions/why-sar-confidence-cap.md)
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md)
- [decisions/audit-fixes-inference-2026-06-12.md](../decisions/audit-fixes-inference-2026-06-12.md) — once-per-chip pooling, device pin, NaN guard

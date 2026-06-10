# Why DINOv3 embeddings aren't gated by enabled_layers (auto-identify Fix)

**Status:** accepted
**Date:** 2026-06-10
**Scope:** `inference-sam3/main.py` (detection embedding pass)

## Decision

The per-detection DINOv3-SAT embedding pass no longer requires
`_layer_active("dinov3_sat")`. It runs whenever `SAM3_EMBED_DETECTIONS=1` and the
DINOv3 model is resident, **regardless of a request's `enabled_layers` filter**.

```python
# before:  if SAM3_EMBED_DETECTIONS and _layer_active("dinov3_sat") and bundle.get("dinov3_sat") and detections:
# after:   if SAM3_EMBED_DETECTIONS and bundle.get("dinov3_sat") and detections:
```

## Why

`enabled_layers` is a **detector** allow-list. The embedding is re-ID
*enrichment* — it feeds the backend's reference-platform auto-identify and the
similarity DB — not a detector. Gating it behind the detector filter meant a
request that scoped layers to detectors (e.g. `["sam3","dota_obb","grounding_dino"]`)
left every detection with the placeholder `{"model":"disabled","dim":0,...}`.

That placeholder is a truthy dict with an empty vector, so the backend's
`store_detections` auto-identify (`worker_legacy.py`) passed its `if emb_dict:`
guard, decoded an empty vector, and issued a pgvector query that errored — once
per detection. A 2020-detection pass logged **2020 `auto-identify failed`
warnings**. Decoupling the embedding makes real 1024-d vectors flow, so
auto-identify runs on real data (and the empty-vector error disappears).

**Verified:** after the change, a pass with a detector-only `enabled_layers`
computes embeddings (`timings.embedding > 0`) and logs **0** auto-identify
failures (was 2020). Cost: one batched DINOv3 forward per pass (the embedding
already existed for the default no-filter path; this just stops it being
suppressed by a layer scope).

To actually *match* platforms, the reference DB must also be seeded
(`POST /api/admin/reference/seed`; corpora are baked in the assets image). Until
then auto-identify runs and returns no candidates — cleanly, no error.

## Cross-references

- [backend/reference-platform-db.md](../backend/reference-platform-db.md)
- [decisions/why-auto-identify-in-backend-not-inference.md](why-auto-identify-in-backend-not-inference.md)
- [inference/main-app-entrypoint.md](../inference/main-app-entrypoint.md)

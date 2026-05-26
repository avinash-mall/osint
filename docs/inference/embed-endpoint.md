# `inference-sam3` — `POST /embed`

**Path:** [inference-sam3/main.py](../../inference-sam3/main.py)
**Lines:** ~25 (the route handler)
**Depends on:** `embedding.dinov3_pool()`, the `dinov3_sat` layer in the active profile (auto-loaded via `_ensure_profile("imagery")` on first call).

## Purpose
Compute a DINOv3-SAT 1024-d embedding of a single image. Lightweight alternative to `POST /detect` for callers that only need the embedding, not the full SAM3/DOTA/GDINO/YOLOE pipeline.

## Why this design
See [why-standalone-embed-endpoint.md](../decisions/why-standalone-embed-endpoint.md). The bake script (Plan B) and the analyst lookup (Plan D) both want fast embeddings of arbitrary images without paying the full detection-pipeline cost. The shared `dinov3_pool()` is already in the inference image; this route is a thin wrapper.

## Key symbols
- [`embed_endpoint`](../../inference-sam3/main.py#L1242-L1266) — the FastAPI route handler.

## Request
```
POST /embed
Content-Type: multipart/form-data
Form field:
  image: <PNG | JPEG bytes>
```

## Response
```json
{
  "model": "facebook/dinov3-vitl16-pretrain-sat493m",
  "dim": 1024,
  "fp16_b64": "<base64-encoded fp16 vector>"
}
```

Decode with:
```python
import base64, numpy as np
arr = np.frombuffer(base64.b64decode(resp["fp16_b64"]), dtype=np.float16).astype(np.float32)
```

## Failure modes
- `503` "dinov3_sat layer not loaded" → can only happen if a non-imagery profile is loaded and `dinov3_sat` is absent. Standard imagery profile auto-loads via `_ensure_profile("imagery")` on first call.
- `400` "could not decode image" → image bytes are not a valid PNG/JPEG.
- `500` "embedding computation returned empty result" → crop too small for DINOv3-SAT.

## Auto-heal behaviour
The handler calls `_ensure_profile("imagery")` as its first action, matching the pattern of every other route in `inference-sam3/main.py`. First call against a cold container will block for ~10–30 s while the imagery profile loads.

## Cross-references
- [reference-platform-baker.md](../backend/reference-platform-baker.md) — the primary consumer in Plan B.
- [dinov3-embeddings.md](dinov3-embeddings.md) — the model bundle this route uses.
- [main-app-entrypoint.md](main-app-entrypoint.md) — `inference-sam3/main.py` route inventory.

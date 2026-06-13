# `inference-sam3` — `POST /embed`

**Path:** [inference-sam3/main.py](../../inference-sam3/main.py)
**Lines:** ~2089
**Depends on:** `embedding.dinov3_pool()`, the `dinov3_sat` layer in the active profile (auto-loaded via `_ensure_profile("imagery_rgb")` on first call).

## Purpose
Compute a DINOv3-SAT 1024-d embedding of a single image. Lightweight alternative to `POST /detect` for callers that only need the embedding, not the full SAM3/DOTA/GDINO pipeline.

## Why this design
See [why-standalone-embed-endpoint.md](../decisions/why-standalone-embed-endpoint.md). The bake script (Plan B) and the analyst lookup (Plan D) both want fast embeddings of arbitrary images without paying the full detection-pipeline cost. The shared `dinov3_pool()` is already in the inference image; this route is a thin wrapper.

## Key symbols
- [`embed_endpoint`](../../inference-sam3/main.py#L1484-L1540) — the FastAPI route handler.

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
- `503` "dinov3_sat layer not loaded" → can only happen if a non-imagery profile is loaded and `dinov3_sat` is absent. Standard imagery profile auto-loads via `_ensure_profile("imagery_rgb")` on first call.
- `503` "profile swap … deferred" → `_ensure_profile` would need a real teardown+reload while another request is in flight; retry (see [decisions/audit-fixes-inference-2026-06-12.md](../decisions/audit-fixes-inference-2026-06-12.md)).
- `400` "could not decode image" → image bytes are not a valid PNG/JPEG.
- `500` "embedding computation returned empty result" → crop too small for DINOv3-SAT.
- Poisoned CUDA context in the forward → `logger.critical` + `os._exit(1)` self-heal, same as `_detect_pipeline_guarded` (see [decisions/why-exit-on-poisoned-cuda-context.md](../decisions/why-exit-on-poisoned-cuda-context.md)).

## Auto-heal behaviour
The handler calls `_ensure_profile("imagery_rgb")` (any imagery profile carries `dinov3_sat`; rgb is the lightest), matching the pattern of every other route in `inference-sam3/main.py`. First call against a cold container will block for ~10–30 s while the profile loads.

## Concurrency / device
The handler brackets itself with `_enter_request`/`_leave_request` so the `/load`//`/unload` in-flight guards (and `_ensure_profile`'s swap guard) see running embeds. The GPU forward runs via `run_in_threadpool` (not on the event loop, which would block health checks) **under `bundle["forward_lock"]`** — the same lock every detect forward takes, so an embed can't race another forward on serialize-forwards hosts ([decisions/why-serialize-forwards-on-a100-cu13x.md](../decisions/why-serialize-forwards-on-a100-cu13x.md)). `dinov3_pool` pins the current CUDA device with `device_ctx(bundle["device"])` — matching `embed_crops_batched`. Without the pin, on a multi-GPU host the bundle's forward could issue cross-device kernels (current device defaults to `cuda:0`) and illegal-access under concurrency.

## Cross-references
- [reference-platform-baker.md](../backend/reference-platform-baker.md) — the primary consumer in Plan B.
- [dinov3-embeddings.md](dinov3-embeddings.md) — the model bundle this route uses.
- [main-app-entrypoint.md](main-app-entrypoint.md) — `inference-sam3/main.py` route inventory.
- [decisions/audit-fixes-inference-2026-06-12.md](../decisions/audit-fixes-inference-2026-06-12.md) — request accounting + forward lock + self-heal.

# Why inference-sam3 preloads imagery at lifespan startup

**Decision:** The [inference-sam3 lifespan](../../inference-sam3/main.py) unconditionally calls `_ensure_profile("imagery")` after the existing `preload_models_on_startup()` step, unless `SAM3_SKIP_PRELOAD=1` is set. This guarantees `model_loaded=true` by the time the compose healthcheck runs, so a freshly-built container reports `healthy` without needing a client request to warm the pool.

**Date:** 2026-05-27.

## Context

End-to-end verification of the Reference Embedding DB found inference-sam3 reported `unhealthy` despite `/health` returning HTTP 200 consistently. Root cause: the compose healthcheck strict-checks `model_loaded AND not model_error`, and `model_loaded = bool(_pool)`. On most GPU profiles (`configure_host.py` only sets `sam3_preload_models=True` for `all`/`hopper`/`blackwell-dc`), the pool stays empty until the first `/embed` or `/detect` call triggers `_ensure_profile("imagery")` lazily. So the container was operationally fine but visibly unhealthy.

Three fixes were on the table:

1. **Loosen the healthcheck** to check HTTP-200 only.
2. **Preload imagery at lifespan** (this decision).
3. **Split into `/health` (liveness) + `/ready` (pool warm)** with the healthcheck moving to `/ready`.

## Why preload-at-lifespan

- **Healthcheck stays strict.** `model_loaded AND not model_error` is the right answer — it catches real load failures (missing weights, GPU OOM, broken Dockerfile). Loosening it to HTTP-200 trades one false-positive (unhealthy-but-working) for the much worse false-negative (healthy-but-broken).
- **Reuses an existing helper.** `_ensure_profile` is already the cold-load path used by `/embed` and `/detect`. Calling it from lifespan is one line; it's idempotent against the existing `preload_models_on_startup()` (treats `all` as superset).
- **Lifespan startup is the natural place.** The container's "ready" semantics already align with lifespan completion — every other service preloads what it needs there.
- **`SAM3_SKIP_PRELOAD=1` is the escape hatch.** Operators on truly constrained GPUs (or doing weight-bake CI where models aren't yet on disk) can opt out and accept the prior "unhealthy until first request" behavior.
- **No new endpoints.** A `/ready` route would need consumers in compose, kubernetes specs, the admin Health Dashboard, and any external monitoring. Preload is a pure server-side change.

## How to apply

- Code: [inference-sam3/main.py — `lifespan()`](../../inference-sam3/main.py).
- Default: imagery preload ON.
- Opt-out: `SAM3_SKIP_PRELOAD=1` in the container env.
- The existing `SAM3_PRELOAD_MODELS` + `SAM3_PRELOAD_PROFILE` knobs still take precedence — they run first via `preload_models_on_startup()`, and the lifespan-level `_ensure_profile("imagery")` becomes a no-op when the `all`/`imagery` superset is already loaded.
- Healthcheck shape is unchanged in [docker-compose.yml](../../docker-compose.yml).

## Failure modes

- Preload errors do not crash the container — they log to `_model_error` and surface via the healthcheck's `model_error` field. The container stays running so operators can `docker exec` in for diagnosis.
- If imagery weights are missing on disk, the container starts but goes unhealthy with a clear `_model_error` describing the missing layer. That's the desired behavior.

# Decision: retry imagery chips across an inference self-heal restart

## Context

When inference-sam3 hits a poisoned CUDA context it self-heals by `os._exit(1)`
so compose respawns it with a clean context, then preloads SAM3 (~100-150 s
total) — see
[why-exit-on-poisoned-cuda-context.md](why-exit-on-poisoned-cuda-context.md).
During that window the imagery worker's chip POSTs to `/detect_raw` fail at the
connection level (TCP refused / DNS failure / remote-disconnected) and, once the
container is back but still preloading, with HTTP 503 (the model bundle isn't
loaded yet — see [why-503-on-unloaded-component.md](why-503-on-unloaded-component.md)).

The old client (`_post_chip_to_sam3_raw` / `_post_chip_to_sam3`) caught *every*
exception and returned `None`, which the chip loop scored as a failed
(zero-detection) chip. So a single ~100 s restart silently dropped the rest of
the scene, and because the "all chips failed" guard only fired when **zero**
chips succeeded, the job still finalized `ready` — reporting a near-empty result
for a scene that was 97% never analysed. Observed in production: a restart at
chip ~5 of 225 left `failed_chips=222, processed_chips=225,
coverage_fraction=1.0, state=success, detections=0`.

## Decision

Make a chip POST ride out a self-heal restart instead of giving up:

- **Classify unavailability vs per-chip error** (`_inference_unavailable`):
  `requests.ConnectionError` (incl. `ConnectTimeout`), `ChunkedEncodingError`,
  and HTTP **502/503/504** mean the *service* is down/restarting/preloading →
  retry. A `ReadTimeout` (one slow forward) or any other 4xx/5xx (a genuine
  per-chip failure, e.g. a 500 on one tile) is **not** retried.
- **Wait for the model, not just the port** (`_wait_for_inference_healthy`):
  `/health` always returns HTTP 200 with a `model_loaded` flag that is False
  while SAM3 preloads, so the wait requires `model_loaded` truthy — otherwise
  the retried POST would just hit 503 again.
- **Bounded retry** (`_post_chip_with_restart_retry`): on an unavailability,
  wait for recovery and retry up to `INFERENCE_RESTART_RETRY_MAX` (default 3)
  times, each waiting up to `INFERENCE_RESTART_WAIT_S` (default 180 s). The
  next `send()` is the source of truth, so a wait timeout just consumes a retry.
- **Fail loudly past tolerance** (the per-pass guard in
  `process_satellite_imagery`): after retries, if more than
  `INFERENCE_MAX_FAILED_CHIP_FRACTION` (default 5%) of attempted chips still
  failed (or all did), raise so the upload finishes `status='failed'` instead of
  `ready`-with-misleading-zero-detections. `inference_success_fraction` is added
  to the summary for honest coverage reporting.

## Consequences

- A transient self-heal restart (or a deploy / occasional OOM-restart) is now
  invisible to the result: chips wait for the respawn and retry, the job
  completes with full coverage.
- A *persistent* fault no longer masquerades as success — it fails the upload
  with a clear error after the retry budget, bounded so it can't loop forever.
- Companion to [why-serialize-forwards-on-a100-cu13x.md](why-serialize-forwards-on-a100-cu13x.md),
  which removes the concurrent-forward poison that caused most restarts in the
  first place; the retry covers any residual restart.

## Cross-references

- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md) — the imagery pipeline + chip client + guard.
- [why-exit-on-poisoned-cuda-context.md](why-exit-on-poisoned-cuda-context.md) — what the worker is riding out.
- [why-503-on-unloaded-component.md](why-503-on-unloaded-component.md) — the preload-window 503 the retry waits through.
- [backend/provider-lifecycle.md](../backend/provider-lifecycle.md) — the existing /health wait this mirrors.

# `backend/provider_lifecycle.py` — Wait-for-Inference

**Path:** [backend/provider_lifecycle.py](../../backend/provider_lifecycle.py)
**Lines:** ~37
**Depends on:** `requests`, env `INFERENCE_SAM3_URL`, `INFERENCE_READY_TIMEOUT_S`

## Purpose

Block until `inference-sam3` reports healthy. Called from the worker before submitting chips → chips don't fail just because the GPU service is cold-starting.

## Key symbols

- [`_wait_for_health`](../../backend/provider_lifecycle.py#L20) — polls `/health` until 200 or deadline.
- [`ensure_running`](../../backend/provider_lifecycle.py#L35) — public wrapper, timeout from env.

## Why this design

A simple sleep loop suffices — inference startup is bounded (model load + warm-up). Heavyweight orchestration would add complexity for a guarantee compose already provides.

## Cross-references

- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md) — calls `ensure_running()` at task entry
- [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md)

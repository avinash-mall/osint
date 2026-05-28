# Why backend lifespan enqueues a Celery task (not sync seed)

**Decision:** [`auto_enqueue_reference_seed_if_empty()`](../../backend/platform_schema.py) called from the FastAPI lifespan enqueues `worker.seed_reference_db` via `celery_app.send_task(...)` when `reference_platforms` has zero rows. The bake runs asynchronously on the worker, not synchronously inside the API process.

This is the **first place** in the codebase where lifespan enqueues a task rather than performing the work itself. The neighbouring [`auto_seed_ontology_if_empty()`](../../backend/platform_schema.py) runs synchronously because the ontology seed is a fast (~hundreds of ms) JSON-to-DB load.

**Date:** 2026-05-27.

## Why not sync (the established pattern)

`auto_seed_ontology_if_empty()` runs inside the lifespan because:
1. It's fast (single JSON file → ~100 ontology objects via INSERT).
2. It needs no external services beyond PostGIS, which is already a `service_healthy` dependency.

The reference-DB seed has neither property:
1. Per-chip embedding via inference-sam3 `/embed` takes ~50–200 ms. For 30 chips that's a few seconds; for 30,000 it's many minutes. The full corpus, baked at full chip-per-class caps, can take **hours**.
2. inference-sam3 is itself a heavy GPU service. Backend already depends on it via `service_healthy`, but waiting for it during lifespan is fine; running embedding loops there is not.

Blocking the lifespan would mean:
- `/api/health` never reports `healthy:true` until the bake finished.
- The nginx healthcheck (which depends transitively) blocks the entire stack from reporting ready.
- Any partial-progress recovery (worker crash mid-bake) loses the per-platform commits because they'd be holding an open lifespan-bound transaction.

## Why this trade-off is safe

- **The Celery task is idempotent.** Re-enqueuing on every lifespan run is fine: `force=False` short-circuits when rows are already present.
- **WS event surface already exists.** `publish_event` and the `reference-seed` topic stream progress; the admin UI subscribes via `useEventStream` and renders a progress card. Users see what's happening even though it's async.
- **Both auto and manual paths share the same task.** Admin "Re-seed" button hits `POST /api/admin/reference/seed`, which enqueues the same `worker.seed_reference_db` task. One execution path; no risk of drift between auto and manual seeding.

## When to use this pattern vs. the sync pattern

- **Sync seed (ontology-style):** Fast (<1s), pure DB writes, no external service calls.
- **Async enqueue (reference-DB-style):** Slow (>1s) OR depends on a separate service OR could span minutes.

Future "seed at lifespan" features should pick by this rule. Don't promote a sync seed to async unless the cost justifies it; don't try to keep an async-worthy task sync just to match the existing pattern.

## How to apply

- Lifespan caller: [`backend/main.py`](../../backend/main.py) — after `_auto_seed_ontology_if_empty()`, call `_auto_enqueue_reference_seed_if_empty()`.
- Helper: [`backend/platform_schema.py`](../../backend/platform_schema.py) — counts `reference_platforms`, gates on `REFERENCE_DB_AUTO_SEED` env, calls `celery_app.send_task("worker.seed_reference_db", kwargs={"force": False})`.
- Task: [`backend/worker_legacy.py`](../../backend/worker_legacy.py) — `worker.seed_reference_db` with `bind=True` so `self.request.id` flows into WS events.
- Disable: `REFERENCE_DB_AUTO_SEED=0` env on backend.

## Failure modes

- **Worker not connected yet** when lifespan tries to enqueue. Celery's `send_task` writes to Redis directly; the worker picks up when it's ready. The `worker → redis (service_healthy)` compose dep + the worker healthcheck (`celery inspect ping`) make "worker missing" a startup-time impossibility on a normally-configured stack.
- **Bake fails per-dataset.** The task loop catches per-dataset exceptions and emits `{"type":"error", "dataset":"..."}` events but continues with the next. Partial population is preserved.
- **Worker crashes mid-bake.** Each dataset's bake commits incrementally per chip. A crash mid-loop leaves a partial set; the next auto-seed run with `force=False` sees rows present and short-circuits. Operator can `force=true` via the admin endpoint to complete.

## Cross-references

- [why-bake-reference-corpora-into-assets.md](why-bake-reference-corpora-into-assets.md) — where the corpora the task consumes come from.
- [why-pgvector-for-reference-db.md](why-pgvector-for-reference-db.md) — the target schema.
- Frontend listener: [`frontend/src/components/admin/ReferencePlatformsView.tsx`](../../frontend/src/components/admin/ReferencePlatformsView.tsx) — `useEventStream("reference-seed")`.

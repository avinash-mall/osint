**Decision:** The auto-identify lookup (matching new detections against the Reference Embedding DB) runs in the backend worker, NOT in `inference-sam3`. The original parent spec anticipated a `reference_identifier.py` module inside `inference-sam3` that would open its own read-only Postgres connection at lifespan startup. Plan C reverses that.

## Why
- **Plan B's clean separation, preserved.** [`why-standalone-embed-endpoint.md`](why-standalone-embed-endpoint.md) decided the inference container should NOT carry psycopg2 or DB credentials. Putting the auto-identify lookup in inference-sam3 would have re-introduced exactly that coupling.
- **Failure-mode isolation.** A DB outage today does not break `/detect` — embedding extraction is pure GPU work. If the lookup lived inside `/detect`, a slow or unreachable Postgres would cascade into detection latency / errors. Keeping it on the backend side means a DB outage only affects auto-identify enrichment; detections continue to land.
- **Reuses the pool-level pgvector adapter** added in Plan B (`_VectorAwareConnection` in `backend/database.py`). The worker already has a vector-aware pooled connection; inference-sam3 would have needed its own.
- **The required embedding is already on the detection.** `inference-sam3` attaches `det["embedding"]` (DINOv3-SAT, 1024-d fp16_b64) to every detection it returns. The worker decodes it via the existing `_parse_embedding_anchor` helper and queries pgvector directly. No second model call needed.

## What we rejected
- **`inference-sam3/reference_identifier.py` with its own psycopg2 connection.** Would have meant: pinning psycopg2 in the GPU image, threading DB credentials into a service that has no need to write rows, adding another failure-mode coupling. Net negative.
- **A new `POST /api/internal/reference/identify` HTTP endpoint.** Pure plumbing — the worker is the natural caller and already holds the cursor, so an HTTP hop adds latency and another round-trip for no benefit.

## Consequences
- The auto-identify call lives at `backend/worker_legacy.py` immediately after the detection INSERT (around line 2596) inside a `SAVEPOINT auto_identify` so helper failures don't poison the batch transaction.
- A blanket `except Exception → logger.warning` wraps the call: identification is best-effort and MUST NOT break detection persistence.
- The threshold (`REFERENCE_ID_AUTO_THRESHOLD`, default 0.85) is read from env at backend startup.
- Future Plan D (analyst-side `/api/detections/{id}/identify`) lives in the backend routers, NOT inference-sam3, for the same reasons.

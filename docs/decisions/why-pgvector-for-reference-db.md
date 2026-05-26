**Decision:** Use `pgvector` with HNSW indexes inside the existing PostGIS container as the vector index for the Reference Embedding Vector Database. Do not add `faiss-cpu`, `hnswlib`, `usearch`, `qdrant`, or any external vector store.

## Why
- **Co-location with detections.** The reference DB is queried whenever a detection is persisted (auto-identify) and on demand from the SelectionPanel (analyst lookup). Both call sites already hold a Postgres connection. Keeping vectors in Postgres means one round-trip per query and one consistent transaction boundary.
- **No new dependency in inference-sam3.** The inference container is GPU-heavy and already pinned to specific PyTorch / CUDA versions. Adding `faiss-cpu` or `hnswlib` widens that surface for no gain — the inference service can issue a `psycopg2` query just like the backend does.
- **HNSW handles our scale.** Phase-1 chip count is in the tens of thousands; full corpus (with xView + DVIDS/Wikimedia/NARA) is bounded around the 500 k mark. pgvector's HNSW comfortably handles this with per-query latency well below the 15 ms budget set in the parent spec.
- **Airgap compatibility.** pgvector ships as a Debian package (`postgresql-18-pgvector`) from the PGDG apt repo that the upstream `postgis/postgis` image already configures. No HTTP-time downloads, no model files, no licensing surprises.
- **Schema lives next to detections.** When pruning or rebuilding, `pg_dump` of the reference tables is one command; no separate "index file" to keep in sync.

## What we rejected
- **Pure-Python cosine over JSONB** (current approach for detection-vs-detection similar). Fine for ≤ 5 k rows. With 50 k–500 k reference vectors per query, the O(N) scan would dominate the detection write path. The parent spec's 15 ms latency target rules this out.
- **FAISS in inference-sam3.** Heavy native dep, large image footprint, ABI sensitivity. Wins nothing over pgvector at our scale.
- **External vector store (Qdrant, Weaviate).** Adds a service to the compose stack, a network hop, a separate persistence story, and (for most options) telemetry phone-home behaviours that violate the airgap rule.

## Consequences
- Postgres container becomes a small derived image ([postgis/Dockerfile](../../postgis/Dockerfile)).
- Backend gains a `pgvector` Python dependency for adapter registration.
- Reference embeddings are dumped/restored via standard `pg_dump`, simplifying the airgap bundle workflow.

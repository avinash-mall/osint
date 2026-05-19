# Component Boundaries

## Purpose

Where the cuts are drawn between processes, files, and networks — and why each cut exists.

## Cuts

### 1. backend ↔ worker (in-process Celery routing)

Two services share the same Docker image (`sentinel-backend:latest`) but run different commands. The backend serves FastAPI and publishes Celery tasks; the worker consumes them. Boundary is **Redis** (broker) and **the explicit task name** declared in `@celery_app.task(name="worker.xxx")`.

- **Code shared:** `backend/database.py`, `backend/ontology.py`, `backend/schemas.py`, and most helper modules import freely.
- **Code not shared:** the worker reads PostGIS/Neo4j directly; it does **not** make HTTP calls back into the backend. The backend never imports `worker_legacy`'s task bodies — only the `celery_app` and `.delay(...)` interface.
- **Why:** lets the worker run on a separate host (GPU box) without dragging the API process along, and lets the API stay responsive under long-running ingest.

### 2. backend ↔ inference-sam3 (HTTP)

Inference runs in its own container with its own image (`sentinel-inference-sam3:gpu`). The backend (and worker) talk to it over HTTP at `${INFERENCE_SAM3_URL}` (default `http://inference-sam3:8001`).

- **Why a separate process:** SAM 3's GPU memory cannot be freed without process restart. `/unload` literally re-execs the container. Keeping inference out of the backend means the FastAPI process stays up across model swaps.
- **Why HTTP not gRPC:** the per-chip payload is dominated by the encoded image (~250 KB PNG), not RPC framing. HTTP is simpler to debug and proxy.

### 3. frontend ↔ backend (single nginx gateway)

The browser never talks to the backend directly. Every call goes through `nginx:3000` which proxies `/api/*` and `/ws` to the backend, `/tiles/` to titiler, `/maps/` to martin, `/basemap/` and `/assets/` to the offline asset image, and `/fmv/` to HLS segments on disk.

- **Why:** single TLS termination point, single origin for the SPA (no CORS in production), one 24h tile cache.

### 4. PostGIS ↔ Neo4j

PostGIS owns: detections (with mask RLE and embedding), satellite passes, FMV clips/frames/detections, ontology branches/objects/prompts, auth_config, feed_sources, observations, reports, calibration.

Neo4j owns: entity graph — Targets, Assets, Observations, Satellites, Bases, LaunchPoints, and edges between them.

The two databases are not synchronized. A detection lives in PostGIS only; its **resolution** to a Target creates a Neo4j edge. See [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md).

### 5. inference profile pool

Within `inference-sam3`, the loaded model set is one of `imagery`, `fmv`, or `all`. Profiles are selected via `POST /load?profile=<name>`. Switching requires `/unload` (process restart) when crossing into a set that needs SAM 3 to drop. See [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md).

### 6. ontology cache

Inference pulls prompts from `${ONTOLOGY_BACKEND_URL}/api/ontology/default-prompts?sensor=` every 30 s. SIGHUP to the inference process forces immediate refresh. The backend bumps an ontology `version` cursor on every edit; clients watch `/api/ontology/version` to invalidate their own caches.

## Cross-references

- [system-overview.md](system-overview.md)
- [service-topology.md](service-topology.md)
- [backend/worker-package-facade.md](../backend/worker-package-facade.md)
- [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md)

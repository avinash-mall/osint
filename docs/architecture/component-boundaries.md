# Component Boundaries

## Purpose

Where cuts are drawn between processes, files, networks — and why.

## Cuts

### 1. backend ↔ worker (in-process Celery routing)

Two services, same image (`sentinel-backend:latest`), different commands. Backend serves FastAPI + publishes Celery tasks; worker consumes. Boundary = **Redis** (broker) + **explicit task name** in `@celery_app.task(name="worker.xxx")`.

- **Shared code:** `backend/database.py`, `backend/ontology.py`, `backend/schemas.py`, most helpers import freely.
- **Not shared:** worker reads PostGIS/Neo4j directly — no HTTP calls back into backend. Backend never imports `worker_legacy` task bodies — only `celery_app` + `.delay(...)`.
- **Why:** worker can run on a separate GPU host without the API process; API stays responsive under long ingest.

### 2. backend ↔ inference-sam3 (HTTP)

Inference = own container, own image (`sentinel-inference-sam3:gpu`). Backend + worker talk HTTP at `${INFERENCE_SAM3_URL}` (default `http://inference-sam3:8001`).

- **Why separate process:** SAM 3 GPU memory cannot free without process restart; `/unload` re-execs the container. Keeps FastAPI up across model swaps.
- **Why HTTP not gRPC:** per-chip payload dominated by encoded image (~250 KB PNG), not RPC framing. HTTP simpler to debug/proxy.

### 3. frontend ↔ backend (single nginx gateway)

Browser never talks to backend directly. All calls via `nginx:3000`: `/api/*` + `/ws` → backend, `/tiles/` → titiler, `/maps/` → martin, `/basemap/` + `/assets/` → offline asset image, `/fmv/` → HLS segments on disk.

- **Why:** single TLS termination, single SPA origin (no CORS in prod), one 24h tile cache.

### 4. PostGIS ↔ Neo4j

- **PostGIS owns:** detections (mask RLE + embedding), satellite passes, FMV clips/frames/detections, ontology branches/objects/prompts, auth_config, feed_sources, observations, reports, calibration.
- **Neo4j owns:** entity graph — Targets, Assets, Observations, Satellites, Bases, LaunchPoints + edges.

Not synchronized. A detection lives in PostGIS only; its **resolution** to a Target creates a Neo4j edge. See [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md).

### 5. inference profile pool

Within `inference-sam3`, loaded model set ∈ `imagery` | `fmv` | `all`. Selected via `POST /load?profile=<name>`. Switching needs `/unload` (process restart) when crossing into a set requiring SAM 3 to drop. See [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md).

### 6. ontology cache

Inference pulls prompts from `${ONTOLOGY_BACKEND_URL}/api/ontology/default-prompts?sensor=` every 30 s. SIGHUP forces immediate refresh. Backend bumps ontology `version` cursor on every edit; clients watch `/api/ontology/version` to invalidate caches.

## Cross-references

- [system-overview.md](system-overview.md)
- [service-topology.md](service-topology.md)
- [backend/worker-package-facade.md](../backend/worker-package-facade.md)
- [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md)

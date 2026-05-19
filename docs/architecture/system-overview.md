# System Overview

**Source:** [docker-compose.yml](../../docker-compose.yml) · [nginx/](../../nginx/) · [backend/main.py](../../backend/main.py)

## Purpose

Sentinel is a self-contained GEOINT platform. A single host port (`3000`) terminates all client traffic; every other component runs on the internal compose network. This doc names what runs where and how requests flow.

## Topology

```
                              ┌─────────────────────────────────────┐
                              │  nginx :3000  (sentinel-nginx)      │
                              │  TLS termination + 24h tile cache   │
                              └─────────────────────────────────────┘
                       /  /api  /ws  /tiles  /maps  /basemap  /fmv  /assets
                       │
        ┌──────────────┼───────────────┬──────────────┬──────────────┐
        │              │               │              │              │
   ┌─────────┐  ┌──────────────┐  ┌─────────┐  ┌────────────┐  ┌────────┐
   │frontend │  │   backend    │  │ titiler │  │   martin   │  │ assets │
   │ React19 │  │ FastAPI :8080│  │ COG/2.0 │  │ MVT/1.9    │  │ Carto  │
   └─────────┘  └──────┬───────┘  └─────────┘  └────────────┘  └────────┘
                       │
            ┌──────────┼──────────────┐
            │          │              │
        ┌───────┐  ┌────────┐  ┌──────────────────┐
        │ Neo4j │  │ PostGIS│  │ inference-sam3   │
        │ 5.26  │  │ 18-3.6 │  │ FastAPI :8001    │
        └───────┘  └────────┘  │ SAM3+SAM3.1+...  │
                               └──────────────────┘
            ┌──────────────────┐
            │   Redis 8        │  ← Celery broker
            └──────────────────┘
                ▲           ▲
                │           │
        ┌────────────┐  ┌────────────────┐
        │   worker   │  │  worker_beat   │
        │ Celery     │  │  Celery beat   │
        │ imagery,   │  │  schedule      │
        │ default    │  │                │
        └────────────┘  └────────────────┘
```

See [service-topology.md](service-topology.md) for the per-service compose reference, and [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md) for the route table inside nginx.

## Why this design

- **Single exposed port** simplifies air-gap deployment (no per-service port matrix to firewall) and centralizes the tile cache and HLS-streaming code path.
- **Inference is a separate process** because SAM 3 weights cannot be freed without process restart — see [decisions/disable-addmm-cuda-lt.md](../decisions/disable-addmm-cuda-lt.md) and [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md). Isolating it lets the backend stay alive across model reloads.
- **Two databases** because spatial joins and entity graphs have different access patterns. See [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md).
- **Celery worker is separated from the API process** so long-running ingest jobs cannot block request latency. The legacy monolith file `worker_legacy.py` is preserved as-is — see [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md).

## Data flows

- **Imagery ingest:** [data-flow-imagery.md](data-flow-imagery.md)
- **FMV ingest:** [data-flow-fmv.md](data-flow-fmv.md)
- **Process boundaries:** [component-boundaries.md](component-boundaries.md)

## Cross-references

- [backend/main-app-entrypoint.md](../backend/main-app-entrypoint.md) — what `backend/main.py` mounts and in what order.
- [inference/service-overview.md](../inference/service-overview.md) — the SAM3 service surface.
- [deployment/docker-compose-services.md](../deployment/docker-compose-services.md) — per-service compose reference.

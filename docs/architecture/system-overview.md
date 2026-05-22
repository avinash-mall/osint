# System Overview

**Source:** [docker-compose.yml](../../docker-compose.yml) · [nginx/](../../nginx/) · [backend/main.py](../../backend/main.py)

## Purpose

Self-contained GEOINT platform. Single host port `3000` terminates all client traffic; every other component on the internal compose network. This doc: what runs where, how requests flow.

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

Per-service compose reference: [service-topology.md](service-topology.md). nginx route table: [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md).

## Why this design

- **Single exposed port** — simpler air-gap (no per-service port matrix to firewall); centralizes tile cache + HLS path.
- **Inference = separate process** — SAM 3 weights cannot free without process restart; isolating it keeps backend alive across model reloads. See [decisions/disable-addmm-cuda-lt.md](../decisions/disable-addmm-cuda-lt.md), [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md).
- **Two databases** — spatial joins vs entity graphs = different access patterns. See [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md).
- **Celery worker separate from API** — long ingest jobs cannot block request latency. `worker_legacy.py` monolith preserved as-is — see [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md).

## Data flows

- Imagery ingest: [data-flow-imagery.md](data-flow-imagery.md)
- FMV ingest: [data-flow-fmv.md](data-flow-fmv.md)
- Process boundaries: [component-boundaries.md](component-boundaries.md)

## Cross-references

- [backend/main-app-entrypoint.md](../backend/main-app-entrypoint.md) — what `backend/main.py` mounts, in what order.
- [inference/service-overview.md](../inference/service-overview.md) — SAM3 service surface.
- [deployment/docker-compose-services.md](../deployment/docker-compose-services.md) — per-service compose reference.

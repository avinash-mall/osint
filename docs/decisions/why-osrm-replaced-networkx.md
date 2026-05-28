# Why OSRM replaced the pickled networkx routing graph

**Decision date:** 2026-05-28
**Status:** active
**Replaces:** the `${ROUTING_GRAPH_PATH}` pickled `networkx` design described in the now-deleted `routing-graph-osmnx.md`.

## Context

`backend/routing.py` originally loaded a pickled `networkx` MultiDiGraph (built offline via `osmnx.graph_from_bbox(...)` → `pickle.dump`) into the FastAPI process at first use and ran `nx.shortest_path` against it for every `/api/analytics/routes` call. That worked while the graph was a small AOI (e.g. a single city, ~100k nodes / ~30 MB pickle) but the platform's stated requirement is worldwide coverage for defence analysts: any point to any point, fully air-gapped.

A planet-scale OSM extract is ~80 GB compressed and the corresponding `networkx` graph would be multi-terabyte once unpickled. Even a "major roads only" planet filter is ~50 GB pickled, takes minutes to load, and pins a worker process to it. That is incompatible with the pre-fork Uvicorn model the backend runs under.

## Decision

The routing backend was rewritten as a thin HTTP client against the upstream OSRM service (`ghcr.io/project-osrm/osrm-backend:v6.0.0`, Apache-2.0) running as a sidecar container. The planet OSRM dataset is pre-built once on a connected host via the `osrm-baker` Compose profile and lives on the `osrm_data` named volume; the `osrm` service then serves it through the standard OSRM HTTP API on port 5000.

`backend/routing.py` is now a ~170-line module:

- `osrm_available()` — cached probe, 5 s TTL.
- `compute_routes(...)` — `GET /route/v1/driving/{src};{dst}?alternatives=3&overview=full&geometries=geojson` and maps the response into the same FeatureCollection shape the analytics router previously emitted.

The `networkx` dependency was removed from `backend/requirements.txt`.

## Consequences

**Positive**

- Planet-scale routing works in sub-second time, fully air-gapped after the one-time bake.
- Backend memory footprint drops by the size of the previous graph (effectively unbounded for worldwide).
- Upgrade path is the upstream OSRM image; we own a thin client, not a routing algorithm.

**Negative / accepted trade-offs**

- The historical `strategy` parameter (`shortest` / `balanced` / `least_exposure`) is now API-compat-only — OSRM does not weight by per-edge exposure out of the box. All three OSRM alternatives are surfaced regardless. Re-implementing exposure-aware routing requires baking a custom Lua profile into the planet OSRM build (rebuild of the full extract). Tracked as a follow-up; documented in [backend/routing-osrm.md](../backend/routing-osrm.md).
- The `routing` capability is now a network probe rather than a file check. The cache keeps the cost negligible.
- Car profile only for the first cut. Foot / bicycle can be added later as additional OSRM container instances.

## Related

- [backend/routing-osrm.md](../backend/routing-osrm.md) — current module reference
- [deployment/osrm-planet-bake.md](../deployment/osrm-planet-bake.md) — operator runbook
- [decisions/why-glo30-as-default-dem.md](why-glo30-as-default-dem.md) — sibling decision for the DEM

# `backend/routing.py` — OSRM HTTP Client

**Path:** [backend/routing.py](../../backend/routing.py)
**Lines:** ~170
**Depends on:** `requests`, an OSRM sidecar at `${OSRM_URL:-http://osrm:5000}`

## Purpose

Compute up to three driving routes between observer and destination by calling the OSRM service over HTTP. Surfaces as `POST /api/analytics/routes`.

## Why this design

- **OSRM, not networkx.** The previous design loaded a pickled `networkx` graph entirely into memory. That worked at AOI scale but is multi-terabyte for planet coverage. OSRM streams a Multi-Level Dijkstra (MLD) dataset off disk and answers planet-scale queries in sub-second time. See [decisions/why-osrm-replaced-networkx.md](../decisions/why-osrm-replaced-networkx.md).
- **Sidecar service.** OSRM runs as a separate container (`osrm` in docker-compose) and is air-gappable once the planet has been baked. The backend only owns the HTTP client.
- **Cheap availability probe.** `osrm_available()` hits `/route/v1/driving/0,0;0.001,0.001` with a 1.5 s timeout and caches the result for 5 s. Keeps `/api/analytics/capabilities` polling cheap.
- **Strategy parameter is API-compat-only.** OSRM does not natively weight by exposure. We accept `strategy=` (`shortest` / `balanced` / `least_exposure`) and pass it through to the response `properties.strategy`, but all three alternatives are surfaced regardless. Exposure-aware routing would need a custom Lua profile baked into the planet OSRM build; tracked as a follow-up.

## Key symbols

- [`osrm_url`](../../backend/routing.py#L33), [`osrm_available`](../../backend/routing.py#L45) — cached HTTP probe.
- [`reset_osrm_health_cache`](../../backend/routing.py#L66) — for unit tests / forced re-probe.
- [`compute_routes`](../../backend/routing.py#L88) — main entry; emits a list of FeatureCollection-style Features.
- [`_risk_label`](../../backend/routing.py#L78) — option-index → `primary` / `alternative N`.

## Inputs / Outputs

**Inputs:** observer + destination lat/lon, optional `strategy` (compat-only).

**Outputs:** list of GeoJSON Features with `properties.option`, `length_m`, `duration_minutes`, `exposure` (always `0.0`), `risk`, `strategy`, `label`, `mode: "osrm"`.

## Failure modes

- OSRM unreachable → `osrm_available()` returns False; analytics router emits 503 `Routes unavailable: OSRM service is not reachable.` unless `ANALYTICS_ALLOW_FIXTURES=1`.
- OSRM returns `code != "Ok"` (NoRoute / NoSegment / InvalidQuery) → `compute_routes` returns `None`; router emits 422.
- HTTP timeout (15 s) → `compute_routes` returns `None`.

## Cross-references

- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [decisions/why-osrm-replaced-networkx.md](../decisions/why-osrm-replaced-networkx.md)
- [deployment/osrm-planet-bake.md](../deployment/osrm-planet-bake.md)
- [deployment/volume-mounts-and-paths.md](../deployment/volume-mounts-and-paths.md)
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)

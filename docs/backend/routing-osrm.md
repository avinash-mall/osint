# `backend/routing.py` — OSRM HTTP Client

**Path:** [backend/routing.py](../../backend/routing.py)
**Lines:** ~284
**Depends on:** `requests`, `math`, an OSRM sidecar at `${OSRM_URL:-http://osrm:5000}`

## Purpose

Compute up to three driving routes between observer and destination (`POST /api/analytics/routes`) and a driving-time reachability polygon around a point (`POST /api/analytics/isochrone`) by calling the OSRM service over HTTP.

## Why this design

- **OSRM, not networkx.** The previous design loaded a pickled `networkx` graph entirely into memory. That worked at AOI scale but is multi-terabyte for planet coverage. OSRM streams a Multi-Level Dijkstra (MLD) dataset off disk and answers planet-scale queries in sub-second time. See [decisions/why-osrm-replaced-networkx.md](../decisions/why-osrm-replaced-networkx.md).
- **Sidecar service.** OSRM runs as a separate container (`osrm` in docker-compose) and is air-gappable once the planet has been baked. The backend only owns the HTTP client.
- **Cheap availability probe.** `osrm_available()` hits `/route/v1/driving/0,0;0.001,0.001` with a 1.5 s timeout and caches the result for 5 s. Keeps `/api/analytics/capabilities` polling cheap.
- **Strategy parameter is API-compat-only.** OSRM does not natively weight by exposure. We accept `strategy=` (`shortest` / `balanced` / `least_exposure`) and pass it through to the response `properties.strategy`, but all three alternatives are surfaced regardless. Exposure-aware routing would need a custom Lua profile baked into the planet OSRM build; tracked as a follow-up.
- **Isochrones are a probe-and-matrix approximation.** `compute_isochrone` fires `ISO_BEARINGS=16` × `ISO_RINGS=6` probes outward, asks OSRM `/table` for the driving duration to each in one request, and keeps the farthest reachable probe per bearing — a 16-spoke star polygon. The probe count (97 + source) stays under OSRM's default `--max-table-size 100`. No new service; reuses the routing sidecar. See [decisions/why-isochrone-reachability.md](../decisions/why-isochrone-reachability.md).

## Key symbols

- [`osrm_url`](../../backend/routing.py#L33), [`osrm_available`](../../backend/routing.py#L45) — cached HTTP probe.
- [`reset_osrm_health_cache`](../../backend/routing.py#L66) — for unit tests / forced re-probe.
- [`compute_routes`](../../backend/routing.py#L88) — main entry; emits a list of FeatureCollection-style Features.
- [`_risk_label`](../../backend/routing.py#L78) — option-index → `primary` / `alternative N`.
- [`compute_isochrone(center_lat, center_lon, minutes, nominal_speed_kmh)`](../../backend/routing.py#L190-L284) — single-Polygon reachability FeatureCollection via the OSRM `/table` matrix; `None` when OSRM is unreachable or fewer than 3 spokes are reachable.
- [`_destination_point(lat, lon, bearing_deg, distance_m)`](../../backend/routing.py#L176-L187) — great-circle forward used to place isochrone probes.
- [`EARTH_RADIUS_M`](../../backend/routing.py#L169), [`ISO_BEARINGS`](../../backend/routing.py#L172), [`ISO_RINGS`](../../backend/routing.py#L173) — probe-grid constants.

## Inputs / Outputs

**Inputs:** observer + destination lat/lon, optional `strategy` (compat-only).

**Outputs:** list of GeoJSON Features with `properties.option`, `length_m`, `duration_minutes`, `exposure` (always `0.0`), `risk`, `strategy`, `label`, `mode: "osrm"`.

## Failure modes

- OSRM unreachable → `osrm_available()` returns False; analytics router emits 503 `Routes unavailable: OSRM service is not reachable.` unless `ANALYTICS_ALLOW_FIXTURES=1`.
- OSRM returns `code != "Ok"` (NoRoute / NoSegment / InvalidQuery) → `compute_routes` returns `None`; router emits 422.
- HTTP timeout (15 s) → `compute_routes` returns `None`.
- Isochrone: OSRM unreachable, `/table` non-200/non-Ok, or fewer than 3 reachable spokes → `compute_isochrone` returns `None`; the router falls back to a fixture only under `ANALYTICS_ALLOW_FIXTURES=1`, else 503. Unreachable spokes collapse to the center so the polygon stays valid.

## Cross-references

- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [decisions/why-osrm-replaced-networkx.md](../decisions/why-osrm-replaced-networkx.md)
- [decisions/why-isochrone-reachability.md](../decisions/why-isochrone-reachability.md)
- [deployment/osrm-planet-bake.md](../deployment/osrm-planet-bake.md)
- [deployment/volume-mounts-and-paths.md](../deployment/volume-mounts-and-paths.md)
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)

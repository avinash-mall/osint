# Why driving-time isochrones via the OSRM matrix

**Decision date:** 2026-06-10
**Status:** active

## Context

Routing (`/api/analytics/routes`) answers "how do I get from A to B?" but analysts also ask the reachability question: "what area can be reached from this point within N minutes by road?" — a driving-time isochrone. Sentinel already runs an OSRM sidecar for planet-scale routing ([decisions/why-osrm-replaced-networkx.md](why-osrm-replaced-networkx.md)), and OSRM exposes a `/table` duration-matrix endpoint that can answer this without any new service.

## Decision

Add [`compute_isochrone`](../../backend/routing.py) to the existing OSRM client and surface it as `POST /api/analytics/isochrone`. It fires a ring of probe points outward along 16 bearings × 6 radii, asks OSRM `/table` for the driving duration from the center to every probe in **one** request, and for each bearing keeps the farthest probe reachable within the time budget. Connecting those per-bearing extremes yields a star-shaped reachable polygon.

Why a probe-and-matrix approximation rather than a true road-network flood fill: OSRM's contracted MLD dataset does not expose per-edge traversal for an isodistance flood, and shelling out a custom isochrone build would mean baking a new profile into the planet extract. The 16×6 probe grid stays under OSRM's default `--max-table-size 100` (97 probes + source) and gives an analyst-useful reachable shape from infrastructure we already run, fully offline.

Honesty about availability follows the established pattern: returns `None`/503 when OSRM is unreachable (or a fixture only under `ANALYTICS_ALLOW_FIXTURES=1`), exactly like routes / viewshed.

## Consequences

**Positive**
- Reachability analysis with no new service — reuses the OSRM sidecar and its offline planet bake.
- One `/table` request per isochrone keeps it cheap.

**Negative / accepted**
- The polygon is a 16-spoke star approximation, not a true road-accurate isoline; coarse at small time budgets.
- Probe count is capped by OSRM's table-size limit, bounding angular/radial resolution.

## Related

- [backend/routing-osrm.md](../backend/routing-osrm.md) — module reference (`compute_isochrone`)
- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md) — `POST /api/analytics/isochrone`
- [decisions/why-osrm-replaced-networkx.md](why-osrm-replaced-networkx.md) — the OSRM sidecar this builds on
- [frontend/map-analytics-tools.md](../frontend/map-analytics-tools.md) — Isochrone tool

# Analytics Tools Panel

**Path:** [frontend/src/components/map/AnalyticsToolsPanel.tsx](../../frontend/src/components/map/AnalyticsToolsPanel.tsx)
**Lines:** ~630

## Purpose

Operator-driven spatial analysis: viewshed, line-of-sight, routing, isochrone reachability, OD flows, change detection, patterns-of-life. Each tool has its own form (observer height, target point, route strategy, time budget, etc.), calls one of the `/api/analytics/*` endpoints.

## Tools

| Tool | Backend |
|---|---|
| Viewshed (DEM) | `POST /api/analytics/viewshed` |
| Line-of-sight (DEM) | `POST /api/analytics/los` |
| Routes (OSRM sidecar) | `POST /api/analytics/routes` |
| Isochrone (OSRM sidecar) | `POST /api/analytics/isochrone` — pick an observer (`isochrone.observer` pick), a time budget (minutes) + nominal speed; renders a reachability polygon |
| OD Flows | `POST /api/analytics/od-flows` — aggregates `track_points` into weighted movement-corridor LineStrings |
| Patterns-of-life | `POST /api/analytics/pol` |
| Change detection | `POST /api/analytics/change` or [ChangeDetectionDialog.tsx](map-change-detection-dialog.md) |

`AnalyticsKind` is `'viewshed' | 'los' | 'routes' | 'isochrone' | 'odflows'`; the two new ToolCards carry `data-tour="analytics-isochrone"` / `data-tour="analytics-odflows"` anchors. The isochrone-result layer is a Polygon and the odflows layer is a set of LineStrings — both are wired through [GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx) → [MapStage.tsx](../../frontend/src/components/map/MapStage.tsx) → [LayerPanel.tsx](../../frontend/src/components/map/LayerPanel.tsx) / [SelectionPanel.tsx](../../frontend/src/components/map/SelectionPanel.tsx) as two new analytics layer kinds.

## Capabilities-aware buttons

On open, the panel calls `GET /api/analytics/capabilities` to learn whether the DEM mosaic and OSRM sidecar are reachable. The bottom-of-panel chip surfaces `DEM · OK/NONE` and `ROUTING · OK/NONE`. When a tool's underlying capability is missing, the corresponding tool returns a 503 and the panel surfaces the backend `detail` string verbatim in the tool's error row.

## Cross-references

- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md)
- [backend/routing-osrm.md](../backend/routing-osrm.md) — isochrone `compute_isochrone`
- [backend/od-flows.md](../backend/od-flows.md) — OD flow graph builder
- [decisions/why-isochrone-reachability.md](../decisions/why-isochrone-reachability.md), [decisions/why-od-flow-graphs.md](../decisions/why-od-flow-graphs.md)
- [frontend/services-analytics.md](services-analytics.md) — the API client this UI calls into (`runIsochrone`, `runODFlows`)
- [frontend/product-tour.md](product-tour.md) — `analytics-isochrone` / `analytics-odflows` tour steps

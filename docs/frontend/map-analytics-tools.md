# Analytics Tools Panel

**Path:** [frontend/src/components/map/AnalyticsToolsPanel.tsx](../../frontend/src/components/map/AnalyticsToolsPanel.tsx)
**Lines:** ~16233 characters

## Purpose

Operator-driven spatial analysis: viewshed, line-of-sight, routing, change detection, patterns-of-life. Each tool has its own form (observer height, target point, route strategy, etc.), calls one of the `/api/analytics/*` endpoints.

## Tools

| Tool | Backend |
|---|---|
| Viewshed (DEM) | `POST /api/analytics/viewshed` |
| Line-of-sight (DEM) | `POST /api/analytics/los` |
| Routes (OSRM sidecar) | `POST /api/analytics/routes` |
| Patterns-of-life | `POST /api/analytics/pol` |
| Change detection | `POST /api/analytics/change` or [ChangeDetectionDialog.tsx](map-change-detection-dialog.md) |

## Capabilities-aware buttons

On open, the panel calls `GET /api/analytics/capabilities` to learn whether the DEM mosaic and OSRM sidecar are reachable. The bottom-of-panel chip surfaces `DEM · OK/NONE` and `ROUTING · OK/NONE`. When a tool's underlying capability is missing, the corresponding tool returns a 503 and the panel surfaces the backend `detail` string verbatim in the tool's error row.

## Cross-references

- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md)
- [backend/routing-osrm.md](../backend/routing-osrm.md)
- [frontend/services-analytics.md](services-analytics.md) — the API client this UI calls into

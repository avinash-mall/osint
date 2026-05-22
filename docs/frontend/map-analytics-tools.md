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
| Routes (osmnx graph) | `POST /api/analytics/routes` |
| Patterns-of-life | `POST /api/analytics/pol` |
| Change detection | `POST /api/analytics/change` or [ChangeDetectionDialog.tsx](map-change-detection-dialog.md) |

## Capabilities-aware buttons

On open, the panel calls `GET /api/analytics/capabilities` to learn whether DEM + routing graph are present. Missing capabilities disable the corresponding buttons, surface a tooltip: "DEM not configured (mode: fixture_no_dem)."

## Cross-references

- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md)
- [backend/routing-graph-osmnx.md](../backend/routing-graph-osmnx.md)
- [frontend/services-analytics.md](services-analytics.md) — the API client this UI calls into

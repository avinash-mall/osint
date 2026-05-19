# Admin — Health Dashboard

**Path:** [frontend/src/components/admin/HealthDashboardView.tsx](../../frontend/src/components/admin/HealthDashboardView.tsx)
**Lines:** ~16440 characters

## Purpose

The "is everything green?" panel. Shows backend + database liveness, inference profile state, per-GPU VRAM, active requests, and aggregated KPIs (p50/p95 latencies per stage).

## What it polls

- `GET /api/health` — basic liveness (2 s interval)
- `GET /api/inference/health` — cached inference health (5 s interval)
- `GET /api/inference/dashboard` — KPI aggregate (5 s interval)

## Why polling, not WebSocket

The data updates rapidly and is point-in-time; missing a frame doesn't matter. Polling is simpler than maintaining a dedicated stream just for the dashboard. Critical alerts still flow over the `health_alert` WS topic.

## Cross-references

- [backend-routers/health-router.md](../backend-routers/health-router.md)
- [backend-routers/inference-router.md](../backend-routers/inference-router.md)
- [inference/sam3-perf-profiling.md](../inference/sam3-perf-profiling.md) — the source of the per-stage timings
- [operations/health-monitoring.md](../operations/health-monitoring.md)

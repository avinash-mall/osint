# Health Router (`/api/health`, `/api/alerts`)

**Path:** [backend/routers/health.py](../../backend/routers/health.py)
**Lines:** ~141
**Depends on:** [backend/database.py](../../backend/database.py), [backend/ai.py](../../backend/ai.py), [backend/detection_policy.py](../../backend/detection_policy.py)

## Purpose

Liveness probes + operator alerts. `/api/health` is what `nginx`'s healthcheck hits; `/api/alerts` powers the Admin → Health Alerts tab.

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET` | `/api/health` | [health.py#L22](../../backend/routers/health.py#L22) | `{neo4j: bool, postgis: bool, llm: bool, detection_policy: str, timestamp}` |
| `GET` | `/api/alerts` | [health.py#L50](../../backend/routers/health.py#L50) | Operator alerts derived from health + failed ingest tasks |

## Why this design

- **Health is a public GET** — no session required; needed for compose healthchecks and external monitoring.
- **Checks each dependency individually**, not all-or-nothing — a degraded LLM shouldn't show the platform as down.
- **Alerts derive, don't store** — `/api/alerts` synthesizes from `/api/health` + recent Celery task failures in PostGIS. No separate `alerts` table to keep in sync.

## Cross-references

- [backend/database-connections.md](../backend/database-connections.md)
- [operations/health-monitoring.md](../operations/health-monitoring.md)
- [frontend/admin-health-dashboard.md](../frontend/admin-health-dashboard.md)
- [frontend/admin-alerts-and-versions.md](../frontend/admin-alerts-and-versions.md)

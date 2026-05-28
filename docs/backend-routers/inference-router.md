# Inference Router (`/api/inference/*`)

**Path:** [backend/routers/inference.py](../../backend/routers/inference.py)
**Lines:** ~276
**Depends on:** [backend/auth.py](../../backend/auth.py) (`require_admin`), [backend/detection_policy.py](../../backend/detection_policy.py), [backend/schemas.py](../../backend/schemas.py)

## Purpose

Operator surface for the [inference-sam3](../inference/service-overview.md) service. All endpoints are proxies — backend never imports an ML library; just forwards to `${INFERENCE_SAM3_URL}` (default `http://inference-sam3:8001`).

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `POST` | `/api/inference/load` | [inference.py#L27](../../backend/routers/inference.py#L27) | Proxy `POST /load?profile=imagery|fmv|all`; admin session required |
| `POST` | `/api/inference/unload` | [inference.py#L43](../../backend/routers/inference.py#L43) | Proxy `POST /unload` (container restart); admin session required |
| `GET` | `/api/inference/health` | [inference.py#L70](../../backend/routers/inference.py#L70) | Cached `/health` from inference (5 s TTL) |
| `GET` | `/api/inference/confidence-overrides` | [inference.py#L94](../../backend/routers/inference.py#L94) | Per-class confidence floors |
| `PUT` | `/api/inference/confidence-overrides` | [inference.py#L109](../../backend/routers/inference.py#L109) | Admin-only update via [`ConfidenceConfig`](../../backend/schemas.py) |
| `GET` | `/api/inference/dashboard` | [inference.py#L229](../../backend/routers/inference.py#L229) | Aggregated KPIs for the Health Dashboard view |

## Why this design

- **Cached `/health`** — Health Dashboard polls every 2-3 s and inference `/health` is non-trivial (probes loaded models + replica list + active requests). 5 s TTL absorbs the polling without staleness.
- **Confidence overrides live in PostGIS**, not env → editable from the UI without restart. Backend writes them, signals inference via SIGHUP / cache invalidation on next request.
- **`/unload` re-execs the container** — see [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md). Endpoint returns immediately; clients poll `/api/inference/health` to wait for the new process.
- **Admin role required for lifecycle mutation** because model load/unload changes memory allocation and service availability for the whole stack.

## Failure modes

- Missing/expired session on lifecycle or confidence writes → 401; non-admin session → 403.
- Inference service unavailable → proxy endpoints surface 503 while `/health` keeps using its short TTL cache.

## Cross-references

- [inference/service-overview.md](../inference/service-overview.md)
- [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md)
- [frontend/admin-conf-overrides.md](../frontend/admin-conf-overrides.md)
- [frontend/admin-health-dashboard.md](../frontend/admin-health-dashboard.md)
- [decisions/why-admin-mutators-require-admin.md](../decisions/why-admin-mutators-require-admin.md)

# Inference Router (`/api/inference/*`)

**Path:** [backend/routers/inference.py](../../backend/routers/inference.py)
**Lines:** ~276
**Depends on:** [backend/auth.py](../../backend/auth.py) (`require_admin`), [backend/detection_policy.py](../../backend/detection_policy.py), [backend/schemas.py](../../backend/schemas.py)

## Purpose

Operator surface for the [inference-sam3](../inference/service-overview.md) service. All endpoints are proxies — the backend never imports any ML library; it just forwards to `${INFERENCE_SAM3_URL}` (default `http://inference-sam3:8001`).

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `POST` | `/api/inference/load` | [inference.py#L26](../../backend/routers/inference.py#L26) | Proxy `POST /load?profile=imagery|fmv|all` |
| `POST` | `/api/inference/unload` | [inference.py#L42](../../backend/routers/inference.py#L42) | Proxy `POST /unload` (container restart) |
| `GET` | `/api/inference/health` | [inference.py#L70](../../backend/routers/inference.py#L70) | Cached `/health` from inference (5-second TTL) |
| `GET` | `/api/inference/confidence-overrides` | [inference.py#L94](../../backend/routers/inference.py#L94) | Per-class confidence floors |
| `PUT` | `/api/inference/confidence-overrides` | [inference.py#L109](../../backend/routers/inference.py#L109) | Admin-only update via [`ConfidenceConfig`](../../backend/schemas.py) |
| `GET` | `/api/inference/dashboard` | [inference.py#L229](../../backend/routers/inference.py#L229) | Aggregated KPIs for the Health Dashboard view |

## Why this design

- **Cached `/health`** because the Health Dashboard polls every 2-3 seconds and the inference `/health` is non-trivial (probes loaded models + replica list + active requests). Five-second TTL is enough to absorb the polling without staleness.
- **Confidence overrides live in PostGIS**, not env, so they can be edited from the UI without restarting. The backend writes them and signals inference with SIGHUP / cache invalidation on the next request.
- **`/unload` re-execs the container** — see [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md). The endpoint returns immediately; clients should poll `/api/inference/health` to wait for the new process.

## Cross-references

- [inference/service-overview.md](../inference/service-overview.md)
- [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md)
- [frontend/admin-conf-overrides.md](../frontend/admin-conf-overrides.md)
- [frontend/admin-health-dashboard.md](../frontend/admin-health-dashboard.md)

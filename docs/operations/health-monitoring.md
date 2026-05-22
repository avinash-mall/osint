# Operations — Health Monitoring

## What to watch

| Surface | Refresh | Source |
|---|---|---|
| **Admin → Health Dashboard** | 2-5 s polling | `/api/inference/dashboard` + `/api/health` |
| **Admin → Health Alerts** | live + 5 s polling | `/api/alerts` + WS `health_alert` |
| **nginx healthcheck** | docker `healthcheck:` interval | `GET /api/health` |

## What `/api/health` returns

```json
{
  "neo4j": true,
  "postgis": true,
  "llm": true,
  "detection_policy": "defence_precision",
  "timestamp": "..."
}
```

Any `false` = a degraded subsystem. The Alerts feed escalates persistent degradation into operator-visible alerts.

## What `/api/inference/dashboard` returns

- Currently loaded profile (`imagery` / `fmv` / `all` / `none`)
- Per-GPU replicas with VRAM / utilization
- Active in-flight requests
- Per-stage p50/p95 latencies (from [inference/sam3-perf-profiling.md](../inference/sam3-perf-profiling.md))
- Recent error counts per stage

## Common alert causes

- **PostGIS or Neo4j unreachable** → ingest fails until restored.
- **Inference profile unloaded** → ingest queues but cannot dispatch. Trigger `/api/inference/load`.
- **VRAM near full** → reduce `SAM3_LOAD_*` flags or unload to recover.
- **Repeated `addmm` errors** → check `DISABLE_ADDMM_CUDA_LT=1` is set on A100/cu130 hosts. See [decisions/disable-addmm-cuda-lt.md](../decisions/disable-addmm-cuda-lt.md).

## Cross-references

- [backend-routers/health-router.md](../backend-routers/health-router.md)
- [backend-routers/inference-router.md](../backend-routers/inference-router.md)
- [frontend/admin-health-dashboard.md](../frontend/admin-health-dashboard.md)
- [frontend/admin-alerts-and-versions.md](../frontend/admin-alerts-and-versions.md)

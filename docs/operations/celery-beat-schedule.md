# Celery Beat Schedule

**Worker:** `worker_beat` service in [docker-compose.yml](../../docker-compose.yml)
**Schedule storage:** [backend/celerybeat-schedule](../../backend/celerybeat-schedule) (binary state file)
**Schedule definition:** `backend/worker_legacy.py`, near the celery_app block

## Periodic tasks

| Task | Cadence | Purpose |
|---|---|---|
| `worker.poll_http_feeds` | 60 s | Pull events from registered feed sources |
| `worker.cleanup_old_observations` | 1 h | Prune `observations` + `timeline_events` older than configured retention |
| `worker.compact_inference_dashboard_metrics` | 5 min | Roll up the inference metrics window |
| `worker.tick_colocation_builder` | 6 h (`COLOCATION_BUILDER_INTERVAL_S`) | Phase 6 — MERGE `COLOCATED_WITH` proximity edges between recent detections ([worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)). Tuned by `COLOCATION_WINDOW_DAYS`, `COLOCATION_MAX_NODES`, `COLOCATION_METHOD`, `COLOCATION_KNN_K`, `COLOCATION_RADIUS_M`. |
| `worker.tick_gnn_link_prediction` | 24 h (`GNN_LINK_PREDICTION_INTERVAL_S`) | Phase 6 — GraphSAGE link prediction → advisory `GNN_SUGGESTED_LINK` edges; no-ops until torch is installed. Tuned by `GNN_LINK_TOP_K`, `GNN_SNAPSHOT_LIMIT`. |

The full live schedule also includes the Phase 4-5 builders (`tick-near-builder`, `tick-repeat-detector`, `tick-entity-resimilarity`, `tick-propose-entities`, …). `grep -A60 "beat_schedule" backend/worker_legacy.py` for the live definition.

## Why a separate `worker_beat` service

Celery beat keeps its own state file (`celerybeat-schedule`). Running it inside a regular worker → every restart could drop or re-fire tasks. The dedicated `worker_beat` service exists solely to be the scheduling singleton.

## Cross-references

- [celery-queues-and-tasks.md](celery-queues-and-tasks.md)
- [backend/feed-collectors.md](../backend/feed-collectors.md)
- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md) — task bodies
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md) — the `COLOCATION_*` / `GNN_*` knobs

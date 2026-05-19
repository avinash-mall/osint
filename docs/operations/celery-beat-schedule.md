# Celery Beat Schedule

**Worker:** `worker_beat` service in [docker-compose.yml](../../docker-compose.yml)
**Schedule storage:** [backend/celerybeat-schedule](../../backend/celerybeat-schedule) (binary state file)
**Schedule definition:** in `backend/worker_legacy.py` near the celery_app block

## Periodic tasks

| Task | Cadence | Purpose |
|---|---|---|
| `worker.poll_http_feeds` | 60 s | Pull events from registered feed sources |
| `worker.cleanup_old_observations` | 1 h | Prune `observations` and `timeline_events` older than the configured retention |
| `worker.compact_inference_dashboard_metrics` | 5 min | Roll up the inference metrics window |

`grep -A20 "beat_schedule" backend/worker_legacy.py` for the live schedule definition.

## Why a separate `worker_beat` service

Celery beat keeps its own state file (`celerybeat-schedule`). Running it inside a regular worker would mean every restart could drop or re-fire tasks. The dedicated `worker_beat` service exists solely to be the scheduling singleton.

## Cross-references

- [celery-queues-and-tasks.md](celery-queues-and-tasks.md)
- [backend/feed-collectors.md](../backend/feed-collectors.md)

# WebSocket Event Channels

**Endpoint:** `/ws` ([backend-routers/websocket-router.md](../backend-routers/websocket-router.md))
**Bridge:** Redis pubsub (see [backend/events-and-timeline.md](../backend/events-and-timeline.md))

## Topics

| Topic | Published by | Subscribed by (frontend) |
|---|---|---|
| `ingest_progress` | `worker.process_satellite_imagery` (per chip) | Ingest workspace progress bars |
| `ingest_complete` | `worker.process_satellite_imagery` (end of pass) | Ingest workspace, Detection layer refresh |
| `fmv_detections_complete` | `worker.process_fmv` (end of clip) | FMV workspace refetch |
| `fmv_progress` | `worker.process_fmv` (per N frames) | FMV upload progress |
| `ontology_updated` | Every ontology mutation in [backend-routers/ontology-router.md](../backend-routers/ontology-router.md) | Ontology UI, prompt-profile UI, any cached ontology consumer |
| `processing_jobs` | Training/analytics job lifecycle | Admin Processing tab |
| `health_alert` | [`/api/health`](../backend-routers/health-router.md) when degradation severity ≥ warn | Admin Alerts tab |

## Payload shape

All topics use the same envelope:

```json
{ "topic": "ingest_progress", "payload": { ... } }
```

`payload` is topic-specific. See `backend/events.py` for the exact shapes published per call site.

## Cross-references

- [backend-routers/websocket-router.md](../backend-routers/websocket-router.md)
- [backend/events-and-timeline.md](../backend/events-and-timeline.md)
- [frontend/event-stream-hook.md](../frontend/event-stream-hook.md)

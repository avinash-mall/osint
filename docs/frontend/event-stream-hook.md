# `useEventStream.ts` — WebSocket Subscription

**Path:** [frontend/src/hooks/useEventStream.ts](../../frontend/src/hooks/useEventStream.ts)
**Lines:** ~60

## Purpose

Open a single WebSocket connection to `/ws`, demultiplex topics to subscribers. Each component needing live updates passes a topic + handler; the hook handles reconnection.

## Behavior

- Single WS connection at app boot; topics multiplexed by name.
- Auto-reconnect with exponential backoff on disconnect.
- Subscribers receive `{topic, payload}` objects.

## Topics consumed

- `ingest_progress` — ingest workspace progress bars
- `fmv_detections_complete` — FMV workspace refresh
- `ontology_updated` — refresh ontology trees in admin
- `processing_jobs` — Admin Processing tab
- `health_alert` — Admin Alerts tab

Full list: [operations/websocket-event-channels.md](../operations/websocket-event-channels.md).

## Cross-references

- [backend-routers/websocket-router.md](../backend-routers/websocket-router.md)
- [backend/events-and-timeline.md](../backend/events-and-timeline.md)

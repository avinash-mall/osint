# `useEventStream.ts` — WebSocket Subscription

**Path:** [frontend/src/hooks/useEventStream.ts](../../frontend/src/hooks/useEventStream.ts)
**Lines:** ~76

## Purpose

Open a single WebSocket connection to `/ws`, demultiplex topics to subscribers. Each component needing live updates passes a topic + handler; the hook handles reconnection.

## Behavior

- Single WS connection at app boot; topics multiplexed by name.
- Auto-reconnect with capped exponential backoff (3 s doubling to 30 s), reset to base on a successful open.
- Close code **1008** (backend rejected the session pre-accept — [backend/routers/ws.py](../../backend/routers/ws.py)) stops reconnecting and dispatches a `sentinel:ws-unauthorized` `CustomEvent` (`detail: { topic }`) on `window` so the auth layer can react; redialing would loop forever against the same rejection.
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

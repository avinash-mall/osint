# WebSocket Router (`/ws`)

**Path:** [backend/routers/ws.py](../../backend/routers/ws.py)
**Lines:** ~40
**Depends on:** Redis pubsub (via `os.getenv("REDIS_URL")`)

## Purpose

A single push channel from backend to browser. Forwards Redis pubsub messages to subscribed WebSocket clients with minimal logic. Topics are published from across the backend and worker.

## Endpoint

| Method | Path | Source |
|---|---|---|
| `WS` | `/ws` | [ws.py#L13](../../backend/routers/ws.py#L13) |

## Topics (Redis channels)

- `ingest_progress` — every chip/frame milestone from the worker
- `fmv_detections_complete` — emitted when `worker.process_fmv` finishes
- `ontology_updated` — published when any ontology endpoint bumps the version
- `health_alert` — for severe degradations the Health Dashboard should highlight immediately
- `processing_jobs` — training job lifecycle

Full list: [operations/websocket-event-channels.md](../operations/websocket-event-channels.md).

## Why this design

- **Single endpoint, multiple topics.** Browsers don't need per-topic connections — they connect once and the server fans out everything subscribed Redis pubsub channels. The frontend hook [useEventStream.ts](../../frontend/src/hooks/useEventStream.ts) demultiplexes by topic name.
- **No application logic in the router.** The router is intentionally thin — it can be load-balanced across multiple backend pods without state coordination.
- **Auth is the session cookie.** The WS handshake checks the same `sentinel_session` cookie the HTTP API uses. There's no separate WS token system.

## Cross-references

- [operations/websocket-event-channels.md](../operations/websocket-event-channels.md)
- [backend/events-and-timeline.md](../backend/events-and-timeline.md)
- [frontend/event-stream-hook.md](../frontend/event-stream-hook.md)

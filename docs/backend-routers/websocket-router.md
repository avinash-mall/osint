# WebSocket Router (`/ws`)

**Path:** [backend/routers/ws.py](../../backend/routers/ws.py)
**Lines:** ~75
**Depends on:** Redis pubsub (via `os.getenv("REDIS_URL")`), `auth.decode_session_cookie` + `auth.SESSION_COOKIE`

## Purpose

Single push channel backend → browser. Forwards Redis pubsub messages to subscribed WebSocket clients with minimal logic. Topics published from across backend + worker.

## Endpoint

| Method | Path | Source |
|---|---|---|
| `WS` | `/ws` | [ws.py#L46](../../backend/routers/ws.py#L46) |

## Auth gate

The handshake is rejected with WebSocket code **1008 (Policy Violation)** before `accept()` if the request carries no valid `sentinel_session` cookie. The check uses the same `auth.decode_session_cookie` the HTTP middleware uses — same `SESSION_SECRET`, salt `sentinel-session-v1`, and TTL — so HTTP and WS auth stay in lockstep automatically. Browser clients need no change: cookies travel on the WS handshake. See [`_get_ws_session_user`](../../backend/routers/ws.py) and the guard immediately after.

### Session re-validation (heartbeat)

Once accepted, the WS handler captures the cookie value and re-decodes it every `WS_SESSION_HEARTBEAT_SECONDS` (default 60s) inside the pubsub loop. On TTL expiry or `SESSION_SECRET` rotation the cookie no longer decodes, and the WS is closed with code 1008 reason `"session expired"` instead of continuing to deliver events to an expired session. This closes the gap where a 12-hour-default cookie could otherwise pin a single accepted connection for the full TTL. See [why-ws-session-heartbeat.md](../decisions/why-ws-session-heartbeat.md). Tests: `test_ws_closes_when_session_invalidates_at_heartbeat`, `test_ws_stays_open_while_session_valid` in [test_reference_platforms_ws_events.py](../../backend/tests/test_reference_platforms_ws_events.py).

## Topics (Redis channels)

- `ingest_progress` — every chip/frame milestone from the worker
- `fmv_detections_complete` — emitted when `worker.process_fmv` finishes
- `ontology_updated` — published when any ontology endpoint bumps the version
- `health_alert` — severe degradations the Health Dashboard should highlight immediately
- `processing_jobs` — training job lifecycle

Full list: [operations/websocket-event-channels.md](../operations/websocket-event-channels.md).

## Why this design

- **Single endpoint, multiple topics** — browsers connect once; server fans out everything from subscribed Redis pubsub channels. Frontend hook [useEventStream.ts](../../frontend/src/hooks/useEventStream.ts) demultiplexes by topic name.
- **No application logic in the router** — intentionally thin → load-balanceable across multiple backend pods without state coordination.
- **Auth = session cookie** — WS handshake checks the same `sentinel_session` cookie as the HTTP API. No separate WS token system.

## Cross-references

- [operations/websocket-event-channels.md](../operations/websocket-event-channels.md)
- [backend/events-and-timeline.md](../backend/events-and-timeline.md)
- [frontend/event-stream-hook.md](../frontend/event-stream-hook.md)

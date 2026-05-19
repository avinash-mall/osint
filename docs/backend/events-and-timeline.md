# `backend/events.py` — Redis Pubsub + Timeline

**Path:** [backend/events.py](../../backend/events.py)
**Lines:** ~153
**Depends on:** `redis`, [backend/database.py](../../backend/database.py) (`postgis_db`)

## Purpose

Two coupled responsibilities: ephemeral fan-out via Redis pubsub (consumed by the WebSocket router) and persistent rows in `timeline_events` + `observations` for the analyst feed.

## Why this design

- **Pubsub for "right now," tables for "later."** WebSocket clients see events instantly; the timeline endpoint shows a stored history. Both are populated from the same call site so the two views never diverge.
- **`normalize_domain`** maps free-form domain strings ("intel", "FMV", "humint") into the closed set `{GEOINT, SIGINT, HUMINT, OSINT, MASINT, FMV, ADMIN, WORKFLOW}` so the UI's domain facets work.
- **`domain_for_media`** picks a default domain from a media type — used by the ingest router so uploads tag themselves automatically.
- **Fire-and-forget.** Both publish + record are wrapped in try/except so an event failure never breaks the calling action.

## Key symbols

- [`get_redis_client`](../../backend/events.py#L27) — cached lazy connection.
- [`publish_event`](../../backend/events.py#L37) — pubsub to a topic.
- [`normalize_domain`](../../backend/events.py#L49).
- [`domain_for_media`](../../backend/events.py#L58).
- [`record_timeline_event`](../../backend/events.py#L69) — INSERT into `timeline_events`.
- [`record_observation`](../../backend/events.py#L99) — INSERT into `observations`.

## Topic list

See [operations/websocket-event-channels.md](../operations/websocket-event-channels.md).

## Cross-references

- [backend-routers/websocket-router.md](../backend-routers/websocket-router.md)
- [operations/websocket-event-channels.md](../operations/websocket-event-channels.md)
- [backend/platform-schema-migrations.md](platform-schema-migrations.md) (the `timeline_events` / `observations` tables)

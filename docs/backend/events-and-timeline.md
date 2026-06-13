# `backend/events.py` — Redis Pubsub + Timeline

**Path:** [backend/events.py](../../backend/events.py)
**Lines:** ~180
**Depends on:** `redis`, [backend/database.py](../../backend/database.py) (`postgis_db`)

## Purpose

Two coupled responsibilities: ephemeral fan-out via Redis pubsub (consumed by WebSocket router), and persistent `timeline_events` + `observations` rows for the analyst feed.

## Why this design

- **Pubsub for "now," tables for "later"** — WebSocket clients see events instantly; timeline endpoint shows stored history. Both populated from the same call site → views never diverge.
- **`normalize_domain`** — maps free-form domain strings (`intel`, `FMV`, `humint`) into closed set `{GEOINT, SIGINT, HUMINT, OSINT, MASINT, FMV, ADMIN, WORKFLOW}` for UI domain facets.
- **`domain_for_media`** — picks default domain from media type; used by ingest router so uploads tag themselves.
- **Fire-and-forget** — publish + record wrapped in try/except → event failure never breaks the calling action.

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
- [backend/platform-schema-migrations.md](platform-schema-migrations.md) — `timeline_events` / `observations` tables

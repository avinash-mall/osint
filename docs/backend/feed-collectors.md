# `backend/feed_collectors.py` — HTTP Feed Parsers

**Path:** [backend/feed_collectors.py](../../backend/feed_collectors.py)
**Lines:** ~158
**Depends on:** `requests`

## Purpose

Polling parsers for external HTTP feeds (ADS-B, AIS, civilian GeoJSON pushes). Used by the Celery-beat scheduled poller — see [operations/celery-beat-schedule.md](../operations/celery-beat-schedule.md).

## Why this design

- **Pure parsers; no DB writes** — returns list of normalized `{event_type, lat, lon, observed_at, payload}` dicts; calling task writes to PostGIS. Splitting parser from persistence → both unit-testable, parser runs dry.
- **Three formats built in** — JSON, GeoJSON, ADS-B BaseStation text. New formats = another parser function + dispatch on the source's `format` field.
- **Bounded** — max 500 events per poll; response-size cap before parsing. Stops a misconfigured feed flooding the DB.

## Key symbols

- [`_coerce_float`](../../backend/feed_collectors.py#L30), [`_coerce_str`](../../backend/feed_collectors.py#L37) — defensive type coercion.
- [`_parse_json_events`](../../backend/feed_collectors.py#L43).
- [`_parse_geojson`](../../backend/feed_collectors.py#L67).
- [`_parse_adsb_basestation`](../../backend/feed_collectors.py#L100).
- [`poll_http_feed`](../../backend/feed_collectors.py#L135) — public entry; dispatches by `source.format`.

## Failure modes

- Endpoint timeout → `[]` + log.
- Malformed body → parser skips bad rows, returns the rest.

## Cross-references

- [operations/celery-beat-schedule.md](../operations/celery-beat-schedule.md)
- `/api/feeds/*` in [backend/api-routes-reference.md](api-routes-reference.md)

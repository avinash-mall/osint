# `backend/feed_collectors.py` — HTTP Feed Parsers

**Path:** [backend/feed_collectors.py](../../backend/feed_collectors.py)
**Lines:** ~158
**Depends on:** `requests`

## Purpose

Polling parsers for external HTTP feeds (analyst-facing data sources like ADS-B feeds, AIS, civilian GeoJSON pushes). Used by the Celery-beat scheduled poller — see [operations/celery-beat-schedule.md](../operations/celery-beat-schedule.md).

## Why this design

- **Pure parsers; no DB writes here.** The function returns a list of normalized `{event_type, lat, lon, observed_at, payload}` dicts; the calling task writes to PostGIS. Splitting the parser from the persistence makes both unit-testable and lets the parser run dry.
- **Three formats out of the box.** JSON, GeoJSON, and ADS-B BaseStation text. Other formats are added by writing another parser function and dispatching on the source's declared `format` field.
- **Bounded.** Max 500 events per poll; cap on response size before parsing. Stops a misconfigured feed from flooding the DB.

## Key symbols

- [`_coerce_float`](../../backend/feed_collectors.py#L30), [`_coerce_str`](../../backend/feed_collectors.py#L37) — defensive type coercion.
- [`_parse_json_events`](../../backend/feed_collectors.py#L43).
- [`_parse_geojson`](../../backend/feed_collectors.py#L67).
- [`_parse_adsb_basestation`](../../backend/feed_collectors.py#L100).
- [`poll_http_feed`](../../backend/feed_collectors.py#L135) — public entry; dispatches by `source.format`.

## Failure modes

- Endpoint timeout → returns `[]` and logs.
- Malformed body → parser skips bad rows and returns the rest.

## Cross-references

- [operations/celery-beat-schedule.md](../operations/celery-beat-schedule.md)
- `/api/feeds/*` in [backend/api-routes-reference.md](api-routes-reference.md)

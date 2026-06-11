# Release the pooled DB connection before per-row response enrichment

> **Status note (2026-06-11):** the endpoint this decision modified,
> `GET /api/detections/geojson`, has been removed (see
> [removed-legacy-detection-geojson-path.md](removed-legacy-detection-geojson-path.md)).
> The principle stands for any future bulk endpoint that does slow pure-Python
> work per row: hold the pooled connection only for `execute()` + `fetchall()`.

## Decision

In `GET /api/detections/geojson` ([backend/main.py](../../backend/main.py)) the pooled
PostGIS connection is now held **only** for `execute()` + `fetchall()`. The per-row
`enriched_detection_metadata()` loop and response assembly run **after** the
`with postgis_db.get_cursor()` block exits, on the already-materialised `rows`.

Previously the entire enrichment loop and `return` lived inside the cursor block.

## Why

- **Root cause of an app-wide outage class.** On a dense scene (thousands of detections /
  tens of MB) the enrichment loop is pure-Python and runs for ~20–30 s, and under
  concurrency the GIL serialises it. Holding the connection across that window kept it
  `idle in transaction` the whole time. The pool is `max=10`
  ([database.py](../../backend/database.py) `POSTGIS_POOL_MAX`); ~10 concurrent map polls
  therefore checked out all 10 connections for tens of seconds each, so every *other*
  endpoint got `RuntimeError: PostGIS connection pool exhausted` → **500 + a "Degraded"
  banner**, not self-healing until traffic drained. Heavy ingests made it worse (more
  detections → longer enrichment; CPU contention → even slower drain).
- **The query itself is cheap (~25 ms).** Only the Python response-building is slow, and
  it needs no DB access (`fetchall` already materialised the rows; `enriched_detection_metadata`
  is pure-Python). So the connection has no reason to stay checked out.
- **`get_cursor` was not at fault.** It correctly rolls back + returns the connection on
  normal, `Exception`, and `GeneratorExit` exits (verified). The leak was *duration of
  hold*, not a missing release.

## Reproduction / verification

12 concurrent `geojson` requests against ~3 000 detections:
- **Before:** `/api/imagery` → `500`, `idle in transaction` pegged at **10**, stuck for
  minutes after traffic stopped → required a `backend` restart.
- **After:** `/api/imagery` → `200` throughout, `idle in transaction` peaked at **2** and
  drained to **0**. geojson latency is unchanged (~22 s of enrichment) but no longer blocks
  the pool; the frontend's 60 s layer-fetch timeout + "Loading detections" spinner cover it.

## Scope / not addressed here

- The ~22 s enrichment latency for a whole-scene view is a separate *performance* matter
  (candidate optimisations: cache `enriched_detection_metadata` by class, trim the
  per-detection `imagery_metadata`/`metadata` duplication, server-side geometry simplify).
  It is no longer a *stability* matter.
- Any other read endpoint that builds a large response inside its cursor block has the same
  anti-pattern — fetch rows, exit the cursor, then build.

## Cross-references

- [backend/database-connections.md](../backend/database-connections.md) — pool sizing + the hold-time rule
- [decisions/reset-db-pool-after-fork.md](reset-db-pool-after-fork.md)

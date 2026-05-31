# `/api/satellites/*` — Satellites Router

**Path:** [backend/routers/satellites.py](../../backend/routers/satellites.py)
**Lines:** ~240
**Depends on:** `satellite_overpass`, `satellite_anomaly`, `database` (PostGIS `satellite_tles` + `satellite_tle_history`), `platform_schema`, `schemas`

## Purpose

HTTP surface for offline satellite overpass planning: import TLEs over an
air-gap, predict overpasses over an AOI/point, fetch a ground track, **and
detect orbital maneuvers/decay** from successive TLE epochs (R1) with a
mission-family tag on each object (R2). All read paths are pure computation, so
the feature works air-gapped.

## Why this design

- **TLEs stored in PostGIS, not on disk.** Avoids writing to the read-only
  runtime data dirs (Hard rule #1) and follows the platform-schema migration
  convention. Upsert by NORAD id (latest import wins).
- **POST for prediction.** `/passes` takes a rich body (sat list, AOI/point,
  window) so it is POST, matching the `/api/analytics/*` job endpoints; the
  session middleware gates it automatically.
- **Observer from an AOI centroid or explicit lat/lon** — composes with
  [aois.py](../../backend/routers/aois.py) so "next pass over this AOI" is one call.

## Key symbols

- `import_tle` — `POST /api/satellites/tle`, upsert by NORAD id; also appends the
  epoch to `satellite_tle_history` (idempotent) for anomaly detection.
- `list_tles` — `GET /api/satellites/tle` (provenance: epoch, source, **mission**).
- `predict_overpasses` — `POST /api/satellites/passes` (each sat carries **mission**).
- `get_ground_track` — `GET /api/satellites/ground-track/{norad_id}`.
- `list_anomalies` — `GET /api/satellites/anomalies` (R1) — maneuvers + decay from
  the two most-recent epochs per object; delegates to `satellite_anomaly.py`.
- Helpers: `_load_tles`, `_resolve_observer`, `_parse_iso`.

## Inputs / Outputs

- **`POST /tle`** ← `TleImportRequest {text, source?}` → `{success, imported}`.
- **`GET /tle`** → `{tles:[{norad_id, name, mission, epoch, source, imported_at}], count}`.
- **`POST /passes`** ← `OverpassRequest {norad_ids?, aoi_id|lat+lon, start?, end?, hours, min_elevation_deg, step_s}`
  → `{observer, window, satellites:[{norad_id, name, mission, passes:[...]}]}`.
- **`GET /ground-track/{norad_id}?hours=&step_s=`** → `{coordinates, altitudes_km}`.
- **`GET /anomalies?norad_id=`** → `{maneuvers:[...], decay_anomalies:[...], objects_compared}`.

## Failure modes

- No TLEs stored → 404 with guidance to import.
- AOI not found / missing observer → 404 / 400.
- A single bad element set is skipped (logged), not fatal to the batch.
- `/anomalies` needs ≥2 distinct epochs per object; with one import it returns
  empty lists and `objects_compared: 0`.

## Cross-references

- Module: [backend/satellite-overpass.md](../backend/satellite-overpass.md)
- Module: [backend/satellite-anomaly.md](../backend/satellite-anomaly.md) (maneuver/decay + mission)
- Decision: [decisions/why-offline-tle-import.md](../decisions/why-offline-tle-import.md)
- Decision: [decisions/why-tle-history-for-maneuvers.md](../decisions/why-tle-history-for-maneuvers.md)
- Schema: [backend/pydantic-schemas.md](../backend/pydantic-schemas.md)
- Migration: `satellite_tles` + `satellite_tle_history` in [backend/platform_schema.py](../../backend/platform_schema.py)

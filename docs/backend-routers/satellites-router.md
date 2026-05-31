# `/api/satellites/*` — Satellites Router

**Path:** [backend/routers/satellites.py](../../backend/routers/satellites.py)
**Lines:** ~165
**Depends on:** `satellite_overpass`, `database` (PostGIS `satellite_tles`), `platform_schema`, `schemas`

## Purpose

HTTP surface for offline satellite overpass planning: import TLEs over an
air-gap, predict overpasses over an AOI/point, and fetch a ground track.
Prediction is pure computation on read, so the feature works air-gapped.

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

- `import_tle` — `POST /api/satellites/tle`, upsert by NORAD id.
- `list_tles` — `GET /api/satellites/tle` (provenance: epoch, source).
- `predict_overpasses` — `POST /api/satellites/passes`.
- `get_ground_track` — `GET /api/satellites/ground-track/{norad_id}`.
- Helpers: `_load_tles`, `_resolve_observer`, `_parse_iso`.

## Inputs / Outputs

- **`POST /tle`** ← `TleImportRequest {text, source?}` → `{success, imported}`.
- **`POST /passes`** ← `OverpassRequest {norad_ids?, aoi_id|lat+lon, start?, end?, hours, min_elevation_deg, step_s}`
  → `{observer, window, satellites:[{norad_id, name, passes:[...]}]}`.
- **`GET /ground-track/{norad_id}?hours=&step_s=`** → `{coordinates, altitudes_km}`.

## Failure modes

- No TLEs stored → 404 with guidance to import.
- AOI not found / missing observer → 404 / 400.
- A single bad element set is skipped (logged), not fatal to the batch.

## Cross-references

- Module: [backend/satellite-overpass.md](../backend/satellite-overpass.md)
- Decision: [decisions/why-offline-tle-import.md](../decisions/why-offline-tle-import.md)
- Schema: [backend/pydantic-schemas.md](../backend/pydantic-schemas.md)
- Migration: `satellite_tles` in [backend/platform_schema.py](../../backend/platform_schema.py)

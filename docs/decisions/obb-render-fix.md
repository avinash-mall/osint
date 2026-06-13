# Why Martin must start after the backend (OBB / detection boxes render fix)

**Status:** shipped (2026-06-13). One-line compose change: `martin.depends_on`
now waits on `backend: condition: service_healthy` in addition to `postgis`.

## Symptom

"The OBB is missing." On the Map workspace, detection **markers/dots** rendered
(from the authed `/api/detections/geojson-lite` feed) but the **boxes** never
did — for OBB, HBB *and* MASK alike. Every request to
`/maps/detections_mvt/{z}/{x}/{y}?geom_mode=…` returned **HTTP 404**.

## Root cause — a startup ordering race (not a frontend bug)

The persisted-box renderer is the Martin MVT layer
(`DetectionTileLayer.tsx`, see [why-detection-mvt-tiles.md](why-detection-mvt-tiles.md)).
Martin **auto-discovers its tile sources exactly once, at process startup, and
never re-scans**. The `detections_mvt` function source (and its
`detection_obb_geom` helper) is **created by the backend at startup**
(`backend/platform_schema.py`), *not* at DB-init (`init_postgis.sql` only
creates tables). Martin's compose entry depended on **`postgis` only**, so:

```
postgis healthy ─▶ martin starts, scans DB  (function does NOT exist yet) ─▶ publishes only TABLE sources
                       ⋮  (later)
backend starts ─▶ platform_schema.py CREATE OR REPLACE FUNCTION detections_mvt  ─▶ martin never re-scans
```

The function ends up in `pg_proc` (4-arg signature, correct) but **absent from
Martin's catalog** — so the function-source path 404s forever. Markers come from
the backend feed (unaffected); boxes come from Martin (broken). Hence
"markers yes, boxes no."

### Evidence (Phase-0 boundary tracing)

- **DB:** all 2104 live detections carry a well-formed `metadata.geo_polygon`
  (array, even length ≥6). Clean.
- **SQL:** `detection_obb_geom(metadata, geom)` returns a valid 5-point polygon
  with real area. Clean.
- **Martin catalog:** `detections_mvt` **absent**; only table sources
  `detections` (`.centroid`) and `detections.1` (`.geom`) present. Startup log:
  "Auto-publishing functions" discovered **zero** functions.
- **Decisive test:** `docker restart osint-martin-1` (function now exists) →
  `detections_mvt` appears in the catalog and a tile over the detection cluster
  returns **HTTP 200, ~3.5 KB, `detections` layer present**. Confirms the race.

## Decision

Add `backend: condition: service_healthy` to `martin.depends_on`. The backend's
healthcheck (`GET /api/health → healthy:true`) only passes after FastAPI
startup completes, which is after `platform_schema.py` has run — so Martin now
always scans a DB where `detections_mvt` already exists.

### Why this and not the alternatives

- **Not "create the function in `init_postgis.sql`."** Init scripts run only on
  a *fresh* DB volume, so existing deployments would stay broken; and it would
  duplicate the function body (drift risk) — the single source of truth stays
  `platform_schema.py`.
- **Not an explicit Martin config file.** Martin still resolves sources once at
  startup; a config naming the function doesn't fix *when* the scan happens.
- **No dependency cycle.** The backend depends on
  neo4j/postgis/redis/titiler/inference-sam3 — never on Martin.
- **Cost:** Martin start is delayed until backend health (≤ its 60 s
  start_period). Acceptable — serving detection tiles before the schema exists
  is pointless anyway.

## Consequences

- Cold `docker compose up` (fresh or existing volume) now renders detection
  boxes without manual intervention.
- A backend restart while Martin keeps running is safe: `CREATE OR REPLACE`
  preserves the already-discovered function; Martin needs no re-scan.
- **Smoke check:** after `compose up`, `GET /maps/detections_mvt/<z>/<x>/<y>?geom_mode=obb`
  over a known detection cluster must be HTTP 200 with non-zero bytes (empty
  areas legitimately return 204). The Martin `/catalog` must list `detections_mvt`.

## Cross-references

- [decisions/why-detection-mvt-tiles.md](why-detection-mvt-tiles.md)
- [deployment/docker-compose-services.md](../deployment/docker-compose-services.md)
- [architecture/service-topology.md](../architecture/service-topology.md)
- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)

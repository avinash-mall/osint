# Gotham OSINT / GEOINT Platform — Expanded Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the current minimal Gotham-inspired stack ([backend/](backend/) + [inference/](inference/) + [frontend/](frontend/)) into a production-grade GEOINT exploitation platform that ingests and fuses multi-INT data (IMINT / SIGINT / OSINT / MASINT / GEOINT), supports real-time sensor feeds, video exploitation with frame-accurate overlays, analyst tooling (viewshed, LOS, change detection, pattern-of-life), collection management, and operates end-to-end on an **air-gapped** network.

**Architecture:** Service-mesh of containerized FastAPI + Celery + PostGIS + Neo4j + Redis + TiTiler + Martin + Cesium + MLflow, all fed by an auth-gated ingest plane and fanned out in real time via WebSocket on top of Redis pub/sub. Every dependency is vendored: Python wheelhouse, Cesium/MapLibre static assets, MBTiles basemaps, bundled YOLO weights, local OSRM/Nominatim, pre-seeded Natural Earth.

**Tech Stack:** Python 3.11 (FastAPI, Celery, GDAL/rasterio, geopandas/pyogrio, fastkml, ffmpeg-python, klvdata, py3dtiles, Ultralytics YOLO, MLflow, fastapi-users, alembic, SQLAlchemy, shapely, pyproj, norfair, satellite.js' Python peer `sgp4`, whitebox, rasterio-cogeo, pysolar), TypeScript / React 19 / Vite 8 (react-leaflet, maplibre-gl, cesium, hls.js, deck.gl, react-query, zustand), Postgres 16 + PostGIS 3.4, Neo4j 5.20, Redis 7, MLflow 2.14, GDAL 3.8, nginx.

---

## Table of Contents

0. [Validation Findings](#validation-findings)
1. [System Architecture](#system-architecture)
2. [Domain Model — Ontology & Spatial Schema](#domain-model)
3. [Library & Component Choices](#library--component-choices)
4. [Phase 0 — Auth, Schema, Migrations, DevEx](#phase-0)
5. [Phase 1 — Multi-Format Ingest](#phase-1)
6. [Phase 2 — Sensor-Aware Visualization](#phase-2)
7. [Phase 3 — FMV / Video Exploitation with Frame-Synchronized Overlays](#phase-3)
8. [Phase 4 — Real-Time Feeds + WebSocket Fanout](#phase-4)
9. [Phase 5 — Model Training & MLOps](#phase-5)
10. [Phase 6 — GEOINT Analytics & Exploitation Toolkit](#phase-6)
11. [Phase 7 — Collection Management & PED Workflow](#phase-7)
12. [Phase 8 — Target Packages, Reports, Dissemination](#phase-8)
13. [Phase 9 — Bug Fixes, Placeholder Removal, Full Offline Hardening](#phase-9)
14. [Cross-Cutting — Tests, CI, Observability, Security](#cross-cutting)
15. [Offline Bundle Specification](#offline-bundle)
16. [End-to-End Acceptance](#acceptance)

---

<a id="validation-findings"></a>
## 0. Validation Findings (pre-implementation sweep)

Every claim in the original plan was cross-checked against the current repository. A handful of **new** bugs and gaps surfaced that were not in the original plan and are folded into Phase 9.

**Confirmed placeholders / bugs (already in plan, retained):**

| # | Location | Issue |
|---|---|---|
| 1 | [backend/main.py:14](backend/main.py#L14) | `allow_origins=["*"]` — open CORS |
| 2 | [backend/ai.py:14](backend/ai.py#L14) | `OPENAI_API_KEY="dummy"` default |
| 3 | [backend/ai.py:32](backend/ai.py#L32) | `allow_dangerous_requests=True` — arbitrary Cypher from LLM |
| 4 | [backend/ai.py:9-11](backend/ai.py#L9-L11) | Hard-coded `bolt://localhost:7687` / `http://localhost:8000/v1` |
| 5 | [backend/database.py:9](backend/database.py#L9) | Default `"password"` |
| 6 | [backend/worker.py:159-160](backend/worker.py#L159-L160) | Inference errors `print()` then swallowed |
| 7 | [backend/worker.py:239-240](backend/worker.py#L239-L240) | `image_url.startswith("s3://") == False` logic bug (always truthy) |
| 8 | [inference/main.py:131-149](inference/main.py#L131-L149) | Random-mock detections when no model |
| 9 | [frontend/src/components/TargetWorkbench.tsx:67-78](frontend/src/components/TargetWorkbench.tsx#L67-L78) | Hardcoded `usgs_pass_001.tif` path + `alert()` |
| 10 | [frontend/src/components/ConstellationView.tsx:57-63](frontend/src/components/ConstellationView.tsx#L57-L63) | Simulated `endLat: sat.lat + 10` arcs |
| 11 | [frontend/src/components/ConstellationView.tsx:92](frontend/src/components/ConstellationView.tsx#L92) | Hardcoded `T-{15 + i*12}m` TCA |
| 12 | [frontend/src/components/GaiaMap.tsx:99](frontend/src/components/GaiaMap.tsx#L99) | `setInterval(fetchData, 10000)` polling |
| 13 | [frontend/src/components/GaiaMap.tsx:271](frontend/src/components/GaiaMap.tsx#L271) | CARTO CDN basemap (online-only) |

**New bugs / gaps found during validation (NOT in the original plan, must be fixed):**

| # | Location | Issue | Fix Phase |
|---|---|---|---|
| N1 | [backend/Dockerfile:4-11](backend/Dockerfile#L4-L11) | No `gdal-bin` — `subprocess.run(["gdal_translate", …])` at [worker.py:39-47](backend/worker.py#L39-L47) will fail in-container | Phase 0 |
| N2 | [backend/worker.py:99](backend/worker.py#L99) | `np.all(chip == src.nodata)` crashes when `src.nodata is None` | Phase 9 |
| N3 | [backend/worker.py:108-116](backend/worker.py#L108-L116) | `profile.update(driver="PNG")` fails for multi-band float rasters (PNG driver has 1/2/3/4 band uint8/uint16 only) | Phase 1 |
| N4 | [backend/worker.py:18](backend/worker.py#L18) | `Celery(..., broker=REDIS_URL)` — **no** `backend=` → `AsyncResult` cannot report state; required for `GET /api/ingest/jobs/{task_id}` | Phase 0 |
| N5 | [backend/init_postgis.sql:40-55](backend/init_postgis.sql#L40-L55) | `ne_countries`/`ne_coastline` tables defined but never populated → Martin serves empty offline basemap tiles | Phase 9 |
| N6 | [frontend/package.json](frontend/package.json) | Plan referenced React 18; actual is React 19, Vite 8, TypeScript ~6.0.2 | Plan updated |
| N7 | [inference/Dockerfile:30](inference/Dockerfile#L30) | `RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"` requires internet at build time | Phase 9 |
| N8 | [docker-compose.yml:38-61](docker-compose.yml#L38-L61) | Backend has no `depends_on: titiler / redis`; ordering relies on restart loops | Phase 0 |
| N9 | [backend/worker.py:262-271](backend/worker.py#L262-L271) | `satellite_passes.footprint` is declared `MULTIPOLYGON` in [init_postgis.sql:12](backend/init_postgis.sql#L12), but worker inserts a plain `POLYGON` WKT — PostGIS will error on first real ingest | Phase 0 (with baseline migration) |
| N10 | [backend/database.py](backend/database.py) | Uses `psycopg2-binary` sync; our new WS/fastapi-users code wants `asyncpg`. Need dual-driver coexistence | Phase 0 |
| N11 | [backend/main.py:353-357](backend/main.py#L353-L357) | `from worker import process_satellite_imagery` inside endpoint reaches across process boundary; fine today, but the ingest router must import the celery signature directly, not the function | Phase 1 |
| N12 | [frontend/src/components/AvaChat.tsx](frontend/src/components/AvaChat.tsx) | (not examined in original plan) — likely passes raw user text to `GraphCypherQAChain`. Must be JWT-gated + read-only chain after Phase 0 | Phase 0 |

---

<a id="system-architecture"></a>
## 1. System Architecture

### 1.1 Service Topology (end state, post-Phase 9)

```
                       ┌──────────────────────────── Analyst Browser ──────────────────────────┐
                       │  React 19 / Vite / Tailwind — GaiaMap · FMV · View3D · Admin · PED    │
                       │  maplibre-gl + CesiumJS + hls.js + deck.gl + react-query + zustand    │
                       └───────────────▲───────────────────────────────▲───────────────────────┘
                                       │ https+wss                      │ https (tiles)
                                       │                                │
                                ┌──────┴────────┐                ┌──────┴───────┐
                                │   nginx       │                │   nginx      │
                                │   (TLS, auth  │                │ (tile cache  │
                                │    propagat.) │                │  + gzip)     │
                                └──────┬────────┘                └──────┬───────┘
                                       │                                │
    ┌──────────────────────────────────┼────────────────────────────────┼──────────────────────┐
    │                                  │                                │                      │
    │      ┌───────────────┐   ┌───────▼──────────┐   ┌─────────────┐  ┌▼──────────────┐       │
    │      │ FastAPI       │   │ FastAPI          │   │ MLflow 2.14 │  │ TiTiler       │       │
    │      │ backend       │◀──│ auth (fastapi-   │   │  (model     │  │ (COG, NetCDF, │       │
    │      │ REST + WS     │   │  users, JWT)     │   │   registry) │  │  Zarr, band   │       │
    │      └───┬────────┬──┘   └──────────────────┘   └─────────────┘  │  math)        │       │
    │          │        │                                              └───────▲───────┘       │
    │   Redis  │        │ Celery                                               │ reads         │
    │   pub/   │        │ queues: default, imagery, vector,                    │               │
    │   sub    │        │         video, training, feeds, analytics            │               │
    │   ┌──────▼────┐   │                                                      │               │
    │   │  redis    │   │                                                      │               │
    │   └──────▲────┘   │                                                      │               │
    │          │        │                                                      │               │
    │   ┌──────┼────────┼──────────────────────────────────────────────────────┼──────┐        │
    │   │ Celery workers                                                       │      │        │
    │   │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │      │        │
    │   │  │ imagery  │  │ vector   │  │ video /  │  │ training │  │feeds /  │ │      │        │
    │   │  │ (GDAL,   │  │ (gpd,    │  │ KLV /FMV │  │ (YOLO)   │  │analytics│ │      │        │
    │   │  │ rasterio)│  │ pyogrio) │  │ (ffmpeg, │  │          │  │         │ │      │        │
    │   │  │          │  │          │  │ klvdata) │  │          │  │         │ │      │        │
    │   │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬────┘ │      │        │
    │   │       │             │             │              │            │      │      │        │
    │   │       │             │             ▼              │            │      │      │        │
    │   │       │             │       ┌──────────┐         │            │      │      │        │
    │   │       │             │       │Inference │─────────┘            │      │      │        │
    │   │       │             │       │ YOLOv8 + │                      │      │      │        │
    │   │       │             │       │   SAHI   │                      │      │      │        │
    │   │       │             │       └────┬─────┘                      │      │      │        │
    │   │       │             │            │                            │      │      │        │
    │   │       ▼             ▼            ▼                            ▼      │      │        │
    │   │  ┌──────────────────────────────────────────────────────────────────┐│      │        │
    │   │  │                          PostGIS (spatial)                       ││      │        │
    │   │  │  satellite_passes · detections · vector_layers · fmv_clips ·     ││      │        │
    │   │  │  tiles_3d · feed_events · aois · observations · tracks · models ││      │        │
    │   │  └──────────────────────────────────────────────────────────────────┘│      │        │
    │   │                                                                      │      │        │
    │   │  ┌──────────────────────────────────────────────────────────────────┐│      │        │
    │   │  │                     Neo4j (ontology / GraphRAG)                  ││      │        │
    │   │  │  Target · Asset · Person · Event · Pass · Detection · Feed       ││      │        │
    │   │  └──────────────────────────────────────────────────────────────────┘│      │        │
    │   └──────────────────────────────────────────────────────────────────────┘      │        │
    │                                                                                  │       │
    │                                ┌──────────────┐        ┌────────────┐           │        │
    │                                │  Martin      │        │  External  │           │        │
    │                                │ (MVT from    │        │ LLM (OSS,  │           │        │
    │                                │  PostGIS +   │        │  local)    │           │        │
    │                                │  MBTiles)    │        │            │           │        │
    │                                └──────────────┘        └────────────┘           │        │
    └──────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Control Flow — Ingest → Detect → Dissemination (the "kill chain" pipeline)

```
 Analyst uploads             Celery routes             Worker                    Backend
 /api/ingest/upload  ──►  via extension →  ──►   process_<format>  ──► redis.publish("events:<topic>")
 (JWT-gated)              registry dispatch       (GDAL / ffmpeg /     │              │
                                                   klvdata / etc.)     │              │
                                                                       │              ▼
                                                                       │      WS fanout  ──►  React UI
                                                                       │              │       (GaiaMap auto-refresh,
                                                                       ▼              │        FMV playhead seek,
                                                              Store in PostGIS        │        Target Workbench badge)
                                                              + Neo4j                 │
                                                                       │              ▼
                                                                       └──► Auto-chain: schedule next stage
                                                                             (e.g. inference after COG ready)
```

### 1.3 Data Plane — Shared Volumes and Ownership

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ /data (shared bind; readonly where possible)                                 │
│                                                                              │
│   imagery/                                                                   │
│     incoming/<uuid>/<file>         — upload landing, world-writable by API   │
│     processed/<uuid>_cog.tif       — COG output, world-readable              │
│     chips/                         — ephemeral inference chips, gc'd         │
│   fmv/<clip_id>/                   — HLS index + fragments, serve via nginx  │
│     index.m3u8                                                               │
│     chunk_00001.m4s …                                                        │
│     klv.jsonl                      — per-frame pose timeline                 │
│     thumbs/                                                                  │
│     overlays/                      — cached overlay render PNGs              │
│   tiles3d/<tileset_id>/tileset.json                                          │
│   vector/<layer_id>.gpkg           — canonical vector mirror                 │
│   basemaps/world.mbtiles           — offline planet basemap                  │
│   dem/<aoi>.tif                    — DEM tiles for LOS / viewshed            │
│   datasets/<name>/                 — YOLO training datasets                  │
│   models/<name>_<ver>/weights/     — .pt weights, hashed path                │
│   mlruns/                          — MLflow artifact root                    │
│   exports/<user>/<yyyy-mm-dd>/     — PDF / KMZ / GPKG exports                │
│   wheels/                          — pip wheelhouse for air-gapped rebuild   │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 1.4 Network / Port Matrix (final)

| Service | Internal port | Exposed | Purpose |
|---|---|---|---|
| nginx (edge) | 443 | 443 | TLS termination, routing, tile cache |
| backend (FastAPI + WS) | 8080 | — | REST + WebSocket |
| inference | 8001 | — | `/detect`, `/reload`, `/health` |
| titiler | 8080 | — | COG / NetCDF / Zarr / band-math |
| martin | 3000 | — | MVT from PostGIS + MBTiles |
| postgis | 5432 | — | Spatial + auth |
| neo4j | 7474, 7687 | — | Ontology |
| redis | 6379 | — | Broker + pub/sub |
| mlflow | 5000 | — | Model registry + UI |
| frontend | 3000 | — | Dev-only; prod is served via nginx static |

All traffic flows through **nginx**; nothing else is published on the host in production. This keeps the attack surface at exactly one TLS endpoint.

---

<a id="domain-model"></a>
## 2. Domain Model — Ontology & Spatial Schema

### 2.1 Neo4j Ontology (post-Phase 0)

```
                    ┌───────────┐  OWNED_BY    ┌──────────┐
                    │   User    │◀─────────────│  Target  │
                    └───────────┘              └────┬─────┘
                                                    │
                              DETECTED_AS           │ BELONGS_TO
                          ┌────────────────────────▶│
                 ┌────────┴──────┐                  │
                 │  Detection    │                  ▼
                 └────▲──────────┘        ┌──────────────────┐
                      │ CONTAINS          │       Asset      │ OBSERVED_AT
                      │                   │ (ship/aircraft/… )│────┐
              ┌───────┴───────┐           └─────────┬────────┘    │
              │ SatellitePass │                     │              ▼
              │ (postgis_id)  │ OBSERVES            │       ┌───────────────┐
              └──────┬────────┘ ──────────────────▶ │       │ Observation   │
                     │ TASKED_BY                     │       │  (t, x, y, z) │
              ┌──────▼────────┐            ┌────────▼─────┐ └───────────────┘
              │ Satellite     │            │  Event       │
              │ (TLE, sensor) │            │ (incident,   │
              └───────────────┘            │  PIR match)  │
                                           └──────────────┘

              ┌─────────────┐     FED_BY      ┌─────────────┐
              │ FeedSource  │◀────────────────│ FeedEvent   │  (AIS, ADS-B,
              │ (kind)      │                 │ (geom, t)   │   RSS, MQTT)
              └─────────────┘                 └─────────────┘

              ┌───────────┐  COVERED_BY   ┌───────────────┐
              │    AOI    │◀──────────────│ CollectionTask│
              │ (polygon, │               │ (EEIs, SLA)   │
              │  priority)│               └───────┬───────┘
              └───────────┘                       │
                                                  ▼
                                         ┌────────────────┐
                                         │ CollectionPlan │
                                         │ (window,       │
                                         │  sensor mix)   │
                                         └────────────────┘
```

### 2.2 PostGIS Schema (Alembic baseline + extensions)

`backend/migrations/versions/0001_baseline.py` ports today's [backend/init_postgis.sql](backend/init_postgis.sql). `0002_extensions.py` adds:

```sql
CREATE EXTENSION IF NOT EXISTS postgis_raster;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TYPE sensor_type_enum AS ENUM
  ('optical','panchromatic','multispectral','hyperspectral','sar','thermal','fmv','lidar');
CREATE TYPE asset_format_enum AS ENUM
  ('geotiff','jp2','nitf','netcdf','kml','kmz','shp','geojson','gpkg','mp4','mpeg_ts','citygml','3dtiles','las','laz');
CREATE TYPE job_status_enum AS ENUM ('pending','running','succeeded','failed','cancelled','retrying');
CREATE TYPE feed_kind_enum  AS ENUM ('ais','adsb','rss','mqtt','kafka','http_poll','websocket','stix');
CREATE TYPE intel_classification AS ENUM ('unclassified','fouo','confidential','secret');

-- Users / auth (managed by fastapi-users)
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email CITEXT UNIQUE NOT NULL,
  hashed_password TEXT NOT NULL,
  is_active BOOL DEFAULT true,
  is_superuser BOOL DEFAULT false,
  is_verified BOOL DEFAULT false,
  role TEXT DEFAULT 'analyst',  -- analyst | collection_mgr | admin | viewer
  last_login TIMESTAMPTZ
);

-- Fix N9: footprint is MULTIPOLYGON, but worker insertion now also wraps POLYGON → MULTIPOLYGON via ST_Multi
ALTER TABLE satellite_passes
  ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES users(id),
  ADD COLUMN IF NOT EXISTS format asset_format_enum,
  ADD COLUMN IF NOT EXISTS classification intel_classification DEFAULT 'unclassified',
  ADD COLUMN IF NOT EXISTS band_count INT,
  ADD COLUMN IF NOT EXISTS band_names TEXT[],
  ADD COLUMN IF NOT EXISTS band_dtypes TEXT[],
  ALTER COLUMN sensor_type TYPE sensor_type_enum USING lower(sensor_type)::sensor_type_enum;

CREATE TABLE vector_layers (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  source_format asset_format_enum NOT NULL,
  source_path TEXT NOT NULL,
  table_name TEXT UNIQUE,          -- actual PostGIS table published to Martin
  geom_type TEXT,                   -- Point / LineString / Polygon / …
  srid INT DEFAULT 4326,
  feature_count INT,
  attributes JSONB,                 -- schema introspection
  classification intel_classification DEFAULT 'unclassified',
  owner_id UUID REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE fmv_clips (
  id SERIAL PRIMARY KEY,
  name TEXT,
  file_path TEXT UNIQUE NOT NULL,      -- original MP4/TS
  hls_path TEXT NOT NULL,              -- /data/fmv/<id>/index.m3u8
  duration_s NUMERIC,
  fps NUMERIC,
  width INT, height INT,
  start_time TIMESTAMPTZ,
  end_time TIMESTAMPTZ,
  frame_track GEOMETRY(LINESTRINGZM, 4326), -- z=alt m, m=epoch_ms  (sensor path)
  klv_path TEXT,                        -- path to klv.jsonl
  pass_id INT REFERENCES satellite_passes(id) NULL,
  classification intel_classification DEFAULT 'unclassified',
  owner_id UUID REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE fmv_frame_detections (
  id BIGSERIAL PRIMARY KEY,
  clip_id INT REFERENCES fmv_clips(id) ON DELETE CASCADE,
  frame_idx INT NOT NULL,
  frame_time NUMERIC NOT NULL,       -- seconds from clip start
  det_class TEXT,
  confidence REAL,
  track_id INT,                      -- SORT / ByteTrack id
  bbox_px JSONB,                     -- [x,y,w,h] pixel space
  geom GEOMETRY(POLYGON, 4326),      -- geo-projected corner points
  centroid GEOMETRY(POINT, 4326),
  UNIQUE(clip_id, frame_idx, track_id, det_class)
);
CREATE INDEX ON fmv_frame_detections USING GIST(geom);
CREATE INDEX ON fmv_frame_detections(clip_id, frame_time);

CREATE TABLE tiles_3d (
  id SERIAL PRIMARY KEY,
  name TEXT, root_url TEXT,
  source_format asset_format_enum,
  bbox GEOMETRY(POLYGON,4326),
  min_z REAL, max_z REAL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE aois (                      -- Areas / Named Areas of Interest
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  kind TEXT,                              -- NAI | TAI | PIR | AOR | fence
  geom GEOMETRY(GEOMETRY, 4326) NOT NULL, -- any type
  priority INT DEFAULT 3,                 -- 1 = highest
  active BOOL DEFAULT true,
  owner_id UUID REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ON aois USING GIST(geom);

CREATE TABLE geofence_triggers (         -- per-AOI alerting rules
  id SERIAL PRIMARY KEY,
  aoi_id INT REFERENCES aois(id) ON DELETE CASCADE,
  match_kind TEXT,                        -- 'enter' | 'exit' | 'dwell' | 'detection_class'
  params JSONB,
  notify_users UUID[]
);

CREATE TABLE feed_sources (
  id SERIAL PRIMARY KEY,
  name TEXT,
  kind feed_kind_enum,
  config JSONB,
  enabled BOOL DEFAULT true,
  last_event_at TIMESTAMPTZ,
  owner_id UUID REFERENCES users(id)
);

CREATE TABLE feed_events (
  id BIGSERIAL PRIMARY KEY,
  feed_id INT REFERENCES feed_sources(id),
  geom GEOMETRY(POINT,4326),
  props JSONB,
  event_time TIMESTAMPTZ DEFAULT NOW(),
  ingest_time TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ON feed_events USING GIST(geom);
CREATE INDEX ON feed_events(event_time DESC);
CREATE INDEX ON feed_events(feed_id, event_time DESC);

CREATE TABLE tracks (                  -- fused multi-sensor tracks
  id SERIAL PRIMARY KEY,
  track_uuid UUID UNIQUE DEFAULT gen_random_uuid(),
  classification intel_classification DEFAULT 'unclassified',
  label TEXT,
  first_seen TIMESTAMPTZ,
  last_seen TIMESTAMPTZ,
  path GEOMETRY(LINESTRINGZM, 4326),
  aggregate_props JSONB
);

CREATE TABLE track_points (
  id BIGSERIAL PRIMARY KEY,
  track_id INT REFERENCES tracks(id) ON DELETE CASCADE,
  t TIMESTAMPTZ NOT NULL,
  geom GEOMETRY(POINT, 4326),
  altitude_m REAL,
  speed_mps REAL,
  heading_deg REAL,
  source_kind TEXT,                    -- 'ais' | 'adsb' | 'detection' | 'fmv'
  source_id BIGINT,
  confidence REAL
);
CREATE INDEX ON track_points(track_id, t DESC);
CREATE INDEX ON track_points USING GIST(geom);

CREATE TABLE collection_tasks (
  id SERIAL PRIMARY KEY,
  aoi_id INT REFERENCES aois(id),
  requested_by UUID REFERENCES users(id),
  sensor_mix sensor_type_enum[],
  window_start TIMESTAMPTZ,
  window_end TIMESTAMPTZ,
  eeis JSONB,                            -- essential elements of information
  priority INT DEFAULT 3,
  status job_status_enum DEFAULT 'pending',
  planned_pass_ids INT[],                -- references satellite_passes
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE change_events (
  id SERIAL PRIMARY KEY,
  aoi_id INT REFERENCES aois(id),
  pass_before INT REFERENCES satellite_passes(id),
  pass_after  INT REFERENCES satellite_passes(id),
  metric TEXT,                           -- 'ndvi_delta' | 'intensity' | 'sar_coherence'
  delta_stats JSONB,
  hotspot GEOMETRY(MULTIPOLYGON, 4326),
  confidence REAL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ON change_events USING GIST(hotspot);

CREATE TABLE models (
  id SERIAL PRIMARY KEY,
  name TEXT, version TEXT,
  sensor_type sensor_type_enum,
  framework TEXT,
  weights_path TEXT,
  weights_sha256 CHAR(64),
  classes JSONB, metrics JSONB,
  mlflow_run_id TEXT,
  status TEXT DEFAULT 'staged',          -- staged | production | archived
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(name, version)
);

CREATE TABLE training_jobs (
  id SERIAL PRIMARY KEY,
  model_name TEXT, base_weights TEXT,
  dataset_path TEXT, hyperparams JSONB,
  status job_status_enum DEFAULT 'pending',
  celery_task_id TEXT,
  mlflow_run_id TEXT,
  metrics JSONB,
  log_path TEXT,
  owner_id UUID REFERENCES users(id),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);

CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY,
  t TIMESTAMPTZ DEFAULT NOW(),
  user_id UUID REFERENCES users(id),
  action TEXT,
  resource TEXT,
  resource_id TEXT,
  metadata JSONB
);
CREATE INDEX ON audit_log(t DESC);
CREATE INDEX ON audit_log(user_id, t DESC);
```

---

<a id="library--component-choices"></a>
## 3. Library & Component Choices

| Concern | Choice | Rationale |
|---|---|---|
| NITF | GDAL `NITF` driver via existing `rasterio==1.3.x` | Built-in to GDAL; routes through extended `ensure_cog()` |
| Vector ingest | `geopandas==0.14.4` + `pyogrio` + `fastkml==1.0` + `lxml` | SHP/GeoJSON/GPKG/KML all through one API; `to_postgis` writes atomically |
| FMV transcoding | `ffmpeg-python==0.2.0` wrapping `ffmpeg 6.x` | Fragmented MP4 + HLS with passthrough, KLV stream copy |
| FMV metadata | `klvdata==0.0.10` (MISB ST 0601.17) | Parses sensor pose, corner points, LOS, FOV per-frame |
| Object tracking | `norfair==2.2` (Kalman + Hungarian) | Pure-Python, offline, swap for ByteTrack later |
| 3D Tiles serve | `py3dtiles==7.0` → nginx static | Deterministic, cacheable, no runtime compute |
| CityGML → 3D Tiles | `citygml-tools` (Java CLI) in worker | Mature converter; ingest-time only |
| 3D globe | **CesiumJS 1.124** (OSS build, `CESIUM_BASE_URL=/cesium/`) | Native 3D Tiles + KML + glTF + CZML; offline when assets vendored |
| Multispectral viz | TiTiler `expression` + `rescale` + `colormap_name` | Server-side band math with `rio-tiler` |
| SAR / Thermal viz | TiTiler `rescale` (dB stretch) + custom matplotlib colormaps via `TITILER_API_CMAP_DIRECTORY` | Reuses TiTiler; colormaps shipped as JSON in image |
| Real-time | FastAPI WebSocket + `redis.asyncio` pub/sub | Native, low-latency, replaces 10 s polling |
| AIS feed | `pyais==2.6` | Active library, parses NMEA + AIVDM |
| ADS-B feed | `pyModeS==2.18` | Mode S + ADS-B decoder |
| RSS feed | `feedparser==6.0.11` | — |
| MQTT feed | `paho-mqtt==1.6` | — |
| STIX 2.1 feed | `stix2==3.0` | TAXII pull + STIX parse for threat intel |
| HTTP poll | `httpx==0.27` | Async poller framework |
| Training orchestration | Ultralytics `yolo train` on `training` Celery queue + **MLflow 2.14** registry | Free UI, sqlite backend, local artifact root |
| Auth | `fastapi-users[sqlalchemy]==13.0` + JWT + `asyncpg` | Battle-tested, Postgres-backed |
| Migrations | **Alembic 1.13** | Versioned change; retires `init_postgis.sql` |
| Offline basemap | Pre-built `world.mbtiles` served by Martin | Replaces CARTO CDN |
| Offline geocoding | **Nominatim** (OSS) on local OSM extract | Reverse + forward geocoding, optional |
| Offline routing | **OSRM** (OSS) on local OSM extract | Isochrones (drive-time) |
| DEM / terrain | **SRTM 30 m** (public domain) as COG, + `whitebox==2.3` | Viewshed, LOS, slope, aspect |
| Change detection | `rasterio-cogeo` + NumPy + `scikit-image` (Otsu, SSIM) | Pairwise raster diff |
| Orbital propagation | `sgp4==2.23` (pip), TLE stored in Neo4j | Real TCA, visibility windows |
| Solar angle | `pysolar==0.11` | Shadow analysis from sun elevation/azimuth |
| Reports | `reportlab==4.0` + `python-pptx==0.6` + `weasyprint==62` | Offline PDF / PPTX |
| Frontend state | `zustand==4.5` + `@tanstack/react-query==5` | Cache + WS merge |

---

<a id="phase-0"></a>
## 4. Phase 0 — Auth, Schema, Migrations, DevEx *(Size: M, ~3 days)*

**Goal:** Every later phase assumes auth-gated routes and Alembic-controlled schema. This phase also fixes the two blocking new bugs (**N1** missing GDAL, **N4** missing Celery result backend) that would otherwise make Phase 1 untestable.

### 4.1 Files

**Create**
- [backend/alembic.ini](backend/alembic.ini)
- [backend/migrations/env.py](backend/migrations/env.py)
- [backend/migrations/versions/0001_baseline.py](backend/migrations/versions/0001_baseline.py) — ports [backend/init_postgis.sql](backend/init_postgis.sql) verbatim
- [backend/migrations/versions/0002_auth_and_extensions.py](backend/migrations/versions/0002_auth_and_extensions.py) — enums, users, vector_layers, fmv_clips, tiles_3d, aois, feed_sources, feed_events, tracks, track_points, models, training_jobs, audit_log, change_events, collection_tasks
- [backend/auth/__init__.py](backend/auth/__init__.py), [backend/auth/users.py](backend/auth/users.py), [backend/auth/router.py](backend/auth/router.py), [backend/auth/schemas.py](backend/auth/schemas.py)
- [backend/deps.py](backend/deps.py) — `current_active_user`, `require_role("admin"|"collection_mgr"|"analyst")`, `audit_dep` factory
- [backend/settings.py](backend/settings.py) — pydantic-settings; single source of env truth
- [backend/async_db.py](backend/async_db.py) — async SQLAlchemy engine + `asyncpg` for auth/WS

**Modify**
- [backend/main.py:12-18](backend/main.py#L12-L18) — tighten CORS, mount auth router, attach audit middleware
- [backend/worker.py:18](backend/worker.py#L18) — add `backend=os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)` (fix **N4**)
- [backend/worker.py:262-271](backend/worker.py#L262-L271) — wrap WKT in `ST_Multi(ST_GeomFromText(...))` (fix **N9**)
- [backend/Dockerfile:4-11](backend/Dockerfile#L4-L11) — add `gdal-bin python3-gdal libgdal-dev` (fix **N1**)
- [backend/requirements.txt](backend/requirements.txt) — add `alembic==1.13`, `sqlalchemy==2.0`, `fastapi-users[sqlalchemy]==13.0`, `asyncpg==0.29`, `pydantic-settings==2.3`
- [docker-compose.yml:31](docker-compose.yml#L31) — remove `init_postgis.sql` bind-mount; add depends_on for redis, titiler; backend `command: bash -c "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8080"`
- [backend/ai.py:14, 32](backend/ai.py#L14) — drop `"dummy"` default; set `allow_dangerous_requests=False`; use a read-only Neo4j user

### 4.2 Architecture notes

```
 Client ──► nginx ──► /auth/* ──► fastapi-users ──► JWT access + refresh
                     │                                    │
                     │                                    ▼
                     │                              users table (Postgres)
                     ▼
                  /api/*  (Depends(current_active_user))
                     │
                     ▼
                  Route logic  ──► Audit middleware ──► audit_log
```

### 4.3 Pseudocode — `deps.py`

```python
# backend/deps.py
async def current_active_user(
    token: str = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_async_session),
) -> User:
    payload = jwt_decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    user = await session.get(User, UUID(payload["sub"]))
    if not user or not user.is_active:
        raise HTTPException(401, "inactive")
    return user

def require_role(*allowed: str):
    async def _dep(user: User = Depends(current_active_user)) -> User:
        if user.is_superuser:
            return user
        if user.role not in allowed:
            raise HTTPException(403, f"requires role in {allowed}")
        return user
    return _dep

def audit(action: str, resource: str):
    async def _dep(
        req: Request,
        user: User = Depends(current_active_user),
        session: AsyncSession = Depends(get_async_session),
    ):
        session.add(AuditLog(
            user_id=user.id, action=action, resource=resource,
            resource_id=req.path_params.get("id"),
            metadata={"ip": req.client.host, "ua": req.headers.get("user-agent")}))
        await session.commit()
        return user
    return _dep
```

### 4.4 Verification checklist

- [ ] `docker compose build backend && docker compose run --rm backend alembic upgrade head` returns `INFO [alembic.runtime.migration] Running upgrade  -> 0001_baseline, baseline`
- [ ] `psql $POSTGIS_URI -c "\dT+"` lists every enum; `\dt` lists every new table
- [ ] `curl -X POST localhost:8080/auth/register -d '{"email":"a@b.c","password":"testtest12"}'` → 201
- [ ] `curl -X POST localhost:8080/auth/jwt/login -d 'username=a@b.c&password=testtest12' -H 'Content-Type: application/x-www-form-urlencoded'` → JWT
- [ ] `curl localhost:8080/api/targets` (no JWT) → **401** (proves auth wiring)
- [ ] `docker compose exec worker celery -A worker inspect ping` → pong (proves N4 fix)
- [ ] `docker compose exec worker gdal_translate --version` → `GDAL 3.x` (proves N1 fix)
- [ ] Commit: `feat(auth): fastapi-users JWT, alembic baseline, role deps; fix GDAL + celery backend`

---

<a id="phase-1"></a>
## 5. Phase 1 — Multi-Format Ingest *(Size: L, ~5 days)*

**Goal:** One drag-drop UI, one auth-gated `POST /api/ingest/upload`, and a **handler registry** dispatches by extension to the right Celery task. Supported formats:

| Extension | Handler | Worker queue | Output |
|---|---|---|---|
| `.tif .tiff .ntf .nitf .jp2 .img` | `process_raster` | `imagery` | COG → `satellite_passes` row |
| `.nc .netcdf .h5 .hdf` | `process_raster_netcdf` | `imagery` | per-time-step COG (or Zarr) |
| `.shp .zip(shp) .gpkg .geojson .json .kml .kmz` | `process_vector` | `vector` | PostGIS table + `vector_layers` row |
| `.mp4 .mpeg .ts .mov` | `process_fmv` | `video` | HLS + `fmv_clips` + klv.jsonl |
| `.zip(3dtileset) .citygml .gml .las .laz` | `process_3d` | `imagery` | 3D Tiles tileset + `tiles_3d` row |

### 5.1 Files

**Create**
- [backend/ingest/__init__.py](backend/ingest/__init__.py)
- [backend/ingest/formats.py](backend/ingest/formats.py) — registry, sniffing
- [backend/ingest/routes.py](backend/ingest/routes.py) — upload + job status
- [backend/ingest/chunked.py](backend/ingest/chunked.py) — tus-style resumable for >2 GB files
- [backend/workers/vector.py](backend/workers/vector.py)
- [backend/workers/video.py](backend/workers/video.py)
- [backend/workers/tiles3d.py](backend/workers/tiles3d.py)
- [frontend/src/components/UploadCenter.tsx](frontend/src/components/UploadCenter.tsx)
- [frontend/src/components/UploadRow.tsx](frontend/src/components/UploadRow.tsx)
- [frontend/src/lib/apiClient.ts](frontend/src/lib/apiClient.ts) — generated from OpenAPI

**Modify**
- [backend/main.py](backend/main.py) — mount ingest router; register every new endpoint
- [backend/worker.py](backend/worker.py) — split `process_satellite_imagery` into `process_raster`, keep the raster-specific logic
- [backend/worker.py:108-116](backend/worker.py#L108-L116) — **fix N3**: write chip as GeoTIFF, not PNG; send bytes with MIME `image/tiff` and let inference accept TIFF
- [backend/worker.py:99](backend/worker.py#L99) — **fix N2**: `nodata_test = src.nodata if src.nodata is not None else None; if nodata_test is not None and np.all(chip == nodata_test): continue`
- [backend/Dockerfile](backend/Dockerfile) — add `apt-get install ffmpeg openjdk-17-jre-headless unzip`, copy `citygml-tools` jar into `/opt/`
- [backend/requirements.txt](backend/requirements.txt) — add `geopandas==0.14.4`, `pyogrio==0.9`, `fastkml==1.0`, `lxml==5.2`, `ffmpeg-python==0.2.0`, `klvdata==0.0.10`, `py3dtiles==7.0`, `python-magic==0.4`

**Frontend**
- [frontend/src/App.tsx:10-23](frontend/src/App.tsx#L10-L23) — add `upload`, `fmv`, `space3d`, `admin` tabs
- [frontend/package.json](frontend/package.json) — add `react-dropzone`, `@tanstack/react-query`, `zustand`, `sonner`, `hls.js`, `cesium@1.124`, `vite-plugin-cesium`, `maplibre-gl`, `deck.gl`

### 5.2 Architecture — Handler Registry

```python
# backend/ingest/formats.py
from dataclasses import dataclass

@dataclass(frozen=True)
class Handler:
    task_name: str     # celery signature string, e.g. "workers.vector.process_vector"
    queue: str
    accept_exts: frozenset[str]
    content_sniff: callable | None = None  # optional libmagic check

REGISTRY: tuple[Handler, ...] = (
    Handler("workers.raster.process_raster",
            "imagery",
            frozenset({".tif",".tiff",".ntf",".nitf",".jp2",".img"})),
    Handler("workers.raster.process_raster_netcdf",
            "imagery",
            frozenset({".nc",".netcdf",".h5",".hdf"})),
    Handler("workers.vector.process_vector",
            "vector",
            frozenset({".shp",".gpkg",".geojson",".json",".kml",".kmz",".zip"}),
            content_sniff=lambda path: _sniff_vector(path)),
    Handler("workers.video.process_fmv",
            "video",
            frozenset({".mp4",".mov",".ts",".mpeg"})),
    Handler("workers.tiles3d.process_3d",
            "imagery",
            frozenset({".citygml",".gml",".las",".laz"}),
            content_sniff=lambda path: _sniff_tileset_zip(path)),
)

def dispatch(path: Path) -> Handler:
    ext = path.suffix.lower()
    for h in REGISTRY:
        if ext in h.accept_exts:
            if h.content_sniff is None or h.content_sniff(path):
                return h
    raise UnsupportedFormat(ext)
```

### 5.3 Pseudocode — `POST /api/ingest/upload`

```python
# backend/ingest/routes.py
@router.post("/upload", dependencies=[Depends(audit("upload", "ingest"))])
async def upload(
    file: UploadFile,
    user: User = Depends(current_active_user),
    celery: Celery = Depends(get_celery),
):
    uuid4 = uuid.uuid4().hex
    dest_dir = Path(settings.IMAGERY_PATH) / "incoming" / uuid4
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_filename(file.filename)

    # stream-to-disk, never buffer in memory
    async with aiofiles.open(dest, "wb") as f:
        async for chunk in file.stream(1 << 20):     # 1 MiB
            await f.write(chunk)

    # hash for dedupe + integrity
    sha = sha256_file(dest)

    handler = dispatch(dest)
    task = celery.send_task(
        handler.task_name,
        args=[str(dest), uuid4, str(user.id)],
        queue=handler.queue,
    )
    return {
        "task_id": task.id,
        "handler": handler.task_name,
        "sha256": sha,
        "upload_id": uuid4,
        "status_url": f"/api/ingest/jobs/{task.id}",
    }
```

### 5.4 Pseudocode — `process_vector`

```python
# backend/workers/vector.py
@celery_app.task(name="workers.vector.process_vector", queue="vector", bind=True)
def process_vector(self, path: str, upload_id: str, owner_id: str):
    path = Path(path)
    # KMZ → extract .kml; Shapefile ZIP → unpack
    if path.suffix.lower() == ".zip":
        path = _unpack_vector_zip(path)
    if path.suffix.lower() == ".kmz":
        path = _unpack_kmz(path)

    # read via pyogrio; preserve CRS and attributes
    import geopandas as gpd
    gdf = gpd.read_file(path, engine="pyogrio").to_crs(4326)
    if gdf.empty:
        raise ValueError("empty vector")

    table_name = f"vec_{upload_id[:12]}"
    # write via pyogrio engine to bypass SQLAlchemy + psycopg2 slowness
    gdf.to_postgis(
        table_name,
        con=engine_sync(),
        if_exists="replace",
        index=False,
        chunksize=10_000,
    )

    geom_type = gdf.geom_type.value_counts().idxmax()
    with pg_sync() as cur:
        cur.execute("""
            INSERT INTO vector_layers
              (name, source_format, source_path, table_name, geom_type,
               srid, feature_count, attributes, owner_id)
            VALUES (%s, %s, %s, %s, %s, 4326, %s, %s::jsonb, %s)
            RETURNING id
        """, (path.stem, _fmt_enum(path.suffix), str(path),
              table_name, geom_type, len(gdf),
              json.dumps({c: str(gdf[c].dtype) for c in gdf.columns}),
              owner_id))
        layer_id = cur.fetchone()["id"]

    # notify Martin to pick up the new table (Martin watches pg_class via cache timeout;
    # we can also signal it via REST reload)
    requests.post(f"{settings.MARTIN_URL}/catalog/refresh", timeout=5)

    redis.publish("events:ingest",
        json.dumps({"kind":"vector","layer_id":layer_id,"table":table_name,
                    "feature_count": len(gdf), "bbox": list(gdf.total_bounds)}))

    return {"layer_id": layer_id, "table_name": table_name}
```

### 5.5 Pseudocode — `process_raster` (extended from today's worker)

```python
# backend/workers/raster.py
@celery_app.task(name="workers.raster.process_raster", queue="imagery", bind=True)
def process_raster(self, path: str, upload_id: str, owner_id: str):
    p = Path(path); cog = Path(settings.IMAGERY_PATH) / "processed" / f"{upload_id}_cog.tif"
    cog.parent.mkdir(parents=True, exist_ok=True)

    # 1) NITF / GeoTIFF / JP2 all flow through GDAL translate
    ensure_cog_gdal(p, cog)        # keeps existing behaviour, drops extension whitelist

    # 2) introspect bands → band_count, band_names, band_dtypes for sensor viz
    with rasterio.open(cog) as src:
        band_info = {
            "count": src.count,
            "descriptions": list(src.descriptions or []),
            "dtypes": list(src.dtypes),
            "nodata": src.nodata,
            "crs": str(src.crs),
        }
        footprint = _footprint_multipoly(src)   # returns ST_Multi-safe polygon

    # 3) catalog
    with pg_sync(commit=True) as cur:
        cur.execute("""
            INSERT INTO satellite_passes
              (name, file_path, sensor_type, acquisition_time, footprint,
               crs, band_count, band_names, band_dtypes, owner_id, format)
            VALUES (%s, %s, %s, %s, ST_Multi(ST_GeomFromText(%s, 4326)),
                    %s, %s, %s, %s, %s, %s::asset_format_enum)
            RETURNING id
        """, (p.name, str(cog), _infer_sensor(p, band_info),
              _infer_acq_time(p), footprint.wkt, band_info["crs"],
              band_info["count"], band_info["descriptions"],
              band_info["dtypes"], owner_id, _fmt_enum(p.suffix)))
        pass_id = cur.fetchone()["id"]

    # 4) Neo4j node
    _neo4j_create_pass(pass_id, cog, band_info)

    # 5) chain → tiling inference
    celery_app.send_task("workers.raster.slice_and_infer",
                         args=[str(cog), pass_id],
                         queue="imagery")

    redis.publish("events:ingest",
        json.dumps({"kind":"raster","pass_id":pass_id,
                    "bbox":list(footprint.bounds),"band_count":band_info["count"]}))
    return {"pass_id": pass_id, "cog": str(cog)}
```

### 5.6 Verification

- [ ] Drag each format into the UI; watch `status_url` poll transition `pending → running → succeeded`
- [ ] `psql -c "SELECT count(*) FROM vector_layers"` reflects each upload
- [ ] `curl $MARTIN_URL/<table_name>/0/0/0.pbf | file -` reports `protobuf`
- [ ] Inference chip TIFF now returns HTTP 200 from `/detect` (was 400 for multi-band float, fixes N3)

---

<a id="phase-2"></a>
## 6. Phase 2 — Sensor-Aware Visualization *(Size: M, ~3 days)*

**Goal:** One imagery layer in the UI can be rendered in the mode appropriate to its sensor — RGB, grayscale single-band, band-math index, SAR dB stretch, thermal ironbow. No new server code; we lean on TiTiler's `expression` + `rescale` + `colormap_name` parameters.

### 6.1 Supported modes

| Mode | Sensor | Params to TiTiler |
|---|---|---|
| `rgb` | optical / multispectral | `bidx=R,G,B&rescale=…` per channel |
| `single` | panchromatic / SAR / thermal | `bidx=N&rescale=min,max&colormap_name=…` |
| `ndvi` | multispectral (NIR, Red) | `expression=(b8-b4)/(b8+b4)&rescale=-1,1&colormap_name=ndvi` |
| `ndwi` | MSI (Green, NIR) | `expression=(b3-b8)/(b3+b8)&rescale=-1,1&colormap_name=water` |
| `nbr`  | MSI (NIR, SWIR) | `expression=(b8-b12)/(b8+b12)&rescale=-1,1&colormap_name=burn` |
| `sar_db` | SAR (single-band float) | `expression=10*log10(b1)&rescale=-25,5&colormap_name=sar_grayscale` |
| `thermal_k` | Thermal (Kelvin single band) | `rescale=270,320&colormap_name=thermal_ironbow` |

### 6.2 Files

**Create**
- [backend/colormaps/thermal_ironbow.json](backend/colormaps/thermal_ironbow.json)
- [backend/colormaps/sar_grayscale.json](backend/colormaps/sar_grayscale.json)
- [backend/colormaps/ndvi.json](backend/colormaps/ndvi.json)
- [backend/colormaps/water.json](backend/colormaps/water.json)
- [backend/colormaps/burn.json](backend/colormaps/burn.json)
- [backend/routes/imagery.py](backend/routes/imagery.py) — new `GET /api/imagery/{pass_id}/bands` returns `band_count`, `band_names`, `band_dtypes`, statistics
- [frontend/src/components/SensorControls.tsx](frontend/src/components/SensorControls.tsx)
- [frontend/src/lib/titilerUrl.ts](frontend/src/lib/titilerUrl.ts) — pure builder
- [frontend/src/lib/sensorPresets.ts](frontend/src/lib/sensorPresets.ts)

**Modify**
- [frontend/src/components/GaiaMap.tsx:285-292](frontend/src/components/GaiaMap.tsx#L285-L292) — replace static TiTiler URL with `useMemo` of `buildTitilerUrl(selectedImagery, sensorMode, sensorParams)`
- [docker-compose.yml](docker-compose.yml) — titiler gets `TITILER_API_CMAP_DIRECTORY=/cmap`, volume `./backend/colormaps:/cmap:ro`

### 6.3 Pseudocode — `titilerUrl.ts`

```ts
// frontend/src/lib/titilerUrl.ts
export type Mode = "rgb" | "single" | "ndvi" | "ndwi" | "nbr" | "sar_db" | "thermal_k" | "custom";
export interface SensorState {
  mode: Mode;
  bands: { r?: number; g?: number; b?: number; n?: number };
  expression?: string;                  // custom
  rescale: [number, number][];          // one tuple per output channel
  colormap?: string;                    // server-side name
  gamma?: number;
}

export function buildTitilerUrl(url: string, s: SensorState): string {
  const p = new URLSearchParams();
  p.set("url", url);
  if (s.mode === "rgb")
    p.set("bidx", [s.bands.r, s.bands.g, s.bands.b].join(","));
  else if (s.mode === "single")
    p.set("bidx", String(s.bands.n ?? 1));
  else
    p.set("expression", s.expression ?? presetExpression(s.mode, s.bands));
  if (s.rescale?.length)
    p.set("rescale", s.rescale.map(([lo, hi]) => `${lo},${hi}`).join(";"));
  if (s.colormap)
    p.set("colormap_name", s.colormap);
  if (s.gamma)
    p.set("gamma", String(s.gamma));
  return `${TITILER_URL}/cog/tiles/{z}/{x}/{y}?${p.toString()}`;
}
```

### 6.4 Verification

- [ ] Load a Sentinel-2 COG, set mode=`ndvi`, expect green/brown ramp at zoom 8–12
- [ ] Load a Sentinel-1 SAR (VV single-band float32), mode=`sar_db`, rescale `-25,5`, expect realistic grayscale
- [ ] Load a Landsat 8 TIRS band (Kelvin), mode=`thermal_k`, rescale `270,320`, expect ironbow ramp

---

<a id="phase-3"></a>
## 7. Phase 3 — FMV / Video Exploitation with Frame-Synchronized Overlays *(Size: XL, ~7 days)*

**This is the flagship phase for the "video with overlays" requirement.** Full-motion video exploitation is the single most data-dense analyst task in a GEOINT workflow — we give it an exploitation surface as rich as the still-imagery map.

### 7.1 End-state behavior

```
┌────────────────────────────────── FMV Viewer Tab ───────────────────────────────────┐
│                                                                                     │
│  ┌─────────────────────────── <video> (hls.js) ────────────────────────┐  ┌──────┐ │
│  │                                                                     │  │      │ │
│  │  Drone feed / satellite FMV — 1920x1080 @ 30 fps                    │  │ Sub- │ │
│  │                                                                     │  │ map  │ │
│  │    ┌───────────── detection bbox (Vessel 0.92) ─────────────┐       │  │      │ │
│  │    │                                                        │       │  │ green│ │
│  │    │    ┌─ track line (track_id 17) ─┐                      │       │  │ poly-│ │
│  │    │    └──────────────────────────► │                      │       │  │ gon  │ │
│  │    │                                                        │       │  │ =    │ │
│  │    │                                                        │       │  │ frame│ │
│  │    │                                                        │       │  │ foot-│ │
│  │    └────────────────────────────────────────────────────────┘       │  │ print│ │
│  │                                                                     │  │      │ │
│  │  ┌──────── HUD ────────┐           ┌── Reticle (LOS) ──┐            │  │ →→→  │ │
│  │  │ t=00:47.200         │           │       +           │            │  │sensor│ │
│  │  │ alt  1450 m         │           └───────────────────┘            │  │ path │ │
│  │  │ hdg  213°           │                                            │  │      │ │
│  │  │ FOV   4.2° x 2.4°   │                                            │  │      │ │
│  │  │ zoom 12x            │                                            │  │      │ │
│  │  └─────────────────────┘                                            │  │      │ │
│  │                                                                     │  │      │ │
│  └─────────────────────────────────────────────────────────────────────┘  └──────┘ │
│                                                                                     │
│  ┌──────────────── Timeline with detection spikes, scrubber, markers ──────────┐   │
│  │ ━━━━━━━━━━━▲━━━━━━━━━━━━━━━━━━━━▲━━━━━━━━━━━━━━━━━━━▲━━━━━━━━━━━━━━━━━━━  │   │
│  │         det spike                 AOI crossing          target callout       │   │
│  └────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                     │
│  [ ◀◀ ]  [ ◀ ]  [ ▶ / ❚❚ ]  [ ▶ ]  [ ▶▶ ]    [Extract chip]  [Pin AOI]  [Export] │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

Every overlay element below is synchronized to `<video>.currentTime`:

1. **Detection bounding boxes** — per-frame inference results (Phase 3.4) rendered in a `<canvas>` absolutely positioned over `<video>`. At each `requestAnimationFrame` we look up the nearest frame index and draw boxes.
2. **Track lines** — polylines in the same `<canvas>`, showing the last N=60 frames of each tracked object, fading with age.
3. **Target callouts** — if a detection is resolved to an existing `Target` (Phase 3.5), label and colour change; callout renders target id.
4. **HUD** — `currentTime`, altitude, heading, FOV, zoom pulled from KLV track at nearest timestamp.
5. **Reticle** — the sensor line-of-sight impact point (from KLV fields `Sensor Latitude/Longitude/Elevation` + `Sensor Relative Azimuth/Elevation`) projected into pixel space, drawn as a `+` mark.
6. **Sub-map frame footprint** — the 4 KLV corner points form a polygon; re-drawn each frame on a Leaflet sub-map. Historical footprints fade. Gives analyst the "where does this video look right now?" answer.
7. **Timeline markers** — detection spikes (red), AOI crossings (amber), user-pinned moments (blue). Clicking jumps `currentTime`.

### 7.2 Architecture — ingest to playback

```
 Upload FMV (.mp4/.ts)                                           Analyst browser
         │                                                              │
         ▼                                                              │
  process_fmv (celery, queue=video)                                     │
   ├── ffprobe       → fps, duration, w, h, pixel_format                │
   ├── ffmpeg -c copy -f hls -hls_time 4 -hls_segment_type fmp4         │
   │      -map 0:v -map 0:a? -map 0:d?                                  │
   │      /data/fmv/<id>/index.m3u8                                     │
   ├── klv extract:  ffmpeg -map 0:d:0 -f data klv.bin                  │
   ├── klvdata.StreamParser(klv.bin)                                    │
   │      → klv.jsonl  (one line per packet, normalised MISB fields)    │
   ├── build LINESTRINGZM frame_track from sensor positions             │
   ├── extract N thumbs (every 5 s) → thumbs/                           │
   ├── INSERT INTO fmv_clips                                            │
   ├── chain → infer_fmv (celery, queue=video)                          │
   └── redis.publish("events:ingest", {kind:"fmv", clip_id})            │
                                                                        │
  infer_fmv                                                             │
   ├── extract frames @ configurable stride (default every 5th frame)   │
   ├── POST each frame → inference /detect                              │
   ├── norfair.Tracker.update(detections) across frames                 │
   ├── for each tracked detection:                                      │
   │     corners → interp KLV → geo-project pixel → geom POLYGON        │
   │     INSERT INTO fmv_frame_detections                               │
   └── redis.publish("events:fmv_detections", {clip_id, frame_idx})     │
                                                                        ▼
                                              GET /api/fmv/<id>  ─► clip metadata
                                              GET /api/fmv/<id>/klv ─► klv.jsonl
                                              GET /api/fmv/<id>/detections ─► rows
                                              GET /api/fmv/<id>/hls/*     ─► nginx static
                                              WS  events:fmv_detections[clip=id]
```

### 7.3 Files

**Create**
- [backend/workers/video.py](backend/workers/video.py) — `process_fmv`, `infer_fmv`
- [backend/workers/klv.py](backend/workers/klv.py) — MISB ST 0601 parser wrapper + corner-point → POLYGON builder
- [backend/routes/fmv.py](backend/routes/fmv.py) — list clips, stream KLV, stream detections
- [frontend/src/components/FmvViewer.tsx](frontend/src/components/FmvViewer.tsx)
- [frontend/src/components/FmvOverlayCanvas.tsx](frontend/src/components/FmvOverlayCanvas.tsx)
- [frontend/src/components/FmvSubMap.tsx](frontend/src/components/FmvSubMap.tsx)
- [frontend/src/components/FmvTimeline.tsx](frontend/src/components/FmvTimeline.tsx)
- [frontend/src/components/FmvHud.tsx](frontend/src/components/FmvHud.tsx)
- [frontend/src/lib/klvInterp.ts](frontend/src/lib/klvInterp.ts) — linear/slerp interp by time
- [frontend/src/lib/geoProject.ts](frontend/src/lib/geoProject.ts) — pixel ↔ geo using KLV corners
- [frontend/src/components/View3D.tsx](frontend/src/components/View3D.tsx) (Cesium viewer, bonus 3D)
- [frontend/public/cesium/](frontend/public/cesium/) — vendored `Workers/`, `Assets/`, `Widgets/`

**Modify**
- [nginx/tile-proxy.conf](nginx/tile-proxy.conf) — add location `/fmv/` serving `/data/fmv/` with `add_header Accept-Ranges bytes; gzip off; mp4;`
- [docker-compose.yml](docker-compose.yml) — mount `fmv_data` volume, shared readonly by nginx

### 7.4 Pseudocode — `process_fmv`

```python
# backend/workers/video.py
@celery_app.task(name="workers.video.process_fmv", queue="video", bind=True)
def process_fmv(self, path: str, upload_id: str, owner_id: str):
    src = Path(path)
    out_dir = Path(settings.FMV_PATH) / upload_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. probe
    meta = ffmpeg_probe(src)          # ffmpeg.probe() wrapper
    fps  = _rational(meta["streams"][0]["r_frame_rate"])
    dur  = float(meta["format"]["duration"])
    w, h = meta["streams"][0]["width"], meta["streams"][0]["height"]

    # 2. HLS fragmented-MP4 (copy codecs — no transcode)
    hls_idx = out_dir / "index.m3u8"
    (ffmpeg
        .input(str(src))
        .output(
            str(hls_idx),
            **{
                "c:v": "copy",
                "c:a": "copy",
                "map": "0",                       # keep all streams including data
                "hls_time": 4,
                "hls_segment_type": "fmp4",
                "hls_list_size": 0,
                "hls_segment_filename": str(out_dir / "seg_%05d.m4s"),
                "f": "hls",
            })
        .run(overwrite_output=True, quiet=True))

    # 3. extract KLV data stream (if present)
    klv_path = out_dir / "klv.jsonl"
    try:
        klv_bin = out_dir / "_tmp_klv.bin"
        ffmpeg.input(str(src)).output(str(klv_bin),
            **{"map": "0:d:0", "f": "data", "c:d": "copy"}).run(
            overwrite_output=True, quiet=True)
        rows = _parse_misb_0601(klv_bin)          # klvdata.StreamParser
        with open(klv_path, "w") as f:
            for r in rows: f.write(json.dumps(r) + "\n")
        os.unlink(klv_bin)
    except ffmpeg.Error:
        rows = []                                 # no KLV stream

    # 4. sensor path as LINESTRINGZM (z=alt, m=epoch_ms)
    if rows:
        coords = [(r["sensor_lon"], r["sensor_lat"], r.get("sensor_alt_m", 0),
                   int(r["unix_timestamp_us"] / 1000)) for r in rows]
        frame_track_wkt = f"LINESTRINGZM({','.join(f'{x} {y} {z} {m}' for x,y,z,m in coords)})"
    else:
        frame_track_wkt = None

    # 5. 6 thumbnails every dur/6 seconds
    _extract_thumbnails(src, out_dir / "thumbs", count=6)

    # 6. catalog
    with pg_sync(commit=True) as cur:
        cur.execute("""
            INSERT INTO fmv_clips
              (name, file_path, hls_path, duration_s, fps, width, height,
               start_time, end_time, frame_track, klv_path, owner_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                    CASE WHEN %s IS NULL THEN NULL ELSE ST_GeomFromText(%s, 4326) END,
                    %s, %s)
            RETURNING id
        """, (src.name, str(src), str(hls_idx), dur, fps, w, h,
              rows[0]["timestamp"] if rows else None,
              rows[-1]["timestamp"] if rows else None,
              frame_track_wkt, frame_track_wkt,
              str(klv_path), owner_id))
        clip_id = cur.fetchone()["id"]

    redis.publish("events:ingest",
        json.dumps({"kind":"fmv","clip_id":clip_id,"has_klv":bool(rows)}))

    # 7. chain inference
    celery_app.send_task("workers.video.infer_fmv", args=[clip_id], queue="video")
    return {"clip_id": clip_id}
```

### 7.5 Pseudocode — `infer_fmv` (tracking + geo-projection)

```python
# backend/workers/video.py
@celery_app.task(name="workers.video.infer_fmv", queue="video", bind=True)
def infer_fmv(self, clip_id: int):
    clip = _load_clip(clip_id)              # returns metadata incl. klv rows, src path
    tracker = norfair.Tracker(distance_function="euclidean", distance_threshold=50)

    stride = max(1, int(clip.fps) // 5)    # ~5 Hz inference
    klv_rows = _load_klv(clip.klv_path)    # list of dicts by timestamp

    with av.open(clip.file_path) as container:
        for frame_idx, frame in enumerate(container.decode(video=0)):
            if frame_idx % stride:
                continue
            t_s = float(frame.pts * frame.time_base)
            img = frame.to_ndarray(format="rgb24")

            resp = inference_client.detect_array(img, meta={"clip_id": clip_id})
            detections = [
                norfair.Detection(
                    points=np.array([[d["bbox"][0] * img.shape[1],
                                      d["bbox"][1] * img.shape[0]]]),
                    scores=np.array([d["confidence"]]),
                    data={"class": d["class"], "bbox": d["bbox"], "conf": d["confidence"]},
                ) for d in resp["detections"]
            ]
            tracked = tracker.update(detections)
            klv_now = _interp_klv(klv_rows, t_s)   # linear interp on t

            rows_to_insert = []
            for tr in tracked:
                if tr.last_detection is None: continue
                bbox_n = tr.last_detection.data["bbox"]     # normalised cx,cy,w,h
                px_poly = _bbox_to_px_polygon(bbox_n, img.shape)
                if klv_now is not None and klv_now.get("corner_latlons"):
                    geo_poly = _pixel_to_geo_polygon(px_poly, img.shape, klv_now["corner_latlons"])
                else:
                    geo_poly = None
                rows_to_insert.append({
                    "clip_id": clip_id, "frame_idx": frame_idx, "frame_time": t_s,
                    "det_class": tr.last_detection.data["class"],
                    "confidence": float(tr.last_detection.data["conf"]),
                    "track_id": tr.id,
                    "bbox_px": px_poly.tolist(),
                    "geom_wkt": geo_poly.wkt if geo_poly else None,
                })

            if rows_to_insert:
                _insert_frame_dets(rows_to_insert)
                redis.publish(f"events:fmv_detections:{clip_id}",
                    json.dumps({"frame_idx": frame_idx, "t": t_s,
                                "n": len(rows_to_insert)}))
```

### 7.6 Pseudocode — pixel-to-geo projection using KLV corner points

```python
# backend/workers/klv.py
def _pixel_to_geo_polygon(px_poly: np.ndarray, img_shape, corner_latlons):
    """
    px_poly: Nx2 array of (px_x, px_y) polygon corners.
    img_shape: (H, W, 3)
    corner_latlons: list of 4 (lat, lon) for TL, TR, BR, BL of video frame,
                    pulled from MISB 0601 fields 26-29 / 23-25 triplets.
    Maps pixel (0..W, 0..H) bi-linearly onto the ground quadrilateral
    defined by corner_latlons. This is a first-order approximation;
    for high-oblique shots we should intersect the camera ray with the DEM
    (implemented in Phase 6's ray_to_dem()).
    """
    H, W = img_shape[:2]
    TL, TR, BR, BL = corner_latlons
    pts = []
    for x, y in px_poly:
        u, v = x / W, y / H
        top    = _lerp_ll(TL, TR, u)
        bottom = _lerp_ll(BL, BR, u)
        p = _lerp_ll(top, bottom, v)
        pts.append((p[1], p[0]))           # lon, lat
    return shapely.geometry.Polygon(pts)
```

### 7.7 Frontend pseudocode — `FmvOverlayCanvas.tsx`

```tsx
// frontend/src/components/FmvOverlayCanvas.tsx
function FmvOverlayCanvas({ video, clipId }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const detsRef   = useRef<Detection[]>([]);          // loaded from /api/fmv/<id>/detections
  const klvRef    = useRef<KlvRow[]>([]);

  // initial load
  useEffect(() => {
    Promise.all([fetchDetections(clipId), fetchKlv(clipId)]).then(
      ([dets, klv]) => { detsRef.current = dets; klvRef.current = klv; }
    );
  }, [clipId]);

  // subscribe to WS for new frames as inference streams in
  useEventStream(`fmv_detections:${clipId}`, (evt) => {
    detsRef.current = mergeSorted(detsRef.current, evt.rows);
  });

  // rAF loop that draws the canvas in sync with <video>
  useEffect(() => {
    let raf: number;
    const ctx = canvasRef.current!.getContext("2d")!;
    const draw = () => {
      const t   = video.currentTime;
      const w   = canvasRef.current!.width  = video.videoWidth;
      const h   = canvasRef.current!.height = video.videoHeight;
      ctx.clearRect(0, 0, w, h);

      const frameDets = detsAtTime(detsRef.current, t, window=0.2);   // ±200 ms
      for (const d of frameDets) {
        drawBox(ctx, d.bbox_px, color(d.det_class), 2);
        drawLabel(ctx, d.bbox_px, `${d.det_class} ${(d.confidence*100).toFixed(0)}% #${d.track_id}`);
      }
      drawTrackTails(ctx, detsRef.current, t, tailFrames=60);

      const klv = interpKlv(klvRef.current, t);
      if (klv) drawReticle(ctx, klvToBoresightPx(klv, w, h));

      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [video]);

  return <canvas ref={canvasRef} className="absolute inset-0 pointer-events-none" />;
}
```

### 7.8 Frontend pseudocode — `FmvSubMap.tsx` (frame footprint follows playhead)

```tsx
function FmvSubMap({ video, klv }) {
  const [poly, setPoly] = useState<LatLng[]>([]);
  useAnimationFrame(() => {
    const row = interpKlv(klv, video.currentTime);
    if (!row?.corner_latlons) return;
    setPoly(row.corner_latlons);
  });
  return (
    <MapContainer center={[0,0]} zoom={3} style={{height: 220, width: 280}}>
      <TileLayer url={OFFLINE_MBTILES_URL} />
      <Polygon positions={poly} pathOptions={{ color: "#10b981", fillOpacity: 0.15 }} />
      <Polyline positions={sensorPathSoFar(klv, video.currentTime)}
                pathOptions={{ color: "#3b82f6", dashArray: "4,4" }} />
    </MapContainer>
  );
}
```

### 7.9 Verification

- [ ] Upload `sample.mp4` with embedded MISB KLV data stream → job succeeds, `fmv_clips` row created, `klv.jsonl` has ≥ 1 row per frame
- [ ] `/data/fmv/<id>/index.m3u8` plays in hls.js without transcoding artifacts
- [ ] On playhead scrub, sub-map polygon moves to match reported corner points within 100 ms of `currentTime`
- [ ] Detection boxes appear on-screen with label, disappear when out of ±200 ms window
- [ ] Track lines persist across N frames, fade with age
- [ ] Reticle coincides with sensor boresight centre on a calibrated test clip
- [ ] `psql -c "SELECT count(*) FROM fmv_frame_detections WHERE clip_id=<id>"` > 0

---

<a id="phase-4"></a>
## 8. Phase 4 — Real-Time Feeds + WebSocket Fanout *(Size: M, ~3 days)*

**Goal:** Replace the 10-second polling loop ([GaiaMap.tsx:99](frontend/src/components/GaiaMap.tsx#L99)) with JWT-gated WebSocket pub/sub over Redis, and add live ingestors (AIS, ADS-B, RSS, MQTT, STIX TAXII) fed through a common pipeline that yields `feed_events`.

### 8.1 Architecture

```
 ┌─ External sources ─────────────────────────┐
 │ AIS (NMEA / dAISy / sample log)            │
 │ ADS-B (dump1090 TCP 30003)                 │
 │ RSS  (OSINT news)                          │
 │ MQTT (sensor gateways)                     │
 │ TAXII STIX 2.1 (threat intel)              │
 │ HTTP poll (arbitrary JSON endpoints)       │
 └───────────┬────────────────────────────────┘
             │
             ▼
     ┌───────────────────────┐
     │ Celery beat scheduler │──► workers.feeds.run_source(source_id)
     │  tick per feed source │
     └─────────┬─────────────┘
               │
               ▼
     ┌───────────────────────┐
     │ per-kind adapter      │─► emits FeedEvent{geom, props, t}
     │ (pyais/pyModeS/etc.)  │
     └─────────┬─────────────┘
               │
               ▼
     ┌───────────────────────┐
     │ deduplicate + geofence│  (AOI intersection; fire geofence_triggers)
     │ + track association   │  (stitch to tracks table using callsign/MMSI)
     └─────────┬─────────────┘
               │
               ▼
     PostGIS (feed_events, track_points)
               │
               ▼
     redis.publish("events:feeds", {…})
               │
               ▼
     FastAPI WebSocket fans out by topic ─► browser Leaflet re-render
```

### 8.2 Files

**Create**
- [backend/realtime/ws.py](backend/realtime/ws.py)
- [backend/realtime/redis_bridge.py](backend/realtime/redis_bridge.py)
- [backend/feeds/__init__.py](backend/feeds/__init__.py)
- [backend/feeds/adapters/ais.py](backend/feeds/adapters/ais.py)
- [backend/feeds/adapters/adsb.py](backend/feeds/adapters/adsb.py)
- [backend/feeds/adapters/rss.py](backend/feeds/adapters/rss.py)
- [backend/feeds/adapters/mqtt.py](backend/feeds/adapters/mqtt.py)
- [backend/feeds/adapters/stix.py](backend/feeds/adapters/stix.py)
- [backend/feeds/runner.py](backend/feeds/runner.py) — celery beat config
- [backend/feeds/geofence.py](backend/feeds/geofence.py) — AOI intersection + trigger dispatch
- [backend/routes/feeds.py](backend/routes/feeds.py) — CRUD, admin-only
- [frontend/src/hooks/useEventStream.ts](frontend/src/hooks/useEventStream.ts)
- [frontend/src/components/admin/FeedManager.tsx](frontend/src/components/admin/FeedManager.tsx)

**Modify**
- [backend/worker.py:311](backend/worker.py#L311) — after `store_detections`, publish `events:detections`
- [frontend/src/components/GaiaMap.tsx:89-100](frontend/src/components/GaiaMap.tsx#L89-L100) — replace polling with `useEventStream("detections")` and `useEventStream("feeds")`
- [frontend/src/components/TargetWorkbench.tsx](frontend/src/components/TargetWorkbench.tsx) — subscribe to `ingest` topic for live ingestion badge

### 8.3 Pseudocode — WebSocket endpoint with JWT + topic subscribe

```python
# backend/realtime/ws.py
TOPIC_RE = re.compile(r"^(detections|feeds|ingest|training:\d+|fmv_detections:\d+|changes|alerts)$")

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, topic: str, token: str | None = Query(None)):
    # JWT via subprotocol OR ?token= (proxy-friendly)
    raw = token or _subprotocol_token(ws)
    user = await _verify_jwt(raw)
    if user is None:
        await ws.close(code=4401); return
    if not TOPIC_RE.match(topic):
        await ws.close(code=4400); return

    await ws.accept(subprotocol="bearer" if token is None else None)
    sub = redis.pubsub()
    await sub.subscribe(f"events:{topic}")
    try:
        # heartbeat so proxies don't kill the socket
        async with anyio.create_task_group() as tg:
            tg.start_soon(_relay, sub, ws)
            tg.start_soon(_heartbeat, ws, interval=20)
    except WebSocketDisconnect:
        pass
    finally:
        await sub.unsubscribe(); await sub.close()

async def _relay(sub, ws):
    async for msg in sub.listen():
        if msg["type"] != "message": continue
        await ws.send_text(msg["data"].decode())
```

### 8.4 Pseudocode — AIS adapter

```python
# backend/feeds/adapters/ais.py
async def run(source: FeedSource):
    host, port = source.config["host"], source.config["port"]
    async with asyncio.open_connection(host, port) as (reader, _):
        async for line in reader:
            if not line.startswith(b"!AIVDM"): continue
            try:
                msg = pyais.decode(line.strip())
            except pyais.exceptions.InvalidNMEAChecksum: continue
            if not (msg.lat and msg.lon): continue
            await _emit(
                source.id,
                geom=f"POINT({msg.lon} {msg.lat})",
                props={"mmsi": msg.mmsi, "callsign": getattr(msg, "shipname", None),
                       "speed": msg.speed, "course": msg.course, "type": msg.type},
                event_time=datetime.now(tz=UTC),
            )
```

### 8.5 Pseudocode — `useEventStream.ts`

```ts
// frontend/src/hooks/useEventStream.ts
export function useEventStream<T = any>(topic: string, onMessage?: (m: T) => void) {
  const [state, setState] = useState({ connected: false, lastMsg: null as T | null });
  const reconnectRef = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let cancelled = false;

    const connect = () => {
      const url = `${WS_URL}/ws?topic=${encodeURIComponent(topic)}&token=${getJwt()}`;
      ws = new WebSocket(url);
      ws.onopen = () => { setState(s => ({...s, connected: true})); reconnectRef.current = 0; };
      ws.onmessage = (e) => {
        const m = JSON.parse(e.data) as T;
        setState(s => ({ ...s, lastMsg: m }));
        onMessage?.(m);
      };
      ws.onclose = () => {
        setState(s => ({ ...s, connected: false }));
        if (cancelled) return;
        const delay = Math.min(30_000, 1000 * 2 ** reconnectRef.current++);
        setTimeout(connect, delay);
      };
    };
    connect();
    return () => { cancelled = true; ws?.close(); };
  }, [topic]);

  return state;
}
```

### 8.6 Verification

- [ ] `wscat -c "ws://localhost:8080/ws?topic=detections&token=$JWT"` connects, rejects bad token
- [ ] Upload imagery; second browser tab sees detection layer refresh in < 1 s
- [ ] Add an AIS feed pointing at a local test stream (`nc -l 4002 < ais_sample.nmea`); vessels appear in GaiaMap live
- [ ] Replace polling in GaiaMap with `useEventStream` — verify no `setInterval` remains in component
- [ ] Geofence crossing fires alert toast via `events:alerts`

---

<a id="phase-5"></a>
## 9. Phase 5 — Model Training & MLOps *(Size: L, ~5 days)*

**Goal:** An admin surface where a superuser uploads a YOLO-format dataset, kicks a training job, streams the live log, watches MLflow metrics, promotes a model, and inference hot-reloads — all without leaving the app.

### 9.1 Architecture

```
 Admin browser
     │
     ▼
 /api/training/datasets  ──► store zip → /data/datasets/<name>/ (validated YOLO layout)
 /api/training/jobs      ──► enqueue task on celery queue=training
     │                         │
     │                         ▼
     │          ┌──────────── worker-training ─────────────┐
     │          │  import ultralytics; import mlflow        │
     │          │  mlflow.set_tracking_uri("http://mlflow") │
     │          │  with mlflow.start_run(run_name=…):       │
     │          │    YOLO(base).train(                      │
     │          │       data=data.yaml, epochs=E, imgsz=S,  │
     │          │       project="/mlruns",                  │
     │          │       callbacks=[                         │
     │          │         LogToFileCallback(log_path),      │
     │          │         RedisPublishCallback(topic),      │
     │          │         MlflowCallback(),                 │
     │          │       ])                                  │
     │          │  register .pt in `models` table           │
     │          └──────────────────────────────────────────┘
     │
     ▼
 /api/models/{id}/promote
     │
     ▼
 models.status = 'production' (via transaction + history)
     │
     ▼
 POST http://inference:8001/reload
     │
     ▼
 inference reloads YOLO with new weights_path
```

### 9.2 Files

**Create**
- [backend/training/__init__.py](backend/training/__init__.py)
- [backend/training/routes.py](backend/training/routes.py) — admin-only
- [backend/training/dataset.py](backend/training/dataset.py) — YOLO-format validator
- [backend/training/callbacks.py](backend/training/callbacks.py) — Redis publish, file log
- [backend/worker_training.py](backend/worker_training.py) — separate Celery app
- [frontend/src/components/admin/AdminShell.tsx](frontend/src/components/admin/AdminShell.tsx)
- [frontend/src/components/admin/ModelTraining.tsx](frontend/src/components/admin/ModelTraining.tsx)
- [frontend/src/components/admin/ModelRegistry.tsx](frontend/src/components/admin/ModelRegistry.tsx)

**Modify**
- [inference/main.py](inference/main.py) — add `POST /reload` re-running `load_model()` against current `MODEL_PATH`; env `REQUIRE_MODEL=true` hard-fails if no model
- [docker-compose.yml](docker-compose.yml) — add `mlflow`, `worker-training` services, `mlruns_volume`, `datasets_volume`

### 9.3 Pseudocode — training task

```python
# backend/worker_training.py
@celery_app.task(name="workers.training.train", queue="training", bind=True, soft_time_limit=72*3600)
def train(self, job_id: int):
    job = _load_job(job_id); _mark_running(job_id)
    data_yaml = _write_yolo_yaml(job.dataset_path, job.hyperparams)
    mlflow.set_tracking_uri(settings.MLFLOW_URL)
    with mlflow.start_run(run_name=f"{job.model_name}_{job_id}") as run:
        mlflow.log_params(job.hyperparams)
        try:
            model = YOLO(job.base_weights)
            model.add_callback("on_train_epoch_end",
                _mk_epoch_publisher(redis, topic=f"training:{job_id}"))
            results = model.train(
                data=data_yaml,
                epochs=job.hyperparams["epochs"],
                imgsz=job.hyperparams.get("imgsz", 640),
                batch=job.hyperparams.get("batch", 16),
                project="/mlruns",
                name=f"job_{job_id}",
                device="0" if torch.cuda.is_available() else "cpu",
                verbose=True,
            )
            best_pt = Path(results.save_dir) / "weights" / "best.pt"
            sha = sha256_file(best_pt)
            with pg_sync(commit=True) as cur:
                cur.execute("""
                    INSERT INTO models
                        (name, version, sensor_type, framework,
                         weights_path, weights_sha256, classes, metrics,
                         mlflow_run_id, status)
                    VALUES (%s, %s, %s, 'yolov8', %s, %s, %s::jsonb, %s::jsonb, %s, 'staged')
                """, (job.model_name, _next_version(job.model_name),
                      job.hyperparams.get("sensor_type", "optical"),
                      str(best_pt), sha, json.dumps(model.names),
                      json.dumps(results.results_dict), run.info.run_id))
            _mark_succeeded(job_id)
            redis.publish(f"events:training:{job_id}",
                json.dumps({"kind":"done","mlflow_run": run.info.run_id}))
        except Exception as e:
            _mark_failed(job_id, str(e)); raise
```

### 9.4 Pseudocode — `/reload` in inference

```python
# inference/main.py
@app.post("/reload")
def reload(path: str | None = None, token: str = Depends(verify_internal_secret)):
    global detection_model, MODEL_PATH
    MODEL_PATH = path or os.getenv("MODEL_PATH")
    load_model()
    if settings.REQUIRE_MODEL and detection_model is None:
        raise HTTPException(503, "model load failed")
    return {"status": "reloaded", "model_path": MODEL_PATH}
```

### 9.5 Verification

- [ ] Upload a 3-class YOLO subset (zip), validator accepts
- [ ] Kick 1-epoch job; `useEventStream("training:<id>")` streams per-epoch logs
- [ ] MLflow UI at `/mlflow/` (nginx-proxied) shows the run
- [ ] Promote model → `POST /api/models/{id}/promote` → inference `/health` reports new `model_path`
- [ ] Run a new ingest; detections now come from new weights (spot-check class names)

---

<a id="phase-6"></a>
## 10. Phase 6 — GEOINT Analytics & Exploitation Toolkit *(Size: XL, ~8 days)*

**Goal:** Lift the platform from "ingest + display" to "analyst-grade exploitation". This is the biggest NEW phase on top of the original plan. Everything runs on stored data; every operation is reproducible; every output is geotagged and audit-logged.

### 10.1 Toolset

| Tool | Input | Output | Library |
|---|---|---|---|
| **Change Detection** | two passes over same AOI | change_events polygons | `rasterio` + `scikit-image` (SSIM + Otsu) + GDAL `gdal_calc` |
| **Coregistration** | two co-located rasters | warped second raster | `arosics==1.9` |
| **Viewshed** | observer point + DEM | visible polygon | `whitebox==2.3` (`Viewshed` tool) |
| **Line-of-Sight** | (A,B) + DEM | bool + profile plot | `whitebox` (`LineOfSight`) |
| **Drive-time isochrones** | origin + minutes | polygon | OSRM + `scipy.spatial.ConvexHull` over reachable nodes |
| **Great-circle paths / buffers** | pair of points | LineString + buffer | shapely + pyproj |
| **Shadow overlay** | pass time + DEM | shadow polygon | `pysolar` + DEM hillshade |
| **Pattern-of-Life (POL)** | tracks over window | heatmap + histogram | kernel density (`scipy`) + time-binned histogram |
| **Track fusion** | multi-sensor points | track_id per point | `norfair` Kalman + callsign joiner |
| **Mosaic build** | N overlapping COGs | single COG | `rio-cogeo` + `gdalbuildvrt` |
| **Pan-sharpening** | pan band + MS bands | higher-res MS | `rio pansharpen` (from rio-tiler ecosystem) |
| **Coverage gap** | pass footprints ∪ vs AOI | deficit polygon | `ST_Difference` in PostGIS |
| **Cloud masking** | MSI pass | cloud polygon | simple QA band extraction (Sentinel-2 SCL) |
| **Hotspot clustering** | detections | cluster labels | `sklearn.cluster.DBSCAN` on (lon,lat) |

### 10.2 Files

**Create**
- [backend/analytics/__init__.py](backend/analytics/__init__.py)
- [backend/analytics/change.py](backend/analytics/change.py)
- [backend/analytics/viewshed.py](backend/analytics/viewshed.py)
- [backend/analytics/los.py](backend/analytics/los.py)
- [backend/analytics/isochrones.py](backend/analytics/isochrones.py)
- [backend/analytics/shadow.py](backend/analytics/shadow.py)
- [backend/analytics/pol.py](backend/analytics/pol.py)
- [backend/analytics/tracks.py](backend/analytics/tracks.py)
- [backend/analytics/mosaic.py](backend/analytics/mosaic.py)
- [backend/routes/analytics.py](backend/routes/analytics.py)
- [frontend/src/components/analytics/ChangePanel.tsx](frontend/src/components/analytics/ChangePanel.tsx)
- [frontend/src/components/analytics/ViewshedPanel.tsx](frontend/src/components/analytics/ViewshedPanel.tsx)
- [frontend/src/components/analytics/LosPanel.tsx](frontend/src/components/analytics/LosPanel.tsx)
- [frontend/src/components/analytics/IsochronePanel.tsx](frontend/src/components/analytics/IsochronePanel.tsx)
- [frontend/src/components/analytics/PolHeatmap.tsx](frontend/src/components/analytics/PolHeatmap.tsx)

### 10.3 Pseudocode — Change Detection (bitemporal, cloud-masked)

```python
# backend/analytics/change.py
@celery_app.task(name="workers.analytics.change_detect", queue="analytics")
def change_detect(pass_before: int, pass_after: int, aoi_id: int, metric: str = "ndvi_delta"):
    a = _load_pass(pass_before); b = _load_pass(pass_after); aoi = _load_aoi(aoi_id)

    # 1) clip both to AOI
    with rasterio.open(a.cog) as src_a, rasterio.open(b.cog) as src_b:
        arr_a, tr_a = rio_mask(src_a, [aoi.geom], crop=True, filled=True)
        arr_b, tr_b = rio_mask(src_b, [aoi.geom], crop=True, filled=True)

    # 2) coregister (b onto a) if they diverge
    if _needs_coreg(arr_a, arr_b):
        arr_b = arosics_align(arr_a, arr_b)

    # 3) cloud mask (Sentinel-2 SCL if available, else luminance > thr)
    cm_a = _cloud_mask(src_a); cm_b = _cloud_mask(src_b)
    valid = ~(cm_a | cm_b)

    # 4) compute metric
    if metric == "ndvi_delta":
        ndvi_a = (arr_a[7] - arr_a[3]) / (arr_a[7] + arr_a[3] + 1e-9)
        ndvi_b = (arr_b[7] - arr_b[3]) / (arr_b[7] + arr_b[3] + 1e-9)
        diff = np.where(valid, ndvi_b - ndvi_a, np.nan)
    elif metric == "sar_coherence":
        diff = _sar_coherence(arr_a[0], arr_b[0])
    elif metric == "intensity":
        diff = np.where(valid, arr_b.mean(0) - arr_a.mean(0), np.nan)

    # 5) threshold with Otsu on |diff|
    abs_diff = np.abs(diff); abs_diff = abs_diff[~np.isnan(abs_diff)]
    thr = skimage.filters.threshold_otsu(abs_diff)
    mask = np.abs(diff) > thr

    # 6) vectorise to polygons
    polys = list(rasterio.features.shapes(mask.astype("uint8"), transform=tr_a))
    mp    = shapely.ops.unary_union([shapely.geometry.shape(g) for g, v in polys if v == 1])
    if mp.is_empty: return {"n": 0}

    # 7) insert
    with pg_sync(commit=True) as cur:
        cur.execute("""
            INSERT INTO change_events (aoi_id, pass_before, pass_after, metric,
                delta_stats, hotspot, confidence)
            VALUES (%s,%s,%s,%s,%s::jsonb, ST_Multi(ST_GeomFromText(%s,4326)), %s)
            RETURNING id
        """, (aoi_id, pass_before, pass_after, metric,
              json.dumps({"mean": float(np.nanmean(diff)),
                          "p95": float(np.nanpercentile(abs_diff, 95)),
                          "threshold": float(thr)}),
              mp.wkt, 0.8))
        change_id = cur.fetchone()["id"]
    redis.publish("events:changes",
        json.dumps({"aoi_id": aoi_id, "change_id": change_id, "metric": metric}))
    return {"change_id": change_id, "area_m2": _area(mp)}
```

### 10.4 Pseudocode — Viewshed

```python
# backend/analytics/viewshed.py
def viewshed(observer_lon: float, observer_lat: float,
             observer_h_m: float, max_range_m: float,
             dem_path: str) -> shapely.geometry.MultiPolygon:
    wbt = whitebox.WhiteboxTools(); wbt.set_verbose_mode(False)
    obs_shp = _write_single_point_shp(observer_lon, observer_lat, observer_h_m)
    out_tif = tempfile.mktemp(".tif")
    wbt.viewshed(
        input=dem_path, stations=obs_shp, output=out_tif,
        height=observer_h_m,
        max_dist=max_range_m / _px_size(dem_path),
    )
    # polygonise visible (==1) region
    with rasterio.open(out_tif) as src:
        arr = src.read(1)
        polys = [shapely.geometry.shape(g)
                 for g, v in rasterio.features.shapes(arr, transform=src.transform)
                 if v == 1]
    return shapely.geometry.MultiPolygon(polys)
```

### 10.5 Pseudocode — Line-of-Sight profile

```python
# backend/analytics/los.py
def los_profile(a: tuple[float,float,float], b: tuple[float,float,float],
                dem_path: str, samples: int = 500):
    """(lon, lat, h_agl) → (visible:bool, samples:list[{dist, terrain_m, ray_m, blocked:bool}])"""
    line = shapely.geometry.LineString([(a[0], a[1]), (b[0], b[1])])
    lengths = np.linspace(0, 1, samples)
    pts = [line.interpolate(l, normalized=True) for l in lengths]
    with rasterio.open(dem_path) as src:
        terrain = np.array([next(src.sample([(p.x, p.y)]))[0] for p in pts])
    d_total_m = _geodesic_m(a[:2], b[:2])
    ray_m = np.linspace(a[2] + terrain[0], b[2] + terrain[-1], samples)
    blocked = terrain > ray_m
    return {
        "visible": not bool(blocked[1:-1].any()),
        "samples": [
            {"dist_m": float(d_total_m * l),
             "terrain_m": float(t), "ray_m": float(r),
             "blocked": bool(bl)}
            for l, t, r, bl in zip(lengths, terrain, ray_m, blocked)
        ],
    }
```

### 10.6 Pseudocode — Drive-time isochrone using local OSRM

```python
# backend/analytics/isochrones.py
def drive_time_isochrone(lon: float, lat: float, minutes: int) -> shapely.geometry.Polygon:
    r = httpx.get(f"{OSRM_URL}/table/v1/driving/{lon},{lat};{','.join(_grid(lon,lat,minutes))}",
                  params={"annotations":"duration","sources":"0"}).json()
    durations = r["durations"][0][1:]
    reachable = [
        (pt_lon, pt_lat)
        for (pt_lon, pt_lat), d in zip(_grid_points(lon,lat,minutes), durations)
        if d is not None and d <= minutes * 60
    ]
    hull = MultiPoint(reachable).convex_hull       # good enough; alpha-shape is nicer
    return hull.buffer(0.001)                      # smooth
```

### 10.7 Pseudocode — Pattern-of-Life heatmap

```python
# backend/analytics/pol.py
def pol_heatmap(track_ids: list[int], aoi: shapely.geometry.Polygon,
                t_from: datetime, t_to: datetime,
                grid_m: float = 500.0) -> np.ndarray:
    pts = _fetch_track_points(track_ids, aoi, t_from, t_to)        # list[(t, lon, lat)]
    x = np.array([p[1] for p in pts]); y = np.array([p[2] for p in pts])
    (minx, miny, maxx, maxy) = aoi.bounds
    nx = max(1, int(((maxx-minx) * 111_000) / grid_m))
    ny = max(1, int(((maxy-miny) * 111_000) / grid_m))
    H, _, _ = np.histogram2d(x, y, bins=[nx, ny], range=[[minx,maxx],[miny,maxy]])
    H = scipy.ndimage.gaussian_filter(H, sigma=1.5)
    return H, (minx, miny, maxx, maxy)
```

### 10.8 Verification

- [ ] Run change detection on two Sentinel-2 passes over the same AOI 30 days apart; hotspot polygons appear where a field was harvested
- [ ] Place observer on a ridge in Phase 10 DEM; viewshed polygon is sensible (stops at ridge lines)
- [ ] LOS between two rooftops: blocked where a hill intervenes, with correct terrain profile JSON
- [ ] 10-minute drive-time isochrone around a city coordinate on a local OSRM looks city-shaped, not a perfect circle
- [ ] POL heatmap from an AIS track stream shows anchorage clusters at port entrances

---

<a id="phase-7"></a>
## 11. Phase 7 — Collection Management & PED Workflow *(Size: L, ~5 days)*

**Goal:** Close the loop: analyst posts a Collection Requirement → Collection Manager approves → system predicts next satellite access → pass executes → PED (Processing/Exploitation/Dissemination) tasks are generated → BDA (Battle Damage Assessment) compares before/after.

### 11.1 Concepts

- **PIR** — Priority Intelligence Requirement (high-level question)
- **EEI** — Essential Element of Information (measurable sub-question)
- **NAI** — Named Area of Interest (polygon)
- **TAI** — Target Area of Interest (active)
- **Collection Plan** — pairing of NAIs × sensors × time windows
- **TCPED cycle** — Tasking, Collection, Processing, Exploitation, Dissemination

### 11.2 Architecture

```
 Analyst:   POST /api/cr            (Collection Requirement)
               │
               ▼
 Collection Mgr: POST /api/cr/{id}/approve
               │
               ▼
 Predictor: SGP4 over Satellite TLEs vs AOI geom → proposed passes
               │
               ▼
 Tasking:   CREATE collection_tasks row with planned_pass_ids
               │
               ▼
 Collection: (manual now) analyst uploads imagery with {"cr_id": …} → auto-link
               │
               ▼
 Processing: auto-chain from Phase 1 (ingest → inference)
               │
               ▼
 Exploitation: PED dashboard: "3 passes delivered for CR-17" + analyst chip tools
               │
               ▼
 Dissemination: Phase 8 — target package generation, PDF/KMZ export, RSS
               │
               ▼
 BDA:       change detection of same AOI before vs after strike window
```

### 11.3 Files

**Create**
- [backend/collection/routes.py](backend/collection/routes.py)
- [backend/collection/predictor.py](backend/collection/predictor.py) — SGP4 + AOI geometry intersection to compute next-access windows
- [backend/collection/ped.py](backend/collection/ped.py) — task queue for exploitation work items
- [frontend/src/components/ped/PedDashboard.tsx](frontend/src/components/ped/PedDashboard.tsx)
- [frontend/src/components/ped/CollectionPlanEditor.tsx](frontend/src/components/ped/CollectionPlanEditor.tsx)
- [frontend/src/components/ped/BdaPanel.tsx](frontend/src/components/ped/BdaPanel.tsx)
- [frontend/src/components/aoi/AoiEditor.tsx](frontend/src/components/aoi/AoiEditor.tsx)
- [frontend/src/components/aoi/AoiList.tsx](frontend/src/components/aoi/AoiList.tsx)

### 11.4 Pseudocode — next-access predictor

```python
# backend/collection/predictor.py
from sgp4.api import Satrec, jday
from astropy.coordinates import EarthLocation, AltAz, TEME
from astropy.time import Time
from astropy import units as u

def next_access(satellite_tle: tuple[str,str],
                aoi: shapely.geometry.Polygon,
                window_start: datetime, window_end: datetime,
                min_elevation_deg: float = 20.0,
                step_s: int = 30) -> list[tuple[datetime, datetime]]:
    """
    Walks satellite's ground trace in step_s increments over [window_start, window_end].
    Records intervals where the sub-satellite point (or FOR-projected footprint) intersects aoi
    AND the sensor elevation over the AOI centroid exceeds min_elevation_deg.
    """
    sat = Satrec.twoline2rv(*satellite_tle)
    centroid = aoi.centroid
    loc = EarthLocation(lon=centroid.x * u.deg, lat=centroid.y * u.deg, height=0)

    t = window_start; windows = []; in_window = False; start = None
    while t < window_end:
        jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond/1e6)
        e, r, v = sat.sgp4(jd, fr)
        if e != 0: t += timedelta(seconds=step_s); continue
        sub_lon, sub_lat = _teme_to_geodetic(r, Time(t))
        sub_pt = shapely.geometry.Point(sub_lon, sub_lat)
        az, el = _look_angles(r, loc, Time(t))
        over_aoi = aoi.contains(sub_pt) or aoi.distance(sub_pt) < _swath_deg(sat)
        visible  = el.deg >= min_elevation_deg
        active = over_aoi and visible
        if active and not in_window:
            start = t; in_window = True
        elif not active and in_window:
            windows.append((start, t)); in_window = False
        t += timedelta(seconds=step_s)
    if in_window: windows.append((start, window_end))
    return windows
```

### 11.5 Pseudocode — BDA

```python
# backend/collection/ped.py
def run_bda(target_id: str, window_before: tuple[datetime,datetime],
            window_after: tuple[datetime,datetime]) -> dict:
    target = _load_target(target_id)
    aoi    = _get_or_build_bda_aoi(target)

    passes_before = _passes_intersecting(aoi, *window_before)
    passes_after  = _passes_intersecting(aoi, *window_after)
    if not passes_before or not passes_after:
        return {"status": "insufficient_passes"}

    # pick best (lowest cloud) pair
    p_before = min(passes_before, key=lambda p: p.cloud_cover or 100)
    p_after  = min(passes_after,  key=lambda p: p.cloud_cover or 100)

    change = change_detect.delay(p_before.id, p_after.id, aoi.id, metric="intensity").get(timeout=600)
    chip_before = _extract_chip(p_before, aoi)
    chip_after  = _extract_chip(p_after,  aoi)
    return {
        "status": "ok",
        "pass_before": p_before.id, "pass_after": p_after.id,
        "change_event_id": change["change_id"],
        "chip_before_url": chip_before, "chip_after_url": chip_after,
        "area_changed_m2": change["area_m2"],
    }
```

### 11.6 Verification

- [ ] Create AOI + CR; approving produces a list of next access windows for all known satellites within the next 48 h
- [ ] Upload imagery with a `cr_id`; PED dashboard shows "2/3 passes delivered"
- [ ] Request BDA on a target; receive two chips + change mask + area_m2

---

<a id="phase-8"></a>
## 12. Phase 8 — Target Packages, Reports, Dissemination *(Size: M, ~4 days)*

**Goal:** Analyst clicks "generate target package" → PDF / KMZ / GPKG pops out with identity, location history, detections, imagery chips, related entities, and activity log — fully offline via ReportLab / WeasyPrint / python-pptx.

### 12.1 Output Artifacts

| Format | Library | Use |
|---|---|---|
| **PDF target dossier** | WeasyPrint (HTML → PDF, offline) | Printable one-pager with chips + maps |
| **PPTX brief** | python-pptx | Stakeholder briefing deck |
| **KMZ** | stdlib `zipfile` + fastkml | Hand-off to Google Earth / ATAK |
| **GPKG** | `geopandas.to_file(driver="GPKG")` | Hand-off to QGIS / ArcGIS |
| **STIX 2.1 bundle** | `stix2` | Machine-to-machine threat feeds |
| **Shareable link** | presigned, role-gated URL | Internal read-only view |

### 12.2 Files

**Create**
- [backend/dissemination/__init__.py](backend/dissemination/__init__.py)
- [backend/dissemination/pdf.py](backend/dissemination/pdf.py)
- [backend/dissemination/pptx.py](backend/dissemination/pptx.py)
- [backend/dissemination/kmz.py](backend/dissemination/kmz.py)
- [backend/dissemination/gpkg.py](backend/dissemination/gpkg.py)
- [backend/dissemination/stix.py](backend/dissemination/stix.py)
- [backend/templates/target_pdf.html](backend/templates/target_pdf.html)
- [frontend/src/components/target/TargetPackage.tsx](frontend/src/components/target/TargetPackage.tsx)
- [frontend/src/components/target/ExportMenu.tsx](frontend/src/components/target/ExportMenu.tsx)

### 12.3 Pseudocode — Target PDF

```python
# backend/dissemination/pdf.py
def render_target_pdf(target_id: str, user_id: UUID) -> Path:
    t = _load_target_with_dossier(target_id)
    html = _env.get_template("target_pdf.html").render(
        target=t,
        chips=_chips_for_target(target_id),          # list of filesystem paths to PNGs
        history=_location_history(target_id),
        related=_related_entities(target_id),        # Neo4j 1-hop neighbours
        map_png=_render_overview_map_png(t, dims=(800, 500)),
        classification=t.classification,
        generated_at=datetime.now(UTC),
        generated_by=_user_name(user_id),
    )
    out = Path(settings.EXPORTS_DIR)/str(user_id)/f"target_{target_id}_{_ymd()}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(settings.TEMPLATES_DIR)).write_pdf(out)
    _audit("export.pdf", "target", target_id, user_id)
    return out
```

### 12.4 Pseudocode — KMZ export

```python
# backend/dissemination/kmz.py
def render_target_kmz(target_id: str) -> Path:
    t = _load_target_with_dossier(target_id)
    k = fastkml.KML()
    ns = "{http://www.opengis.net/kml/2.2}"
    doc = fastkml.Document(ns, "1", t.name, t.description); k.append(doc)
    pm = fastkml.Placemark(ns, "t1", t.name, t.description)
    pm.geometry = shapely.geometry.Point(t.longitude, t.latitude)
    doc.append(pm)
    for obs in t.observations:
        o = fastkml.Placemark(ns, f"o{obs.id}", obs.label, obs.time.isoformat())
        o.geometry = shapely.geometry.Point(obs.lon, obs.lat); doc.append(o)
    kml_bytes = k.to_string(prettyprint=True).encode()
    out = Path(settings.EXPORTS_DIR)/f"target_{target_id}.kmz"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml_bytes)
        for chip in _chips_for_target(target_id):
            z.write(chip, arcname=f"chips/{chip.name}")
    return out
```

### 12.5 Verification

- [ ] Click "Export → PDF" on a target with 5 detections; PDF opens with chips and map — no network hits (verify in DevTools)
- [ ] KMZ opens in Google Earth offline and positions correctly
- [ ] GPKG opens in QGIS and shows same geometry
- [ ] STIX bundle validates via `stix2-validator`

---

<a id="phase-9"></a>
## 13. Phase 9 — Bug Fixes, Placeholder Removal, Full Offline Hardening *(Size: L, ~4 days)*

**Goal:** Close every loose end catalogued during validation so the final artifact installs cleanly on an air-gapped VM and never reaches the public internet.

### 13.1 Bugs & placeholder fixes (full list, Phase 0/1 fixes excluded — those land earlier)

| # | Fix |
|---|---|
| B1 | [backend/main.py:12-18](backend/main.py#L12-L18) — replace `allow_origins=["*"]` with `settings.ALLOWED_ORIGINS` (list from env) |
| B2 | [backend/ai.py:14](backend/ai.py#L14) — remove `"dummy"` default for `OPENAI_API_KEY`; fail-fast if missing |
| B3 | [backend/ai.py:32](backend/ai.py#L32) — `allow_dangerous_requests=False`; use `langchain_neo4j.GraphCypherQAChain` with `cypher_validator` + **read-only Neo4j role** |
| B4 | [backend/ai.py:9-11](backend/ai.py#L9-L11) — remove all `bolt://localhost` / `http://localhost:8000` defaults |
| B5 | [backend/database.py:9](backend/database.py#L9) — no default `"password"`; raise if unset |
| B6 | [backend/worker.py:159-160](backend/worker.py#L159-L160) — structured log + `redis.publish("events:errors", …)`; never swallowed |
| B7 | [backend/worker.py:239-240](backend/worker.py#L239-L240) — rewrite using `urllib.parse.urlparse(image_url).scheme` |
| B8 | [backend/worker.py:99](backend/worker.py#L99) — (**N2**) `None`-safe nodata check (already queued for Phase 1, confirm) |
| B9 | [inference/main.py:131-149](inference/main.py#L131-L149) — **delete** random-mock branch; return `{"status":"degraded","detections":[]}`; env `REQUIRE_MODEL=true` → hard fail |
| B10 | [frontend/src/components/TargetWorkbench.tsx:67-78](frontend/src/components/TargetWorkbench.tsx#L67-L78) — `<select>` from `GET /api/imagery`; `alert()` → `sonner.toast()` |
| B11 | [frontend/src/components/ConstellationView.tsx:57-63](frontend/src/components/ConstellationView.tsx#L57-L63) — derive arc positions from satellite.js' browser SGP4 using TLEs from Neo4j; real TCA via `satellite.js` pass predictor |
| B12 | [frontend/src/components/ConstellationView.tsx:92](frontend/src/components/ConstellationView.tsx#L92) — live TCA from predictor output (not string template) |
| B13 | All `any`-typed API responses in `*.tsx` — generate `apiClient.ts` from FastAPI OpenAPI via `openapi-typescript` |
| B14 | `alert()` / bare `console.error` — replace with `sonner` toast + `<ErrorBoundary>` at App root |
| B15 | [backend/Dockerfile](backend/Dockerfile) — (**N1**) add `gdal-bin`, confirmed via `gdal_translate --version` |
| B16 | [backend/worker.py:108-116](backend/worker.py#L108-L116) — (**N3**) chip written as GeoTIFF not PNG |
| B17 | [backend/init_postgis.sql:40-55](backend/init_postgis.sql#L40-L55) — (**N5**) new alembic revision `seed_natural_earth` that loads `/data/basemaps/ne_*.gpkg` into `ne_countries` / `ne_coastline` |
| B18 | [inference/Dockerfile:30](inference/Dockerfile#L30) — (**N7**) `COPY weights/yolov8n.pt /app/weights/yolov8n.pt` instead of runtime download |
| B19 | [docker-compose.yml:38-61](docker-compose.yml#L38-L61) — (**N8**) backend `depends_on: {titiler, redis}` |

### 13.2 Offline strategy (8 pillars)

1. **Python wheelhouse** — every Dockerfile gets a build stage:
   ```Dockerfile
   FROM python:3.11-slim AS wheels
   COPY requirements.txt .
   RUN pip download -r requirements.txt -d /wheels
   FROM python:3.11-slim
   COPY --from=wheels /wheels /wheels
   RUN pip install --no-index --find-links=/wheels -r requirements.txt
   ```
   Commit `/wheelhouse/` via **git-lfs** for the air-gapped rebuild.
2. **PyTorch wheel** — [inference/Dockerfile:24](inference/Dockerfile#L24) switches from `--index-url https://download.pytorch.org/whl/cpu` to pre-baked wheel copied from `/wheels`.
3. **YOLO weights** — bundle via `COPY weights/yolov8n.pt /app/weights/yolov8n.pt`.
4. **Basemap** — build `world.mbtiles` from Natural Earth + OSM AOI extract using `tippecanoe`:
   ```
   tippecanoe -o world.mbtiles -z 10 --drop-densest-as-needed ne_*.geojson osm_aoi.geojson
   ```
   Martin reads MBTiles natively. [GaiaMap.tsx:271](frontend/src/components/GaiaMap.tsx#L271) changes to `${MARTIN_URL}/basemap/{z}/{x}/{y}`.
5. **Cesium static assets** — run `npx vite-plugin-cesium` at build time to copy `Workers/`, `Assets/`, `Widgets/` into `frontend/public/cesium/`; set `window.CESIUM_BASE_URL = '/cesium/'`.
6. **Frontend fonts** — `@fontsource/inter` + `@fontsource/jetbrains-mono` vendored; **no** `<link href="https://fonts.googleapis.com…">`.
7. **LLM contract** — no bundled LLM (user-approved). If `OPENAI_API_BASE` unreachable, Ava chat renders an **"LLM endpoint offline"** banner and disables the input — never crashes.
8. **Single bundle** — `make offline-bundle` writes `gotham-offline-<git-sha>.tar.gz` containing:
   - `docker save` output of every image (backend, worker, worker-training, inference, titiler, martin, nginx, postgis, neo4j, redis, mlflow)
   - Wheelhouse
   - YOLO weights
   - `world.mbtiles` + `dem/` + `ne_*.gpkg`
   - Cesium vendor assets
   - `docker-compose.yml`, `.env.example`, `README-air-gap.md`

### 13.3 Verification

- [ ] Air-gapped VM: `sudo nmcli device disconnect eno1 && docker compose up -d` → all 13 services healthy
- [ ] Open UI, run full ingest→detect→display cycle; browser DevTools Network tab shows **zero non-localhost hosts**
- [ ] Cesium 3D viewer loads globe from local Natural Earth without a single CDN request
- [ ] `docker save` → `docker load` → restart → still works

---

<a id="cross-cutting"></a>
## 14. Cross-Cutting — Tests, CI, Observability, Security

### 14.1 Testing pyramid

```
           ┌─────────── Playwright E2E ───────────┐
           │  login → upload TIF → see detection  │
           │  admin → train 1 epoch → promote     │
           │  FMV ingest → overlay sync           │
           │  BDA before/after chip diff          │
           └──────────┬───────────────────────────┘
                      │
              ┌───────┴────────┐
              │  vitest        │   frontend components (titilerUrl,
              │  @testing-lib  │    useEventStream mock, apiClient)
              └───────┬────────┘
                      │
              ┌───────┴────────┐
              │ pytest +       │   backend: auth, ingest handlers,
              │ httpx async    │   ws, analytics, dissemination
              └───────┬────────┘
                      │
              ┌───────┴────────┐
              │ testcontainers-│   integration: real PostGIS, Redis,
              │ postgres/redis │   Celery in eager mode
              └────────────────┘
```

Files:
- [backend/tests/conftest.py](backend/tests/conftest.py)
- [backend/tests/test_auth.py](backend/tests/test_auth.py)
- [backend/tests/test_ingest_vector.py](backend/tests/test_ingest_vector.py)
- [backend/tests/test_ingest_fmv.py](backend/tests/test_ingest_fmv.py)
- [backend/tests/test_ws_detections.py](backend/tests/test_ws_detections.py)
- [backend/tests/test_change_detect.py](backend/tests/test_change_detect.py)
- [backend/tests/test_viewshed.py](backend/tests/test_viewshed.py)
- [backend/tests/test_training_e2e.py](backend/tests/test_training_e2e.py)
- [backend/tests/test_pdf_export.py](backend/tests/test_pdf_export.py)
- [frontend/src/__tests__/titilerUrl.test.ts](frontend/src/__tests__/titilerUrl.test.ts)
- [frontend/src/__tests__/FmvOverlayCanvas.test.tsx](frontend/src/__tests__/FmvOverlayCanvas.test.tsx)
- [e2e/playwright.config.ts](e2e/playwright.config.ts)
- [e2e/tests/full_flow.spec.ts](e2e/tests/full_flow.spec.ts)

### 14.2 CI

- [.github/workflows/ci.yml](.github/workflows/ci.yml) runs on **self-hosted** runners (no hosted GH action for the air-gap image):
  1. `ruff check backend/ inference/`
  2. `mypy --strict backend/` (strict only on `backend/auth`, `backend/ingest`, `backend/analytics`; legacy modules warn)
  3. `pytest -q` with testcontainers
  4. `cd frontend && npm ci && npm run lint && npm run test && npx tsc --noEmit`
  5. Playwright suite against compose
  6. `docker compose build` with `--no-cache` on tagged releases

### 14.3 Observability

- Structured logs via `structlog` → JSON on stdout; nginx and Celery aligned.
- OpenTelemetry traces exported to local **Tempo** (add-on, optional docker-compose profile `observability`).
- `prometheus-fastapi-instrumentator` → Prometheus (same optional profile).
- Grafana dashboards shipped in `observability/grafana/`.

### 14.4 Security

- Nginx terminates TLS with a **self-signed internal CA** (script in `nginx/gen-certs.sh`) for air-gapped ops.
- Neo4j runs with a dedicated `ava` user that has only **read** on the ontology; the `GraphCypherQAChain` uses that user, limiting the blast radius of prompt injection even with `allow_dangerous_requests=False`.
- `fastapi-users` password hashing uses `argon2-cffi`.
- All mutating routes require `current_active_user`; admin routes require `require_role("admin")`; collection routes require `require_role("collection_mgr","admin")`.
- Every upload is SHA256-hashed and rejected if a row already exists (dedupe + integrity).
- `audit_log` captures who did what to which resource, with IP and UA.
- Content Security Policy in nginx: `default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data: blob:;`

### 14.5 Performance

- PostGIS `GIST` indices on every geometry column; `BRIN` on `event_time`; `ANALYZE` after bulk inserts.
- Celery queues segregated by CPU profile: `imagery` (GDAL, high memory), `video` (ffmpeg, IO-heavy), `vector` (fast), `training` (GPU), `analytics` (compute), `feeds` (network).
- TiTiler sits behind nginx `proxy_cache` (24 h, LRU) — [nginx/tile-proxy.conf](nginx/tile-proxy.conf).
- Martin tile cache uses its built-in `CACHE_SIZE_MB=512`.
- hls.js uses `liveSyncDurationCount=3` for near-live, `maxBufferLength=60` for playback.

---

<a id="offline-bundle"></a>
## 15. Offline Bundle Specification

`Makefile` target `offline-bundle`:

```make
offline-bundle: wheels basemap cesium-assets models
	./scripts/bundle.sh gotham-offline-$(shell git rev-parse --short HEAD).tar.gz

wheels:
	docker compose build --target wheels
	docker run --rm -v $(PWD)/wheelhouse:/out gotham-backend:wheels \
		cp -r /wheels /out

basemap: data/basemaps/world.mbtiles data/basemaps/ne_*.gpkg

data/basemaps/world.mbtiles: data/basemaps/ne_countries.geojson \
                             data/basemaps/ne_coastline.geojson \
                             data/basemaps/osm_aoi.geojson
	tippecanoe -o $@ -z 10 --drop-densest-as-needed $^

cesium-assets:
	cd frontend && npm run vendor:cesium

models: inference/weights/yolov8n.pt

inference/weights/yolov8n.pt:
	./scripts/download-once.sh yolov8n.pt $@   # requires online, one-time
```

Bundle contents (`gotham-offline-<sha>.tar.gz`):

```
gotham-offline-<sha>/
├── images/                           docker save output
│   ├── gotham-backend.tar
│   ├── gotham-worker.tar
│   ├── gotham-worker-training.tar
│   ├── gotham-inference.tar
│   ├── titiler.tar
│   ├── martin.tar
│   ├── postgis.tar
│   ├── neo4j.tar
│   ├── redis.tar
│   ├── mlflow.tar
│   └── nginx.tar
├── wheelhouse/                       all Python deps
├── frontend-dist/                    pre-built static site
├── data/
│   ├── basemaps/world.mbtiles
│   ├── basemaps/ne_*.gpkg
│   ├── dem/aoi.tif
│   └── weights/yolov8n.pt
├── compose/docker-compose.yml
├── compose/.env.example
├── README-airgap.md
└── scripts/load.sh                   `docker load` each image, `up -d`
```

---

<a id="acceptance"></a>
## 16. End-to-End Acceptance (air-gapped VM)

Run on a VM with **no outbound network**:

1. `./scripts/load.sh` — loads all images, starts compose
2. `docker compose ps` — every service **healthy** (13 services)
3. `curl -k -X POST https://localhost/auth/register -d '{"email":"a@b.c","password":"testtest12"}'` → 201
4. `curl -k -X POST https://localhost/auth/jwt/login -d 'username=a@b.c&password=testtest12' -H 'Content-Type: application/x-www-form-urlencoded'` → JWT
5. Upload via dashboard (JWT held in httpOnly cookie): GeoTIFF + JP2 + NITF (raster), SHP.zip + KML + GeoJSON (vector), MP4 with MISB KLV (FMV), 3D Tiles zip + CityGML — each appears in the appropriate tab **without** manual trigger
6. Switch a Sentinel-2 COG to NDVI preset; SAR pass to dB stretch; Landsat thermal to ironbow
7. FMV viewer: scrub playhead; sub-map footprint tracks frame; detection boxes sync within ±200 ms; reticle on sensor boresight
8. Open two browsers → upload in one → other refreshes detection layer over WebSocket < 1 s
9. Configure an AIS feed (local socket) → vessels appear live; crossing a geofence pushes a toast via `events:alerts`
10. Admin: upload tiny YOLO dataset → train 1 epoch → live log stream via `training:<id>` → promote → inference `/reload` → next ingest detections use new weights
11. Analyst: pick two passes over an AOI → run change detect → hotspot polygon appears; run viewshed from a ridge → realistic visibility polygon; run 15-min drive-time isochrone → city-shaped polygon
12. Collection Manager: create a CR + AOI → predictor lists next 5 access windows; approve → task created
13. Analyst: "generate target package" on a target → PDF + KMZ + GPKG appear; open offline in Google Earth and QGIS
14. Browser DevTools Network tab across the whole session: **every request hits localhost only**; zero external hosts

---

## 17. Execution Handoff

Plan saved to [ProjectPlan/PLAN.md](ProjectPlan/PLAN.md). Two execution options:

1. **Subagent-Driven (recommended)** — Fresh subagent per task with checkpointed review between phases.
2. **Inline Execution** — Tasks executed in a single long session with batched checkpoints.

Each phase is independently shippable. Suggested order matches the numbering above; Phases 6, 7, 8 can run in parallel after Phase 5 if multiple developers are available.

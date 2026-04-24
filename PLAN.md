# Gotham OSINT Platform — Multi-Format Ingest, Sensor Viz, Real-Time, Training & Offline Plan

## Context

The platform at [d:/osint/](d:/osint/) is a Gotham-inspired tactical intelligence stack: FastAPI backend + Celery worker, Neo4j (ontology) + PostGIS (spatial), TiTiler (raster tiles), Martin (vector tiles), YOLOv8 inference, React/TS/Vite frontend on Leaflet. It currently ingests GeoTIFF/JP2/NetCDF only via API (`POST /api/ingest`) with a hard-coded sample path, has no file-upload UI, no real-time channel (10s polling), no admin surface, no auth (CORS=`*`), and the inference service silently falls back to **random mock detections** when the model is missing ([inference/main.py:131-149](inference/main.py#L131-L149)). The CARTO basemap is fetched from a public CDN ([frontend/src/components/GaiaMap.tsx](frontend/src/components/GaiaMap.tsx)), which breaks the offline target.

This plan adds: (1) multi-format ingest (NITF, KML, Shapefile, GeoJSON, MP4 FMV, CityGML, 3D Tiles), (2) sensor-aware visualization (multispectral / pan / hyperspectral / SAR / thermal), (3) real-time WebSocket-pushed feeds, (4) a fully replaced mock pipeline with auto-triggered end-to-end workflows, (5) systematic bug + placeholder fixes, (6) an admin-only model-training surface, and (7) full offline operation. Decisions confirmed with the user: **JWT auth + admin/analyst roles**, **CesiumJS (OSS)** for 3D, **external LLM endpoint** (no bundled LLM), **7 sequential phases** each independently shippable.

---

## Library & Component Choices

| Concern | Choice | Rationale |
|---|---|---|
| NITF | GDAL `NITF` driver via existing `rasterio==1.3.x` | Built-in to GDAL; routes through existing `ensure_cog()` ([backend/worker.py:20-48](backend/worker.py#L20-L48)) by removing extension whitelist |
| Vector ingest | `geopandas==0.14.4` + `pyogrio` + `fastkml==1.0` | One library reads SHP/GeoJSON/KML/GPKG and writes PostGIS via `to_postgis` |
| FMV / KLV | `ffmpeg-python==0.2.0` + `klvdata==0.0.10` (MISB ST 0601) | KLV parses sensor pose from MPEG-TS data stream; ffmpeg transcodes to fragmented MP4 + HLS |
| 3D Tiles serve | `py3dtiles==7.0` to convert; nginx serves static `tileset.json` | Static is deterministic, cacheable, simplest |
| CityGML | `citygml-tools` CLI (Java) in worker → 3D Tiles | Mature converter; we never serve raw CityGML |
| Frontend 3D | **CesiumJS** OSS build (`cesium@1.x`, no ion token) | Native 3D Tiles + KML + glTF + CZML; full offline once assets are vendored |
| Multispectral viz | TiTiler `expression` param (band math NDVI/NDWI/NBR) + `rescale` + `colormap_name` | Zero new server code; `rio-tiler` handles math server-side |
| SAR / Thermal viz | TiTiler `rescale` (dB stretch) + custom matplotlib colormaps mounted via `TITILER_API_CMAP_DIRECTORY` | Reuses TiTiler; ship colormaps as JSON in image |
| Real-time | FastAPI native WebSocket + `redis.asyncio` pub/sub | Worker `PUBLISH`es post-store; backend fans out; replaces 10s polling |
| Feed ingestors | `pyais` (AIS), `pyModeS` (ADS-B), `feedparser` (RSS), `paho-mqtt`, `httpx` (poll) | Per-kind handlers in a Celery beat scheduler |
| Training | Ultralytics `yolo train` on `training` Celery queue + **MLflow 2.14** registry (sqlite + local artifact root) | Free model registry UI; isolates long jobs |
| Auth | `fastapi-users[sqlalchemy]==13.0` + JWT, Postgres backend, role claim | Battle-tested; same Postgres as PostGIS |
| Migrations | **Alembic** new; retire `init_postgis.sql` after baseline | Versioned change without volume drops |
| Offline basemap | Pre-built MBTiles served by Martin from `/data/basemaps/world.mbtiles` | Replaces CARTO CDN; Martin natively serves MBTiles |

---

## New PostGIS Schema (Alembic baseline + extensions)

`backend/migrations/versions/0001_baseline.py` ports current `init_postgis.sql`. `0002_extensions.py` adds:

```sql
CREATE TYPE sensor_type_enum AS ENUM
  ('optical','panchromatic','multispectral','hyperspectral','sar','thermal','fmv');
CREATE TYPE asset_format_enum AS ENUM
  ('geotiff','jp2','nitf','netcdf','kml','shp','geojson','mp4','citygml','3dtiles');
CREATE TYPE job_status_enum AS ENUM ('pending','running','succeeded','failed','cancelled');
CREATE TYPE feed_kind_enum  AS ENUM ('ais','adsb','rss','mqtt','kafka','http_poll','websocket');

CREATE TABLE users (             -- managed by fastapi-users
  id UUID PRIMARY KEY, email CITEXT UNIQUE NOT NULL,
  hashed_password TEXT NOT NULL, is_active BOOL DEFAULT true,
  is_superuser BOOL DEFAULT false, role TEXT DEFAULT 'analyst');

CREATE TABLE vector_layers (
  id SERIAL PRIMARY KEY, name TEXT NOT NULL, source_format asset_format_enum,
  source_path TEXT, table_name TEXT UNIQUE, geom_type TEXT, srid INT DEFAULT 4326,
  feature_count INT, attributes JSONB, owner_id UUID REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT NOW());

CREATE TABLE fmv_clips (
  id SERIAL PRIMARY KEY, file_path TEXT UNIQUE, hls_path TEXT,
  duration_s NUMERIC, start_time TIMESTAMPTZ, end_time TIMESTAMPTZ,
  frame_track GEOMETRY(LINESTRINGZM, 4326),  -- z=alt, m=epoch_ms
  klv_track JSONB,                           -- per-frame sensor pose
  pass_id INT REFERENCES satellite_passes(id) NULL,
  created_at TIMESTAMPTZ DEFAULT NOW());

CREATE TABLE tiles_3d (
  id SERIAL PRIMARY KEY, name TEXT, root_url TEXT, source_format asset_format_enum,
  bbox GEOMETRY(POLYGON,4326), min_z REAL, max_z REAL,
  created_at TIMESTAMPTZ DEFAULT NOW());

CREATE TABLE feed_sources (
  id SERIAL PRIMARY KEY, name TEXT, kind feed_kind_enum, config JSONB,
  enabled BOOL DEFAULT true, last_event_at TIMESTAMPTZ,
  owner_id UUID REFERENCES users(id));

CREATE TABLE feed_events (
  id BIGSERIAL PRIMARY KEY, feed_id INT REFERENCES feed_sources(id),
  geom GEOMETRY(POINT,4326), props JSONB, event_time TIMESTAMPTZ DEFAULT NOW());
CREATE INDEX feed_events_geom_idx ON feed_events USING GIST(geom);
CREATE INDEX feed_events_time_idx ON feed_events(event_time DESC);

CREATE TABLE models (
  id SERIAL PRIMARY KEY, name TEXT, version TEXT, sensor_type sensor_type_enum,
  framework TEXT, weights_path TEXT, classes JSONB, metrics JSONB,
  mlflow_run_id TEXT, status TEXT DEFAULT 'staged',  -- staged|production|archived
  created_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(name, version));

CREATE TABLE training_jobs (
  id SERIAL PRIMARY KEY, model_name TEXT, base_weights TEXT,
  dataset_path TEXT, hyperparams JSONB, status job_status_enum DEFAULT 'pending',
  celery_task_id TEXT, mlflow_run_id TEXT, metrics JSONB, log_path TEXT,
  owner_id UUID REFERENCES users(id),
  started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ);

ALTER TABLE satellite_passes
  ADD COLUMN owner_id UUID REFERENCES users(id),
  ADD COLUMN format asset_format_enum,
  ALTER COLUMN sensor_type TYPE sensor_type_enum USING sensor_type::sensor_type_enum;
```

---

## Phase 0 — Auth, Schema, Migrations  *(Size: M, ~3 days)*

**Create**
- [backend/alembic.ini](backend/alembic.ini), [backend/migrations/env.py](backend/migrations/env.py), versions `0001_baseline.py`, `0002_extensions.py`
- [backend/auth/users.py](backend/auth/users.py) — fastapi-users `User`, `UserManager`
- [backend/auth/router.py](backend/auth/router.py) — mounts `/auth/jwt/login`, `/auth/register`, `/auth/users/me`
- [backend/deps.py](backend/deps.py) — `current_active_user`, `require_role("admin")` dependencies

**Modify**
- [backend/main.py](backend/main.py): tighten `allow_origins` from env `ALLOWED_ORIGINS` (no more `*`); mount auth router; add `Depends(current_active_user)` on every mutating route
- [backend/requirements.txt](backend/requirements.txt): add `alembic`, `sqlalchemy`, `fastapi-users[sqlalchemy]`, `asyncpg`
- [docker-compose.yml](docker-compose.yml): drop `init_postgis.sql` mount; add `command: bash -c "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8080"` to backend

**Verify**
```
docker compose run backend alembic upgrade head
psql $POSTGIS_URI -c "\dT+"          # all enums + new tables exist
curl -X POST localhost:8080/auth/register -d '{"email":"a@b.c","password":"x"}'
curl -X POST localhost:8080/auth/jwt/login -d 'username=a@b.c&password=x'
```

---

## Phase 1 — Multi-Format Ingest (Dashboard + API)  *(Size: L, ~5 days)*

**Backend create**
- [backend/ingest/__init__.py](backend/ingest/__init__.py), `formats.py` (extension → handler registry), `routes.py`
- `POST /api/ingest/upload` (multipart, auth required) — saves to `/data/imagery/incoming/<uuid>/<filename>`, infers format, dispatches Celery task
- `GET /api/vector_layers`, `GET /api/fmv_clips`, `GET /api/tiles_3d`, `GET /api/ingest/jobs/{task_id}`

**Worker tasks added to [backend/worker.py](backend/worker.py)**
- Extend `ensure_cog()` to drop extension whitelist — NITF flows through GDAL automatically
- `process_vector` — `gpd.read_file(path).to_crs(4326).to_postgis(table_name, engine, if_exists='replace')`; insert `vector_layers` row; refresh Martin
- `process_fmv` — `ffmpeg -i in.mp4 -map 0:d:0 -f data klv.bin` → `klvdata.StreamParser(...)` → `LINESTRINGZM`; transcode `-c copy -f hls -hls_time 4 -hls_segment_type fmp4 /data/fmv/<id>/index.m3u8`
- `process_3dtiles` — if `.zip` of tileset, unpack to `/data/tiles3d/<id>/`; if CityGML/LAS, run `py3dtiles convert` then unpack
- `backend/requirements.txt` adds: `geopandas==0.14.4`, `pyogrio`, `fastkml==1.0`, `lxml`, `ffmpeg-python==0.2.0`, `klvdata==0.0.10`, `py3dtiles==7.0`
- [backend/Dockerfile](backend/Dockerfile): `apt-get install ffmpeg openjdk-17-jre-headless` and copy `citygml-tools` jar

**Frontend create**
- [frontend/src/components/UploadCenter.tsx](frontend/src/components/UploadCenter.tsx) — `react-dropzone` drag-drop, per-file XHR progress, polls `/api/ingest/jobs/<task_id>`
- Wire new tab `upload` in [frontend/src/App.tsx](frontend/src/App.tsx#L10-L23)
- `package.json`: add `react-dropzone`

**Verify** — Drag a `.ntf`, `.shp.zip`, `.kml`, `.geojson`, `.mp4`, tileset `.zip`. Confirm: `/api/vector_layers` lists each; `http://localhost:3001/<table>/0/0/0.pbf` returns Martin tile; `psql -c "SELECT name,duration_s FROM fmv_clips"`; HLS plays at `/data/fmv/<id>/index.m3u8`.

---

## Phase 2 — Sensor Visualization  *(Size: M, ~3 days)*

**Backend**
- Ship colormap JSONs in [backend/colormaps/](backend/colormaps/) (`thermal_ironbow.json`, `sar_grayscale.json`, `ndvi.json`); mount into TiTiler container as `TITILER_API_CMAP_DIRECTORY=/cmap`
- New endpoint `GET /api/imagery/{pass_id}/bands` returning `src.descriptions`, `src.count`, `src.dtypes`, per-band statistics from `rasterio`

**Frontend create**
- [frontend/src/components/SensorControls.tsx](frontend/src/components/SensorControls.tsx) — per-sensor mode (RGB / single-band / index / SAR-dB / thermal), R/G/B band selectors, expression presets (NDVI, NDWI, NBR), rescale min/max sliders, colormap dropdown
- [frontend/src/lib/titilerUrl.ts](frontend/src/lib/titilerUrl.ts) — pure builder for TiTiler tile URL from controls state
- Refactor [frontend/src/components/GaiaMap.tsx](frontend/src/components/GaiaMap.tsx) — tile layer URL is `useMemo` over `SensorControls` state; keeps existing pipeline ([GaiaMap.tsx:104-142, 286-292](frontend/src/components/GaiaMap.tsx#L104-L142))

**Verify** — Load Sentinel-2 multispectral COG → switch to NDVI preset (expect green/brown). Load Sentinel-1 SAR (single-band float) → grayscale dB stretch. Load Landsat thermal band → ironbow ramp.

---

## Phase 3 — FMV Viewer + 3D Viewer (CesiumJS)  *(Size: M, ~3 days)*

**Frontend**
- [frontend/src/components/FmvViewer.tsx](frontend/src/components/FmvViewer.tsx) — `hls.js` `<video>` + Leaflet sub-map; live frame footprint polygon driven by `klv_track` JSON, interpolated by `<video>.currentTime`
- [frontend/src/components/View3D.tsx](frontend/src/components/View3D.tsx) — CesiumJS `Viewer` with `Cesium3DTileset`, `KmlDataSource`, `GeoJsonDataSource`. Tilesets list from `/api/tiles_3d`. Use offline imagery provider (SingleTileImageryProvider over local Natural Earth or our MBTiles via custom provider)
- New routes `fmv` and `space3d` in [frontend/src/App.tsx](frontend/src/App.tsx#L10-L23)
- `package.json`: add `cesium@1.124`, `vite-plugin-cesium`, `hls.js`. Vendor Cesium static assets (`Workers/`, `Assets/`, `Widgets/`) into `/frontend/public/cesium/` so no CDN call

**Verify** — Play FMV; sub-map polygon tracks playhead frame-by-frame. Load NYC sample 3D Tiles converted via py3dtiles, navigate in 3D. Drop a KML, see it overlaid on the globe.

---

## Phase 4 — Real-Time Feeds + Auto-Triggered Workflows  *(Size: M, ~3 days)*

**Backend create**
- [backend/realtime/ws.py](backend/realtime/ws.py) — `@app.websocket("/ws")` accepts `?topic=detections|feeds|training:<id>`; verifies JWT in `Sec-WebSocket-Protocol`; subscribes to Redis pub/sub channels
- [backend/feeds/runner.py](backend/feeds/runner.py) — Celery beat task reading `feed_sources`, dispatching per-kind ingestors (`pyais`, `pyModeS`, `feedparser`, `paho-mqtt`, `httpx`); each event → `feed_events` insert + `redis.publish("events:feeds", ...)`
- `GET/POST/PUT/DELETE /api/feeds` (admin-only via `Depends(require_role("admin"))`)
- `backend/worker.py`: after `store_detections` → `redis.publish("events:detections", json.dumps({pass_id, count, bbox}))`
- Auto-trigger: extend `POST /api/ingest/upload` so upload completion auto-dispatches the appropriate Celery task — no separate "trigger" step

**Frontend**
- [frontend/src/hooks/useEventStream.ts](frontend/src/hooks/useEventStream.ts) — WebSocket hook with auto-reconnect + JWT
- [frontend/src/components/GaiaMap.tsx](frontend/src/components/GaiaMap.tsx#L98-L100): replace `setInterval(fetchData, 10000)` with `useEventStream("detections")` triggering re-fetch only on event; same for feeds layer
- [frontend/src/components/admin/FeedManager.tsx](frontend/src/components/admin/FeedManager.tsx) — admin-only CRUD over `/api/feeds`

**Verify** — Open two browser windows; trigger ingest in one, see detection layer refresh in the other within ~1s. `wscat -c "ws://localhost:8080/ws?topic=detections" -H "Authorization: Bearer $JWT"` and confirm message arrives. Configure an AIS feed pointing at a local sample stream; vessels appear on the map live.

---

## Phase 5 — Model Training Admin  *(Size: L, ~5 days)*

**New compose service** — `mlflow` (`ghcr.io/mlflow/mlflow:v2.14`), sqlite backend at `/mlruns/mlflow.db`, artifact root `/mlruns/artifacts`, port 5000. New `worker-training` service (same image as `worker`, command `celery -A worker_training.celery_app worker -Q training -c 1`).

**Backend create**
- [backend/training/routes.py](backend/training/routes.py) (admin-only):
  - `POST /api/training/datasets` — multipart zip of YOLO-format dataset → unpack to `/data/datasets/<name>`
  - `POST /api/training/jobs` — body `{model_name, base_weights, dataset, sensor_type, epochs, imgsz, batch}` → enqueue on `training` queue
  - `GET /api/training/jobs`, `GET /api/training/jobs/{id}/logs` (tail file; also pushes via WS topic `training:<id>`)
  - `POST /api/models/{id}/promote` → flips `status='production'`, calls `POST /reload` on inference service
- [backend/worker_training.py](backend/worker_training.py) — separate Celery app: `from ultralytics import YOLO; YOLO(base).train(data=..., epochs=..., project='/mlruns')` with MLflow autolog; on finish insert `models` row pointing at `runs/detect/train/weights/best.pt`
- [inference/main.py](inference/main.py): add `POST /reload` re-running `load_model()` against new `MODEL_PATH`

**Frontend create**
- [frontend/src/components/admin/AdminShell.tsx](frontend/src/components/admin/AdminShell.tsx) — wraps admin routes; gates on `is_superuser` claim from JWT
- [frontend/src/components/admin/ModelTraining.tsx](frontend/src/components/admin/ModelTraining.tsx) — dataset uploader, hyperparam form, job table with live log tail (`useEventStream("training:<id>")`), model registry with Promote button, embedded `<iframe src="http://localhost:5000">` for MLflow
- New `admin` route in App.tsx; visible only to superusers

**Verify** — Upload a tiny COCO-format subset, kick a 1-epoch job, watch logs stream live, see `best.pt` registered, promote it, hit inference `/health`, confirm new `model_path` loaded.

---

## Phase 6 — Bug Fixes, Placeholder Removal, Offline Hardening  *(Size: M, ~3 days)*

**Bug + placeholder fixes (catalogued during exploration)**
- [backend/main.py:14](backend/main.py#L14): replace `allow_origins=["*"]` with env-driven list
- [backend/ai.py:14](backend/ai.py#L14): drop `"dummy"` default for `OPENAI_API_KEY`; require explicit value
- [backend/ai.py:32](backend/ai.py#L32): set `allow_dangerous_requests=False`; use `langchain_neo4j.GraphCypherQAChain` with `cypher_validator` against a read-only Neo4j user
- [backend/ai.py:9-11](backend/ai.py#L9-L11): remove hard-coded `bolt://localhost:7687` / `http://localhost:8000/v1` defaults — fail-fast if env not set
- [backend/database.py:9](backend/database.py#L9): remove `"password"` default
- [backend/worker.py:159-160](backend/worker.py#L159-L160): inference errors now logged structured + Redis-published as `events:errors`; never silently swallowed
- [backend/worker.py:239-240](backend/worker.py#L239-L240): fix path/url discrimination using `urllib.parse.urlparse(image_url).scheme`
- [inference/main.py:131-149](inference/main.py#L131-L149): **delete the random-mock branch entirely**; if no model loadable, return `{"status":"degraded","detections":[]}`. Add env `REQUIRE_MODEL=true` to hard-fail in production
- [frontend/src/components/TargetWorkbench.tsx:67-78](frontend/src/components/TargetWorkbench.tsx#L67-L78): replace hard-coded image path with `<select>` populated from `GET /api/imagery`; replace `alert()` with toast notification component
- [frontend/src/components/ConstellationView.tsx:57-63](frontend/src/components/ConstellationView.tsx#L57-L63), [:92](frontend/src/components/ConstellationView.tsx#L92): derive arc positions from `satellite.js` (offline TLE propagator) using TLEs stored in Neo4j; compute real TCA via SGP4
- All `*.tsx` `any` sprawl: introduce typed API client (`frontend/src/lib/apiClient.ts`) with response types generated from FastAPI OpenAPI schema (`openapi-typescript`)
- Add toast/notification system (`sonner`) — replace every `alert()` and silent `console.error`
- Add ErrorBoundary at App root

**Offline strategy**
1. **Wheelhouse**: every Dockerfile gets a `pip download -r requirements.txt -d /wheels` build stage; runtime install uses `--no-index --find-links=/wheels`. Commit `wheelhouse/` to git LFS for air-gapped builds.
2. [inference/Dockerfile:24](inference/Dockerfile#L24): replace PyTorch index URL with pre-baked wheel from `/wheels`
3. [inference/Dockerfile:30](inference/Dockerfile#L30): bundle YOLO weights via `COPY weights/yolov8n.pt /models/yolov8n.pt` instead of network download
4. **Basemap**: add `data/basemaps/world.mbtiles` (Natural Earth + AOI OSM extract); mount into Martin via `tiles_volume`. [frontend/src/components/GaiaMap.tsx:170](frontend/src/components/GaiaMap.tsx#L170) URL changes from `cartocdn.com` to `http://localhost:3001/basemap/{z}/{x}/{y}.pbf`
5. **Cesium static assets**: vendor `Workers/`, `Assets/`, `Widgets/` to `/frontend/public/cesium/`; set `window.CESIUM_BASE_URL='/cesium/'`
6. **Frontend Dockerfile**: vendor any web fonts; remove all CDN `<link>` tags
7. **LLM contract**: per user choice, no bundled LLM — `OPENAI_API_BASE` is required env. If unreachable on startup, Ava chat tab shows "LLM endpoint offline" banner instead of crashing
8. **Bundle target**: add `make offline-bundle` producing a single `.tar.gz` containing `docker save` images, wheelhouse, weights, MBTiles, Cesium assets

**Verify** — Disconnect host network, run `docker compose up`, confirm all services healthy, full ingest→detect→display cycle works, Cesium 3D viewer loads with no external requests (Network tab in DevTools: zero non-localhost hosts).

---

## Cross-Cutting: Tests + CI

Set up incrementally starting in Phase 0:
- **Backend**: `pytest` + `httpx` + `testcontainers-postgres`. Files [backend/tests/test_auth.py](backend/tests/test_auth.py), `test_ingest_vector.py`, `test_ingest_fmv.py`, `test_ws_detections.py`, `test_training_e2e.py`
- **Frontend**: `vitest` + `@testing-library/react` for components; **Playwright** for the four headline flows: login → upload TIF → see detection → promote model
- **CI**: [.github/workflows/ci.yml](.github/workflows/ci.yml) runs `ruff`, `mypy --strict backend/`, `pytest`, `vitest`, `playwright`, `docker compose build`
- Lint: `ruff` + `eslint` + `prettier` configs

---

## Critical Files to Modify or Create

- [backend/worker.py](backend/worker.py) — extend with `process_vector`, `process_fmv`, `process_3dtiles`; Redis publish on detection store
- [backend/main.py](backend/main.py) — mount auth, ingest, training, ws, feeds routers; tighten CORS; wire auth deps everywhere
- [backend/init_postgis.sql](backend/init_postgis.sql) — retire after Alembic baseline at `backend/migrations/versions/0001_baseline.py`
- [backend/ai.py](backend/ai.py) — security hardening (no dangerous Cypher, no dummy keys)
- [backend/database.py](backend/database.py) — remove default password
- [inference/main.py](inference/main.py) — remove mock branch; add `/reload`; sensor-aware preprocessing for SAR/thermal
- [frontend/src/components/GaiaMap.tsx](frontend/src/components/GaiaMap.tsx) — SensorControls integration; WS-driven refresh; MBTiles basemap
- [frontend/src/components/TargetWorkbench.tsx](frontend/src/components/TargetWorkbench.tsx) — remove hard-coded path; toast notifications
- [frontend/src/components/ConstellationView.tsx](frontend/src/components/ConstellationView.tsx) — replace simulated arcs with SGP4
- [frontend/src/App.tsx](frontend/src/App.tsx) — add `upload`, `fmv`, `space3d`, `admin` routes
- [docker-compose.yml](docker-compose.yml) — add `mlflow`, `worker-training`, `tiles_volume`, `models_volume`, `datasets_volume`; mount wheelhouse + weights + Cesium assets
- New: `backend/auth/`, `backend/ingest/`, `backend/realtime/`, `backend/feeds/`, `backend/training/`, `backend/migrations/`, `backend/tests/`, `frontend/src/components/admin/`, `frontend/src/hooks/useEventStream.ts`, `frontend/src/lib/apiClient.ts`, `frontend/src/lib/titilerUrl.ts`

---

## End-to-End Verification (Final Acceptance)

Run on an air-gapped VM after Phase 6:

1. `docker compose up -d` — all 13 services healthy (`docker compose ps`)
2. `curl -X POST localhost:8080/auth/register` → `/auth/jwt/login` → JWT
3. Upload via dashboard: GeoTIFF + JP2 + NITF (raster), SHP + KML + GeoJSON (vector), MP4 (FMV), 3D Tiles zip + CityGML — all appear in the appropriate tab without manual intervention
4. Sensor controls: switch a multispectral COG to NDVI; SAR to dB; thermal to ironbow ramp
5. Open two browsers → upload imagery in one → other refreshes detection layer over WebSocket within 1s
6. Configure an AIS feed → vessels appear live on map
7. Admin: upload tiny dataset → train 1-epoch YOLO → promote model → inference reloads → detections come from new model
8. Browser DevTools Network tab: every request hits localhost only — no external hosts contacted

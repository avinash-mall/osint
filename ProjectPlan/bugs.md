# Bug And Gap Audit

Date: 2026-04-26

## Scope And Method

- Reviewed the shipped source under `backend/`, `frontend/`, `inference/`, `nginx/`, `docker-compose.yml`, `README.md`, and `ProjectPlan/PLAN.md`.
- `python3 -m py_compile backend/*.py inference/main.py` succeeded, so there are no obvious Python syntax errors.
- Frontend build steps could not be executed from the host because `npm` is not installed locally and Docker socket access is denied, so TypeScript build findings are marked as source-inferred when they depend on `tsconfig.json`.
- This document focuses on real defects, logic flaws, missing functionality, placeholder behavior, and planned-only or pseudocode-only areas that are not implemented in the current repository.

## Critical Runtime And Data Integrity Issues

### C1. `GET /api/detections/geojson` is broken whenever a `bbox` is supplied

- Location: `backend/main.py:247-267`, `backend/init_postgis.sql:58-95`
- Problem: the API builds `bbox_geom` as the string `ST_MakeEnvelope(...)` and passes that string into `get_detections_geojson(...)`, but the SQL function expects a real PostGIS `GEOMETRY`, not a SQL expression encoded as text.
- Impact: GaiaMap always sends a bounding box, so the detections overlay is likely to fail on every real map request.

### C2. The worker inserts a `POLYGON` into a `MULTIPOLYGON` column

- Location: `backend/worker.py:51-72`, `backend/worker.py:255-271`, `backend/init_postgis.sql:5-15`
- Problem: `get_raster_footprint()` returns a Shapely `Polygon`, then `process_satellite_imagery()` inserts `footprint.wkt` into `satellite_passes.footprint`, which is declared as `GEOMETRY(MULTIPOLYGON, 4326)`.
- Impact: PostGIS will reject the ingest or force the pipeline into an error path before any pass is cataloged.

### C3. `gdal_translate` is required by the worker but never installed in the backend image

- Location: `backend/worker.py:38-47`, `backend/Dockerfile:1-20`
- Problem: non-NetCDF ingest paths call `gdal_translate`, but the image only installs runtime libraries such as `libgl1`, `libglib2.0-0`, and `libgomp1`.
- Impact: GeoTIFF and JP2 ingest will fail at COG conversion time even if the Python environment is otherwise healthy.

### C4. Remote imagery ingestion is not implemented even though the API accepts `image_url`

- Location: `backend/worker.py:233-241`, `backend/worker.py:248-253`, `README.md:102-117`
- Problem: there is no download, copy, or S3 retrieval step. The code only works when the file already exists on the local shared volume. The `startswith("s3://") == False` branch is also logically wrong and hides that missing behavior.
- Impact: `http://...`, `https://...`, and `s3://...` inputs are effectively unsupported.

### C5. Re-ingesting the same image can create duplicate Neo4j `SatellitePass` and `Detection` nodes

- Location: `backend/worker.py:261-273`, `backend/worker.py:277-302`, `backend/worker.py:201-221`
- Problem: PostGIS uses `ON CONFLICT (file_path) DO UPDATE`, but Neo4j always uses `CREATE` for the corresponding `SatellitePass`. Later, `store_detections()` matches every `SatellitePass` with the same `postgis_id`, which can multiply detection nodes on repeat ingest.
- Impact: reprocessing the same pass can corrupt graph state and produce duplicate detections.

### C6. The pipeline never runs entity resolution after storing detections

- Location: `backend/worker.py:304-310`, `backend/main.py:270-351`, `README.md:109-117`
- Problem: the README says ingest runs entity resolution, but the worker stops after `store_detections()`. `resolve_detection()` exists only as a manual API endpoint.
- Impact: detections do not become targets automatically, and the advertised end-to-end pipeline is incomplete.

### C7. Overlapping chips are stored without any cross-chip deduplication

- Location: `backend/worker.py:87-90`, `backend/worker.py:155-157`, `backend/worker.py:169-223`
- Problem: the worker intentionally overlaps tiles by 100 pixels, but there is no NMS or merge step across adjacent chips before insert.
- Impact: the same object can be stored multiple times, especially near chip borders.

### C8. The NetCDF conversion branch uses an invalid xarray dimension access pattern

- Location: `backend/worker.py:27-33`
- Problem: `rioxarray.open_rasterio()` returns a `DataArray`, but the code uses `ds.dims["band"]`, which is not the normal `DataArray` access pattern.
- Impact: the NetCDF path is likely to break before conversion completes.

### C9. Chips are exported as PNG, which is a poor fit for many real satellite rasters

- Location: `backend/worker.py:107-116`
- Problem: the worker writes every chip as PNG, even though satellite rasters are often multi-band, non-RGB, or higher bit depth.
- Impact: ingest can fail on valid imagery or silently lose spectral and dynamic-range information before inference.

### C10. Inference failures are swallowed and the pipeline continues

- Location: `backend/worker.py:119-164`
- Problem: exceptions only print an error message, then processing continues with partial or zero detections.
- Impact: the task can look successful while silently dropping chips and producing incomplete results.

### C11. The inference service fabricates random detections when no model is available

- Location: `inference/main.py:131-159`, `README.md:38`
- Problem: the fallback mode returns random `Vessel`, `Aircraft`, and `Facility` detections while reporting `"status": "success"`.
- Impact: downstream users cannot distinguish missing-model conditions from real detections.

### C12. `resolve_detection()` creates inconsistent graph state

- Location: `backend/main.py:304-345`
- Problem: the existing-target path does not verify that a matching Neo4j `Detection` node was found, and the new-target path creates a target without linking it to the detection node at all.
- Impact: graph lineage is inconsistent and detection-to-target history cannot be trusted.

## Major Logic And Functionality Gaps

### M1. Target priority sorting is wrong

- Location: `backend/main.py:101-105`
- Problem: priorities are sorted lexicographically as strings.
- Impact: `Medium` and `Low` can appear before `High`, which is the opposite of expected tactical ordering.

### M2. Seeded targets cannot participate in geospatial entity resolution

- Location: `backend/main.py:287-300`, `backend/add_targets.py:5-9`, `backend/seed.py:13-18`
- Problem: entity resolution only considers targets with `latitude` and `longitude`, but the seeded targets do not have coordinates.
- Impact: `POST /api/detections/resolve` will almost always create a new target instead of matching seeded ones.

### M3. The graph endpoint omits disconnected nodes and all observations

- Location: `backend/main.py:47-52`
- Problem: the query only returns `(n)-[r]->(m)` patterns and explicitly excludes `Observation`.
- Impact: standalone targets, satellites, and raw telemetry are missing from both the graph view and the browser even though the UI and README describe broader coverage.

### M4. The Browser is not a raw data browser

- Location: `frontend/src/components/Browser.tsx:7-20`, `frontend/src/components/Browser.tsx:34-67`, `README.md:58`
- Problem: it simply renders the already-filtered `/api/graph` payload and provides no search, sort, filter, pagination, or telemetry-specific view.
- Impact: the module does not meet its stated purpose.

### M5. The Graph Explorer action buttons are placeholders

- Location: `frontend/src/components/GraphExplorer.tsx:157-186`
- Problem: `Focus`, `Export`, `Search Around`, `Add to Filter`, and `Expand Node` render as buttons but have no handlers.
- Impact: the UI advertises graph workflows that do not exist.

### M6. Target Workbench detection history ignores the selected target

- Location: `frontend/src/components/TargetWorkbench.tsx:31-37`, `frontend/src/components/TargetWorkbench.tsx:47-53`, `frontend/src/components/TargetWorkbench.tsx:210-230`
- Problem: `fetchTargetDetections(targetId)` ignores `targetId` and loads the latest 50 detections globally.
- Impact: the detail panel shows unrelated detections and cannot be trusted as target history.

### M7. Target Workbench can only trigger one hard-coded imagery path

- Location: `frontend/src/components/TargetWorkbench.tsx:67-78`
- Problem: the ingest button always submits `/data/imagery/incoming/usgs_pass_001.tif`.
- Impact: users cannot select an available pass, upload a new image, or trigger ingestion for a real targeting workflow.

### M8. GaiaMap's "tactical grid" layer is wired as a raster tile layer even though Martin serves vector tiles

- Location: `frontend/src/components/GaiaMap.tsx:277-283`, `README.md:166`
- Problem: `TileLayer` expects raster tiles, but Martin emits MVT. There is no `Leaflet.VectorGrid`, `MapLibre`, or equivalent vector-tile client path in the current app.
- Impact: the grid layer will not render correctly even if Martin is running.

### M9. The vector-basemap tables are created but never loaded with data

- Location: `backend/init_postgis.sql:39-55`
- Problem: the schema creates `ne_countries` and `ne_coastline`, but there is no loader, migration, or seed job that populates them.
- Impact: the Martin layer is empty even if the frontend rendering were fixed.

### M10. The map is not offline-capable

- Location: `frontend/src/components/GaiaMap.tsx:269-275`, `README.md:17`, `README.md:169`
- Problem: the basemap comes from the public CARTO CDN.
- Impact: the app cannot satisfy the offline or air-gapped behavior described elsewhere in the project.

### M11. The Nginx tile cache is configured but bypassed by the frontend

- Location: `docker-compose.yml:72-75`, `docker-compose.yml:147-155`, `frontend/src/components/GaiaMap.tsx:10`, `frontend/src/components/GaiaMap.tsx:287-290`, `README.md:139-141`
- Problem: the frontend points directly at TiTiler on `8081`, not the Nginx cache proxy on `8090`.
- Impact: the caching layer adds complexity without delivering the advertised performance benefit.

### M12. The timeline is not actually a slider

- Location: `frontend/src/components/GaiaMap.tsx:383-416`, `README.md:55`
- Problem: the UI contains two datetime inputs and a decorative blue bar, but there is no actual scrubber or range-slider interaction.
- Impact: the marketed "functional time slider" is not implemented.

### M13. The map and chat status indicators are hard-coded and can be misleading

- Location: `frontend/src/App.tsx:69-77`, `frontend/src/components/AvaChat.tsx:47-49`
- Problem: the header always shows `NETWORK SECURE`, `ONTOLOGY SYNCED`, and `ONLINE` without checking backend, database, or model health.
- Impact: operators can be shown a healthy-looking interface while services are actually down.

### M14. Constellation altitude handling is wrong enough to break the visualization

- Location: `frontend/src/components/ConstellationView.tsx:31-35`, `frontend/src/components/ConstellationView.tsx:91`, `backend/seed.py:72-77`, `backend/add_constellation.py:11-14`
- Problem: one seed script stores altitude in meters, another stores small values that look like kilometers, the frontend divides by `1000`, then displays `sat.alt * 1000` as `km`, and also feeds the resulting huge values into `react-globe.gl`.
- Impact: rendered positions and displayed altitude values are inconsistent and likely nonsensical.

### M15. Constellation orbits and collection windows are simulated placeholders

- Location: `frontend/src/components/ConstellationView.tsx:57-63`, `frontend/src/components/ConstellationView.tsx:92`
- Problem: future positions are `lat + 10` and `lng + 20`, and TCA is a string template.
- Impact: the module looks live but is not based on any orbital model or telemetry.

## Build, Deployment, And Operational Issues

### O1. The frontend likely fails `tsc` because `noUnusedLocals` is enabled and there are obvious unused imports

- Location: `frontend/tsconfig.json:17-21`, `frontend/src/components/GaiaMap.tsx:1-2`, `frontend/src/components/TargetWorkbench.tsx:3`, `frontend/src/components/ConstellationView.tsx:4`
- Status: source-inferred
- Problem: `GaiaMap` imports `useRef`, `ImageOverlay`, and `LayersControl` without using them; `TargetWorkbench` imports `ExternalLink`; `ConstellationView` imports `MapIcon`.
- Impact: `npm run build` is expected to fail under the current TypeScript settings.

### O2. The frontend container runs the Vite dev server instead of a production build

- Location: `frontend/Dockerfile:1-8`
- Problem: the container uses `npm run dev` as its main process.
- Impact: the deployed app is not a production build, is slower, and depends on dev-server behavior.

### O3. The inference stack is CPU-only despite README claims about GPU acceleration

- Location: `inference/Dockerfile:1-24`, `docker-compose.yml:101-113`, `README.md:33-38`, `README.md:168`
- Problem: the image is based on `python:3.11-slim`, forces `DEVICE=cpu`, and installs CPU-only PyTorch.
- Impact: the documented GPU path does not exist in the shipped configuration.

### O4. The favicon path is wrong

- Location: `frontend/index.html:5`
- Problem: the page points to `/vite.svg`, but the actual public assets contain `favicon.svg` and not `vite.svg`.
- Impact: the deployed frontend will make a broken asset request on load.

### O5. The worker comments promise download and validation steps that are not present

- Location: `backend/worker.py:227-230`
- Problem: the docstring claims `download/validate -> COG conversion -> catalog -> inference -> store`, but only the later steps are partially implemented.
- Impact: the code is easier to misread and operational expectations are wrong.

### O6. There is no automated test suite in the repository

- Location: repository-wide
- Problem: there is no `backend/tests`, no frontend `__tests__`, and no E2E suite.
- Impact: regressions in geospatial math, ingest, graph behavior, and UI wiring will be hard to catch.

## Security And Safety Risks

### S1. CORS is fully open

- Location: `backend/main.py:12-18`
- Problem: `allow_origins=["*"]` is used with credentials allowed.
- Impact: the API is overly permissive and not suitable for a real analyst-facing deployment.

### S2. The AI chain is configured for dangerous requests with weak defaults

- Location: `backend/ai.py:9-15`, `backend/ai.py:28-33`
- Problem: the service defaults to `bolt://localhost:7687`, `OPENAI_API_KEY="dummy"`, and `allow_dangerous_requests=True`.
- Impact: the chat path is unsafe, brittle, and too easy to misconfigure.

### S3. Database defaults are insecure

- Location: `backend/database.py:8-12`
- Problem: the code bakes in default Neo4j and Postgres connection details, including a default Neo4j password.
- Impact: accidental insecure deployments become much more likely.

### S4. Backend errors are surfaced as user-facing chat content

- Location: `backend/ai.py:17-38`, `frontend/src/components/AvaChat.tsx:25-33`
- Problem: AI and database failures are returned as plain strings and rendered as normal bot replies.
- Impact: internal error details leak to the UI, and the frontend cannot distinguish a valid answer from a backend failure.

## Unimplemented, Placeholder, Or Pseudocode-Only Areas

### U1. FMV support exists in the plan but not in the codebase

- Planned in: `ProjectPlan/PLAN.md:1025-1030`, `ProjectPlan/PLAN.md:1209-1279`
- Missing files: `backend/workers/video.py`, `backend/routes/fmv.py`, `frontend/src/components/FmvOverlayCanvas.tsx`, `frontend/src/components/FmvSubMap.tsx`
- Impact: a large planned feature area is still pseudocode-only.

### U2. WebSocket fanout and live feed plumbing are still plan-only

- Planned in: `ProjectPlan/PLAN.md:1294-1458`
- Missing files: `frontend/src/hooks/useEventStream.ts`
- Current implementation: `frontend/src/components/GaiaMap.tsx:99` still uses a 10-second polling loop for one data path, and no live feed ingestors exist in the repository.
- Impact: the real-time architecture described in the plan has not been implemented.

### U3. FMV serving exists in Nginx config, but there is no producer or consumer

- Location: `nginx/tile-proxy.conf:20-37`
- Problem: Nginx exposes `/fmv/`, but there is no current backend job that creates HLS output and no frontend player that consumes it.
- Impact: this looks like partial scaffolding rather than a completed feature.

### U4. `View3D.tsx` is dead code

- Location: `frontend/src/components/View3D.tsx:1-80`, `frontend/src/App.tsx:13-21`
- Problem: a Cesium-based 3D component exists, but the app never renders it.
- Impact: the repo contains an unused 3D path that is not exposed to users.

### U5. Several UI labels overstate implementation maturity

- Location: `README.md:49-61`, `frontend/src/components/GraphExplorer.tsx:157-186`, `frontend/src/components/TargetWorkbench.tsx:31-78`, `frontend/src/components/ConstellationView.tsx:57-92`
- Problem: the README and UI present richer workflows than the code actually provides.
- Impact: users and future contributors can easily overestimate what is finished.

## Suggested Fix Order

1. Fix the ingest blockers first: `C1`, `C2`, `C3`, `C4`, `C8`, `C9`, `C10`, `C11`.
2. Then fix graph and data integrity issues: `C5`, `C6`, `C7`, `C12`, `M1`, `M2`, `M3`.
3. Then repair the map stack: `M8`, `M9`, `M10`, `M11`, `M12`.
4. Then clean up user-facing false affordances: `M5`, `M6`, `M7`, `M13`, `M14`, `M15`, `U5`.
5. After that, address deployment and security hardening: `O1`, `O2`, `O3`, `S1`, `S2`, `S3`, `S4`.
6. Finally, either implement or explicitly remove the planned-only FMV and WebSocket areas: `U1`, `U2`, `U3`, `U4`, `O6`.

# Gotham-Inspired OSINT Platform

This project is an open-source, simplified implementation of a decision-making platform inspired by Palantir Gotham. It is designed to ingest, synthesize, and act upon disparate data streams by modeling them within a mathematical graph ontology, visualized through a modern, dark-mode tactical dashboard.

## Technical Architecture

The platform is containerized using Docker and is split into multiple layers:

### 1. The Semantic Ontology (Neo4j)
Instead of a rigid relational database, the core of the system relies on **Neo4j**, a native graph database. This forms the "Semantic Ontology," mapping real-world complexities (Locations, Personnel, Events) into Nodes and Relationships. This mathematical graph allows for fluid querying and discovery without strict schema limitations.

### 2. Spatial Intelligence Catalog (PostGIS)
A dedicated **PostGIS** database handles heavy geospatial geometry that Neo4j is not optimized for:
- Satellite imagery footprints (MultiPolygons with CRS metadata)
- Detection bounding boxes as native `geometry(Polygon, 4326)`
- Time-series metadata (acquisition time, sensor type, cloud cover)
- Vector basemap data for offline-capable Mapbox Vector Tiles (MVT)

### 3. Backend API & Cognitive Engine (Python / FastAPI)
The backend is a high-performance Python server built with **FastAPI**.
*   **Ontology Access:** Manages the Neo4j driver connection pool and provides REST endpoints for the frontend modules to securely access geospatial telemetry and raw ontological graphs.
*   **PostGIS Integration:** New endpoints for satellite imagery catalog, AI detections, and spatial queries.
*   **Ava Cognitive Engine (GraphRAG):** Uses **LangChain** (`GraphCypherQAChain`) integrated with a local OpenAI-compatible LLM endpoint (e.g., `gemma-4-31B-it`). It acts as an Ontology Augmented Generation (OAG) system, taking natural language queries from the user and translating them deterministically into Neo4j Cypher queries.

### 4. Tile Server (TiTiler)
A modern FastAPI-based server that handles **Cloud Optimized GeoTIFFs (COG)** and NetCDF on-the-fly. TiTiler serves dynamic satellite imagery tiles directly from raw files without pre-rendering.
- Endpoint: `http://localhost:8081/cog/tiles/{z}/{x}/{y}?url=/data/imagery/my_satellite.tif`

### 5. Vector Tile Server (Martin)
Serves **Mapbox Vector Tiles (MVT)** from PostGIS for borders, coastlines, and tactical grids. Enables instant map style switching without downloading new raster images.
- Endpoint: `http://localhost:3001`

### 6. AI Inference Service (Python / FastAPI + GPU)
A dedicated container running **YOLOv8** with **SAHI** (Slicing Aided Hyper Inference) for large satellite imagery object detection.
- Accepts 640x640 image chips
- Returns normalized bounding boxes, class labels, and confidence scores
- GPU acceleration via NVIDIA CUDA (`--gpus all`)
- Fallback to mock mode if no model is available

### 7. Worker Queue (Celery + Redis)
Manages heavy imagery processing in background workers:
- **COG Conversion:** GeoTIFF/JP2/NetCDF -> Cloud Optimized GeoTIFF via GDAL / rioxarray
- **Tiling Inference:** Slices large rasters into chips, dispatches to AI service, georeferences results
- **Entity Resolution:** Matches detections to existing Neo4j Targets or creates new ones

### 8. Frontend Tactical Dashboard (React / TypeScript / Vite)
The frontend relies on **React**, **TypeScript**, and **Tailwind CSS v4** to deliver a data-dense, highly responsive "Titanium Client" aesthetic.

It is broken down into four core operational modules:
*   **Ontology Explorer:** Utilizes `react-force-graph-2d` to render the complex semantic links of the graph database interactively.
*   **Gaia Geospatial Platform:** Utilizes `react-leaflet` with:
    - CARTO Dark Matter base tiles
    - TiTiler satellite imagery overlay with opacity control
    - AI detection GeoJSON overlay (Vessel, Aircraft, Facility)
    - Functional time slider for temporal filtering
    - Layer control panel for toggling data sources
    - Martin vector tile grid overlay
*   **Browser:** A tabular data explorer for high-speed manipulation and viewing of raw telemetry and property nodes.
*   **Ava Chat:** The visual interface for the cognitive engine, allowing analysts to query the ontology using natural language.
*   **Target Workbench:** HPTL management with detection history, status updates, and satellite pass triggering.
*   **Constellation View:** 3D globe visualization of orbital assets via `react-globe.gl`.

---

## Getting Started

### Prerequisites
*   Docker and Docker Compose
*   NVIDIA Container Toolkit (for GPU inference acceleration)
*   A local LLM server running on `http://localhost:8000/v1` (e.g., vLLM, llama.cpp, Ollama) if you wish to use the Ava Cognitive Engine. (Configurable in `docker-compose.yml`).

### Installation & Deployment

1. **Build and Start the Infrastructure**
   ```bash
   docker compose build --no-cache
   docker compose up -d
   ```
   This will spin up the `neo4j`, `postgis`, `titiler`, `martin`, `backend`, `frontend`, `inference`, `redis`, `worker`, and `nginx` containers.

2. **Seed the Ontology**
   Populate the Neo4j database with mock entities and links:
   ```bash
   docker exec -it osint-backend-1 python seed.py
   ```

3. **Seed PostGIS (Optional)**
   Add sample satellite passes and detections for demonstration:
   ```bash
   docker exec -it osint-backend-1 python seed_postgis.py
   ```

4. **Access the Dashboard**
   Open your browser and navigate to `http://localhost:3000`.

---

## Satellite Imagery Pipeline

### Ingesting New Imagery

1. Drop raw `.tif`, `.jp2`, or `.nc` files into the shared volume at `/data/imagery/incoming/`
2. Trigger ingestion via the API or Target Workbench:
   ```bash
   curl -X POST http://localhost:8080/api/ingest \
     -H "Content-Type: application/json" \
     -d '{"image_url": "/data/imagery/incoming/my_image.tif", "sensor_type": "Optical"}'
   ```
3. The Celery worker will:
   - Convert to Cloud Optimized GeoTIFF (COG)
   - Catalog the pass in PostGIS with footprint geometry
   - Create a `SatellitePass` node in Neo4j
   - Slice the COG into 640x640 chips
   - Run AI inference on each chip
   - Georeference pixel coordinates to Lat/Lon
   - Store detections in PostGIS and Neo4j
   - Run entity resolution against existing Targets

### Querying Detections

```bash
# Get detections as GeoJSON (for map overlay)
curl "http://localhost:8080/api/detections/geojson?bbox=54.0,24.0,56.0,26.0&start_time=2024-01-01T00:00:00Z"

# Get detections with metadata
curl "http://localhost:8080/api/detections?bbox=54.0,24.0,56.0,26.0&det_class=Vessel&limit=100"

# Resolve a detection to a Target
curl -X POST "http://localhost:8080/api/detections/resolve?detection_id=1&distance_threshold_meters=500"
```

### TiTiler Tile URLs

Once a pass is cataloged, tiles are served dynamically:
```
http://localhost:8081/cog/tiles/{z}/{x}/{y}?url=/data/imagery/processed/my_image_cog.tif
```

Or via the Nginx cache proxy:
```
http://localhost:8090/cog/tiles/{z}/{x}/{y}?url=/data/imagery/processed/my_image_cog.tif
```

---

## Component Details

| Component | Technology | Purpose |
|-----------|------------|---------|
| **Database (Graph)** | Neo4j `5.20.0` | Semantic ontology, intelligence graph |
| **Database (Spatial)** | PostGIS `16-3.4` | Imagery catalog, detection geometries, vector tiles |
| **Backend** | Python `3.11-slim`, FastAPI | REST API, ontology access, spatial queries |
| **Tile Server** | TiTiler (latest) | Dynamic COG/NetCDF tile serving |
| **Vector Tiles** | Martin (latest) | MVT generation from PostGIS |
| **AI Inference** | Python `3.11`, CUDA `12.1`, YOLOv8, SAHI | Satellite imagery object detection |
| **Worker Queue** | Celery + Redis `alpine` | Background imagery processing |
| **Cache Proxy** | Nginx `alpine` | Tile caching for smooth panning |
| **Frontend** | Node `22-alpine`, React 18, Vite, Tailwind CSS v4 | Tactical dashboard |

---

## Implementation Tips for Offline Stability

| Feature | Solution |
|---------|----------|
| **Vector Tiles** | Martin serves MVT from PostGIS. Style them client-side with Leaflet.VectorGrid or MapLibre GL JS. |
| **Worker Queues** | Celery + Redis with task routing: `celery -Q imagery,default worker`. The `imagery` queue handles heavy GDAL/inference tasks. |
| **Hardware Acceleration** | Inference Dockerfile uses `nvidia/cuda:12.1.0-runtime-ubuntu22.04`. Docker Compose passes `deploy.resources.reservations.devices` for GPU access. |
| **Offline Basemap** | Pre-seed Martin with Natural Earth Data (1:10m) for borders, coastlines, and populated places. |
| **Tile Caching** | Nginx proxy in front of TiTiler caches tiles with `proxy_cache` for 24h. |
| **NetCDF Time-Series** | Store original NetCDFs. On ingest, the worker extracts each time step as a separate COG or Zarr store. TiTiler can serve Zarr via `titiler-xarray`. |

---

## API Endpoints

### Imagery
- `GET /api/imagery` — Query satellite passes (supports `bbox`, `start_time`, `end_time`, `sensor_type`)
- `GET /api/imagery/{id}/tiles` — Get TiTiler tile URL for a pass

### Detections
- `GET /api/detections` — Query detections with spatial/temporal filters
- `GET /api/detections/geojson` — Return detections as GeoJSON FeatureCollection
- `POST /api/detections/resolve` — Entity resolution against existing Targets

### Existing Endpoints
- `GET /api/graph` — Neo4j graph data
- `GET /api/geotime/features` — Static features and tracks
- `GET /api/targets` — Target list
- `PUT /api/targets/{id}/status` — Update target status
- `GET /api/constellation` — Satellite constellation data
- `POST /api/ingest` — Trigger satellite imagery pipeline
- `POST /api/chat` — Ava cognitive engine

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from fastapi import FastAPI, Query, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import uvicorn
from database import db, postgis_db
from ai import AIUnavailable, ai_status, get_ai_response
from worker import celery_app, process_satellite_imagery

app = FastAPI(title="Gotham API")

def get_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Existing Models ---
class ChatRequest(BaseModel):
    message: str

class TargetStatusUpdate(BaseModel):
    status: str

class IngestRequest(BaseModel):
    image_url: str
    sensor_type: Optional[str] = "Optical"
    acquisition_time: Optional[str] = None


class FeedConnectRequest(BaseModel):
    name: str
    feed_type: str
    endpoint: str
    protocol: str = "tcp"
    topic: Optional[str] = "feeds"
    parser: Optional[str] = None
    enabled: bool = True


class DetectionQuery(BaseModel):
    bbox: Optional[List[float]] = None  # [min_lon, min_lat, max_lon, max_lat]
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    det_class: Optional[str] = None


class GraphActionRequest(BaseModel):
    node_id: str


def parse_bbox(bbox: str) -> tuple[float, float, float, float]:
    try:
        values = tuple(map(float, bbox.split(",")))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid bbox format. Use min_lon,min_lat,max_lon,max_lat")
    if len(values) != 4:
        raise HTTPException(status_code=400, detail="Invalid bbox format. Use min_lon,min_lat,max_lon,max_lat")
    min_lon, min_lat, max_lon, max_lat = values
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(status_code=400, detail="Invalid bbox extents")
    return min_lon, min_lat, max_lon, max_lat


def safe_filename(filename: str) -> str:
    name = Path(filename or "upload.tif").name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "upload.tif"


def ensure_feed_tables() -> None:
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feed_sources (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                feed_type VARCHAR(100) NOT NULL,
                protocol VARCHAR(50) NOT NULL,
                endpoint VARCHAR(1024) NOT NULL,
                topic VARCHAR(255) DEFAULT 'feeds',
                parser VARCHAR(100),
                enabled BOOLEAN DEFAULT TRUE,
                status VARCHAR(50) DEFAULT 'configured',
                last_error TEXT,
                last_seen TIMESTAMP WITH TIME ZONE,
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feed_events (
                id SERIAL PRIMARY KEY,
                source_id INTEGER REFERENCES feed_sources(id) ON DELETE CASCADE,
                event_type VARCHAR(100),
                payload JSONB DEFAULT '{}',
                geom GEOMETRY(POINT, 4326),
                observed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_sources_type ON feed_sources(feed_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_events_geom ON feed_events USING GIST(geom)")

# --- Shutdown ---
@app.on_event("shutdown")
def shutdown_event():
    db.close()


@app.get("/api/health")
def health():
    status = {
        "api": "ok",
        "neo4j": "unknown",
        "postgis": "unknown",
        "ai": ai_status(),
    }
    try:
        with db.get_session() as session:
            session.run("RETURN 1 AS ok").single()
        status["neo4j"] = "ok"
    except Exception:
        status["neo4j"] = "error"

    try:
        with postgis_db.get_cursor() as cursor:
            cursor.execute("SELECT 1 AS ok")
            cursor.fetchone()
        status["postgis"] = "ok"
    except Exception:
        status["postgis"] = "error"

    status["healthy"] = status["neo4j"] == "ok" and status["postgis"] == "ok"
    return status


@app.get("/api/feeds")
def list_feeds():
    ensure_feed_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, name, feed_type, protocol, endpoint, topic, parser, enabled,
                   status, last_error, last_seen, metadata, created_at, updated_at
            FROM feed_sources
            ORDER BY updated_at DESC, created_at DESC
        """)
        return {"feeds": [dict(row) for row in cursor.fetchall()]}


@app.post("/api/feeds/connect")
def connect_feed(req: FeedConnectRequest):
    ensure_feed_tables()
    if req.protocol.lower() not in {"tcp", "udp", "http", "https", "websocket", "file", "serial"}:
        raise HTTPException(status_code=400, detail="Unsupported feed protocol")
    if not req.endpoint.strip():
        raise HTTPException(status_code=400, detail="Feed endpoint is required")

    status = "connected" if req.enabled else "configured"
    metadata = {
        "requested_by": "ui",
        "note": "Connector registered. Runtime collectors consume this row to start stream ingestion.",
    }
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO feed_sources (name, feed_type, protocol, endpoint, topic, parser, enabled, status, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, name, feed_type, protocol, endpoint, topic, parser, enabled,
                      status, last_error, last_seen, metadata, created_at, updated_at
        """, (
            req.name,
            req.feed_type,
            req.protocol.lower(),
            req.endpoint,
            req.topic or "feeds",
            req.parser,
            req.enabled,
            status,
            json.dumps(metadata),
        ))
        feed = dict(cursor.fetchone())

    try:
        import redis

        redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
        redis_client.publish(f"events:{req.topic or 'feeds'}", json.dumps({"type": "feed_connected", "feed": feed}, default=str))
        redis_client.close()
    except Exception:
        pass

    return {"success": True, "feed": feed}


@app.put("/api/feeds/{feed_id}/status")
def update_feed_status(feed_id: int, enabled: bool = True):
    ensure_feed_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            UPDATE feed_sources
            SET enabled = %s,
                status = CASE WHEN %s THEN 'connected' ELSE 'disabled' END,
                updated_at = NOW()
            WHERE id = %s
            RETURNING id, name, feed_type, protocol, endpoint, topic, parser, enabled,
                      status, last_error, last_seen, metadata, created_at, updated_at
        """, (enabled, enabled, feed_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Feed source not found")
        return {"success": True, "feed": dict(row)}


# --- Graph Endpoints (Existing) ---
@app.get("/api/graph")
def get_graph():
    with db.get_session() as session:
        result = session.run("""
            MATCH (n)
            OPTIONAL MATCH (n)-[r]->(m)
            RETURN n, r, m
            LIMIT 1500
        """)
        nodes = {}
        links = []
        for record in result:
            n = record["n"]
            m = record["m"]
            r = record["r"]
            
            nodes[n.element_id] = {"id": n.element_id, "label": list(n.labels)[0], "properties": dict(n)}
            if m is not None:
                nodes[m.element_id] = {"id": m.element_id, "label": list(m.labels)[0], "properties": dict(m)}
            if r is not None and m is not None:
                links.append({"source": n.element_id, "target": m.element_id, "type": r.type})
            
        return {"nodes": list(nodes.values()), "links": links}


@app.post("/api/graph/neighborhood")
def get_graph_neighborhood(req: GraphActionRequest):
    with db.get_session() as session:
        result = session.run("""
            MATCH (n)
            WHERE elementId(n) = $id
            OPTIONAL MATCH (n)-[rel]-(m)
            WITH n, collect(DISTINCT m) AS neighbors, collect(DISTINCT rel) AS rels
            RETURN n, neighbors,
                   [rel IN rels WHERE rel IS NOT NULL |
                    {source: elementId(startNode(rel)), target: elementId(endNode(rel)), type: type(rel)}] AS links
        """, {"id": req.node_id})
        record = result.single()
        if not record:
            raise HTTPException(status_code=404, detail="Node not found")

        nodes = {record["n"].element_id: {
            "id": record["n"].element_id,
            "label": list(record["n"].labels)[0],
            "properties": dict(record["n"]),
        }}
        for node in record["neighbors"]:
            if node is not None:
                nodes[node.element_id] = {
                    "id": node.element_id,
                    "label": list(node.labels)[0],
                    "properties": dict(node),
                }
        return {"nodes": list(nodes.values()), "links": record["links"]}

@app.get("/api/geotime/features")
def get_geotime_features():
    with db.get_session() as session:
        result_static = session.run("""
            MATCH (n)
            WHERE (n:Base OR n:LaunchPoint) AND n.latitude IS NOT NULL
            RETURN n
        """)
        static_features = [{"id": r["n"].element_id, "label": list(r["n"].labels)[0], "properties": dict(r["n"])} for r in result_static]

        result_tracks = session.run("""
            MATCH (a:Asset)-[:OBSERVED_AT]->(o:Observation)
            WITH a, o ORDER BY o.timestamp DESC
            WITH a, collect(o) as obs
            RETURN a, obs[0] as latest, obs
        """)
        tracks = []
        for r in result_tracks:
            asset = r["a"]
            latest = r["latest"]
            history = [{"lat": ob["latitude"], "lng": ob["longitude"], "time": ob["timestamp"]} for ob in r["obs"]]
            tracks.append({
                "id": asset.element_id,
                "label": list(asset.labels)[0],
                "asset_id": asset["id"],
                "properties": dict(asset),
                "latest": dict(latest),
                "history": history
            })
            
        return {"static": static_features, "tracks": tracks}

@app.get("/api/targets")
def get_targets():
    with db.get_session() as session:
        result = session.run("""
            MATCH (t:Target)
            RETURN t
            ORDER BY CASE t.priority WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 WHEN 'Low' THEN 1 ELSE 0 END DESC,
                     t.name ASC
        """)
        targets = []
        for record in result:
            t = record["t"]
            targets.append({
                "id": t.element_id,
                "properties": dict(t)
            })
        return {"targets": targets}


@app.get("/api/targets/{target_id}/detections")
def get_target_detections(target_id: str, limit: int = 50):
    """Return detections linked to a target, with a geospatial fallback for seeded targets."""
    with db.get_session() as session:
        result = session.run("""
            MATCH (t:Target)
            WHERE elementId(t) = $target_id OR t.id = $target_id
            OPTIONAL MATCH (t)-[:DETECTED_AS]->(d:Detection)
            RETURN t.latitude AS lat, t.longitude AS lon, collect(d.postgis_id) AS detection_ids
        """, {"target_id": target_id})
        record = result.single()
        if not record:
            raise HTTPException(status_code=404, detail="Target not found")

        detection_ids = [int(i) for i in record["detection_ids"] if i is not None]
        lat = record["lat"]
        lon = record["lon"]

    base_query = """
        SELECT d.id, d.class, d.confidence, d.pass_id, d.metadata, d.created_at,
               ST_AsGeoJSON(d.geom) as geom_geojson,
               ST_AsGeoJSON(d.centroid) as centroid_geojson,
               sp.name as pass_name, sp.acquisition_time, sp.file_path
        FROM detections d
        JOIN satellite_passes sp ON d.pass_id = sp.id
    """
    with postgis_db.get_cursor() as cursor:
        if detection_ids:
            cursor.execute(base_query + """
                WHERE d.id = ANY(%s)
                ORDER BY sp.acquisition_time DESC NULLS LAST, d.confidence DESC
                LIMIT %s
            """, (detection_ids, limit))
        elif lat is not None and lon is not None:
            cursor.execute(base_query + """
                ORDER BY d.centroid <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                LIMIT %s
            """, (lon, lat, limit))
        else:
            return {"detections": []}
        return {"detections": [dict(row) for row in cursor.fetchall()]}

@app.put("/api/targets/{target_id}/status")
def update_target_status(target_id: str, req: TargetStatusUpdate):
    with db.get_session() as session:
        result = session.run("""
            MATCH (t:Target)
            WHERE elementId(t) = $id
            SET t.status = $status
            RETURN t
        """, {"id": target_id, "status": req.status})
        
        record = result.single()
        if record:
            return {"success": True, "target": dict(record["t"])}
        return {"success": False, "error": "Target not found"}

@app.get("/api/constellation")
def get_constellation():
    with db.get_session() as session:
        result = session.run("""
            MATCH (s:Satellite)
            RETURN s
        """)
        satellites = []
        for record in result:
            s = record["s"]
            satellites.append({
                "id": s.element_id,
                "properties": dict(s)
            })
        return {"satellites": satellites}

# --- New Imagery & Detection Endpoints ---
@app.get("/api/imagery")
def get_imagery(
    bbox: Optional[str] = Query(None, description="min_lon,min_lat,max_lon,max_lat"),
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    sensor_type: Optional[str] = None
):
    """Query satellite passes from PostGIS catalog."""
    query = """
        SELECT id, name, file_path, sensor_type, acquisition_time, cloud_cover,
               ST_AsGeoJSON(footprint) as footprint_geojson, crs, created_at
        FROM satellite_passes
        WHERE 1=1
    """
    params = []
    
    if bbox:
        min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
        query += " AND ST_Intersects(footprint, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
        params.extend([min_lon, min_lat, max_lon, max_lat])
    
    if start_time:
        query += " AND acquisition_time >= %s"
        params.append(start_time)
    if end_time:
        query += " AND acquisition_time <= %s"
        params.append(end_time)
    if sensor_type:
        query += " AND sensor_type = %s"
        params.append(sensor_type)
    
    query += " ORDER BY acquisition_time DESC"
    
    with postgis_db.get_cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return {"imagery": [dict(r) for r in rows]}

@app.get("/api/imagery/{pass_id}/tiles")
def get_imagery_tiles(pass_id: int):
    """Return TiTiler tile URL for a given satellite pass."""
    with postgis_db.get_cursor() as cursor:
        cursor.execute("SELECT file_path FROM satellite_passes WHERE id = %s", (pass_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Satellite pass not found")
        
        titiler_url = os.getenv("TITILER_URL", "http://localhost:8081")
        tile_url = f"{titiler_url}/cog/tiles/{{z}}/{{x}}/{{y}}?url={row['file_path']}"
        return {"pass_id": pass_id, "tile_url": tile_url, "file_path": row["file_path"]}


@app.get("/api/basemap/countries")
def get_basemap_countries():
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT jsonb_build_object(
                'type', 'FeatureCollection',
                'features', coalesce(jsonb_agg(jsonb_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(geom)::jsonb,
                    'properties', jsonb_build_object('name', name, 'admin', admin, 'iso_a3', iso_a3)
                )), '[]'::jsonb)
            ) AS geojson
            FROM ne_countries
        """)
        row = cursor.fetchone()
        return row["geojson"] if row else {"type": "FeatureCollection", "features": []}

@app.get("/api/detections")
def get_detections(
    bbox: Optional[str] = Query(None, description="min_lon,min_lat,max_lon,max_lat"),
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    det_class: Optional[str] = None,
    limit: int = 1000
):
    """Query detections from PostGIS with spatial and temporal filters."""
    query = """
        SELECT d.id, d.class, d.confidence, d.pass_id, d.metadata, d.created_at,
               ST_AsGeoJSON(d.geom) as geom_geojson,
               ST_AsGeoJSON(d.centroid) as centroid_geojson,
               sp.name as pass_name, sp.acquisition_time, sp.file_path
        FROM detections d
        JOIN satellite_passes sp ON d.pass_id = sp.id
        WHERE 1=1
    """
    params = []
    
    if bbox:
        min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
        query += " AND ST_Intersects(d.geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
        params.extend([min_lon, min_lat, max_lon, max_lat])
    
    if start_time:
        query += " AND sp.acquisition_time >= %s"
        params.append(start_time)
    if end_time:
        query += " AND sp.acquisition_time <= %s"
        params.append(end_time)
    if det_class:
        query += " AND d.class = %s"
        params.append(det_class)
    
    query += " ORDER BY d.confidence DESC LIMIT %s"
    params.append(limit)
    
    with postgis_db.get_cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return {"detections": [dict(r) for r in rows]}

@app.get("/api/detections/geojson")
def get_detections_geojson(
    bbox: Optional[str] = Query(None, description="min_lon,min_lat,max_lon,max_lat"),
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    det_class: Optional[str] = None
):
    """Return detections as GeoJSON FeatureCollection."""
    with postgis_db.get_cursor() as cursor:
        if bbox:
            min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
            query = "SELECT get_detections_geojson(ST_MakeEnvelope(%s, %s, %s, %s, 4326), %s, %s, %s) as geojson"
            cursor.execute(query, (min_lon, min_lat, max_lon, max_lat, start_time, end_time, det_class))
        else:
            query = "SELECT get_detections_geojson(%s, %s, %s, %s) as geojson"
            cursor.execute(query, (None, start_time, end_time, det_class))
        row = cursor.fetchone()
        return row["geojson"] if row else {"type": "FeatureCollection", "features": []}

@app.post("/api/detections/resolve")
def resolve_detection(detection_id: int, distance_threshold_meters: float = 500.0):
    """
    Entity resolution: Check if a detection matches an existing Neo4j Target within threshold.
    If found, link them. If not, create a new Target.
    """
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT d.class, d.confidence, ST_X(d.centroid) as lon, ST_Y(d.centroid) as lat, d.metadata
            FROM detections d WHERE d.id = %s
        """, (detection_id,))
        det = cursor.fetchone()
        if not det:
            raise HTTPException(status_code=404, detail="Detection not found")
    
    with db.get_session() as session:
        # Search for existing targets near this location
        result = session.run("""
            MATCH (t:Target)
            WHERE t.latitude IS NOT NULL AND t.longitude IS NOT NULL
              AND point.distance(
                  point({latitude: t.latitude, longitude: t.longitude}),
                  point({latitude: $lat, longitude: $lon})
              ) < $threshold
            RETURN t
            ORDER BY point.distance(
                point({latitude: t.latitude, longitude: t.longitude}),
                point({latitude: $lat, longitude: $lon})
            ) ASC
            LIMIT 1
        """, {"lat": det["lat"], "lon": det["lon"], "threshold": distance_threshold_meters})
        
        existing = result.single()
        
        if existing:
            target = existing["t"]
            # Link detection to existing target
            link_result = session.run("""
                MATCH (t:Target) WHERE elementId(t) = $target_id
                MERGE (d:Detection {postgis_id: $det_id})
                ON CREATE SET d.class = $det_class,
                              d.confidence = $confidence,
                              d.latitude = $lat,
                              d.longitude = $lon,
                              d.created_at = datetime()
                MERGE (t)-[:DETECTED_AS]->(d)
                RETURN t, d
            """, {
                "target_id": target.element_id,
                "det_id": detection_id,
                "det_class": det["class"],
                "confidence": det["confidence"],
                "lat": det["lat"],
                "lon": det["lon"],
            })
            if not link_result.single():
                raise HTTPException(status_code=409, detail="Detection node could not be linked")
            return {
                "resolved": True,
                "action": "linked_to_existing",
                "target_id": target.element_id,
                "target_name": target.get("name", "Unknown")
            }
        else:
            # Create new target
            import uuid
            target_id = str(uuid.uuid4())
            target_name = f"Unknown {det['class']} #{target_id[:6]}"
            
            session.run("""
                MERGE (d:Detection {postgis_id: $det_id})
                ON CREATE SET d.class = $det_class,
                              d.confidence = $confidence,
                              d.latitude = $lat,
                              d.longitude = $lon,
                              d.created_at = datetime()
                CREATE (t:Target {
                    id: $id,
                    name: $name,
                    priority: 'High',
                    status: 'Active',
                    description: 'Automated detection via CV pipeline. Class: ' + $det_class,
                    latitude: $lat,
                    longitude: $lon,
                    confidence: $confidence,
                    detection_id: $det_id
                })
                MERGE (t)-[:DETECTED_AS]->(d)
            """, {
                "id": target_id,
                "name": target_name,
                "det_class": det["class"],
                "lat": det["lat"],
                "lon": det["lon"],
                "confidence": det["confidence"],
                "det_id": detection_id
            })
            return {
                "resolved": True,
                "action": "created_new",
                "target_id": target_id,
                "target_name": target_name
            }

@app.post("/api/ingest")
def trigger_ingest(req: IngestRequest):
    task = process_satellite_imagery.delay(req.image_url, req.sensor_type, req.acquisition_time)
    return {
        "success": True,
        "task_id": task.id,
        "status_url": f"/api/ingest/jobs/{task.id}",
        "message": "Satellite imagery pipeline initiated.",
    }


@app.post("/api/ingest/upload")
async def upload_imagery(
    file: UploadFile = File(...),
    sensor_type: str = Form("Optical"),
    acquisition_time: Optional[str] = Form(None),
    auto_process: bool = Form(True),
):
    allowed_suffixes = {".tif", ".tiff", ".jp2", ".j2k", ".nc", ".netcdf", ".png", ".jpg", ".jpeg"}
    filename = safe_filename(file.filename or "upload.tif")
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_suffixes:
        raise HTTPException(status_code=400, detail=f"Unsupported imagery format: {suffix or 'unknown'}")

    upload_dir = Path(os.getenv("IMAGERY_PATH", "/data/imagery")) / "incoming"
    upload_dir.mkdir(parents=True, exist_ok=True)
    local_path = upload_dir / f"{uuid.uuid4().hex}_{filename}"

    size = 0
    try:
        with local_path.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                handle.write(chunk)
    finally:
        await file.close()

    if size == 0:
        local_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    response = {
        "success": True,
        "file_path": str(local_path),
        "filename": filename,
        "bytes": size,
        "sensor_type": sensor_type,
        "auto_process": auto_process,
    }
    if auto_process:
        task = process_satellite_imagery.delay(str(local_path), sensor_type, acquisition_time)
        response.update({
            "task_id": task.id,
            "status_url": f"/api/ingest/jobs/{task.id}",
            "message": "Upload received and imagery pipeline queued.",
        })
    else:
        response["message"] = "Upload received."
    return response


@app.get("/api/ingest/jobs/{task_id}")
def get_ingest_job(task_id: str):
    from celery.result import AsyncResult

    result = AsyncResult(task_id, app=celery_app)
    payload = {
        "task_id": task_id,
        "state": result.state.lower(),
        "ready": result.ready(),
        "successful": result.successful() if result.ready() else False,
    }
    if result.ready():
        if result.successful():
            payload["result"] = result.result
        else:
            payload["error"] = str(result.result)
    return payload


@app.websocket("/ws")
async def websocket_events(websocket: WebSocket, topic: str = "detections"):
    await websocket.accept()
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    redis_client = None
    pubsub = None
    try:
        import redis.asyncio as redis

        redis_client = redis.from_url(redis_url, decode_responses=True)
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"events:{topic}")
        await websocket.send_json({"type": "connected", "topic": topic})

        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message:
                try:
                    data = json.loads(message["data"])
                except (TypeError, json.JSONDecodeError):
                    data = {"type": "message", "payload": message["data"]}
                await websocket.send_json(data)
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    finally:
        if pubsub is not None:
            await pubsub.close()
        if redis_client is not None:
            await redis_client.close()

@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        response = get_ai_response(req.message)
        return {"reply": response, "status": "ok"}
    except AIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)

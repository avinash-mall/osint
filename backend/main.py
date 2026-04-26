from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import uvicorn
from database import db, postgis_db
from ai import get_ai_response
import os

app = FastAPI(title="Gotham API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

class DetectionQuery(BaseModel):
    bbox: Optional[List[float]] = None  # [min_lon, min_lat, max_lon, max_lat]
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    det_class: Optional[str] = None

# --- Shutdown ---
@app.on_event("shutdown")
def shutdown_event():
    db.close()

# --- Graph Endpoints (Existing) ---
@app.get("/api/graph")
def get_graph():
    with db.get_session() as session:
        result = session.run("""
            MATCH (n)-[r]->(m)
            WHERE NOT n:Observation AND NOT m:Observation
            RETURN n, r, m
            LIMIT 1000
        """)
        nodes = {}
        links = []
        for record in result:
            n = record["n"]
            m = record["m"]
            r = record["r"]
            
            nodes[n.element_id] = {"id": n.element_id, "label": list(n.labels)[0], "properties": dict(n)}
            nodes[m.element_id] = {"id": m.element_id, "label": list(m.labels)[0], "properties": dict(m)}
            links.append({"source": n.element_id, "target": m.element_id, "type": r.type})
            
        return {"nodes": list(nodes.values()), "links": links}

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
            ORDER BY t.priority DESC, t.name ASC
        """)
        targets = []
        for record in result:
            t = record["t"]
            targets.append({
                "id": t.element_id,
                "properties": dict(t)
            })
        return {"targets": targets}

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
        try:
            min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(","))
            query += " AND ST_Intersects(footprint, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
            params.extend([min_lon, min_lat, max_lon, max_lat])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid bbox format. Use min_lon,min_lat,max_lon,max_lat")
    
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
        try:
            min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(","))
            query += " AND ST_Intersects(d.geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
            params.extend([min_lon, min_lat, max_lon, max_lat])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid bbox format")
    
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
            try:
                min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(","))
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid bbox format")
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
            session.run("""
                MATCH (t:Target) WHERE elementId(t) = $target_id
                MATCH (d:Detection {postgis_id: $det_id})
                MERGE (t)-[:DETECTED_AS]->(d)
                RETURN t
            """, {"target_id": target.element_id, "det_id": detection_id})
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
    from worker import process_satellite_imagery
    task = process_satellite_imagery.delay(req.image_url, req.sensor_type, req.acquisition_time)
    return {"success": True, "task_id": task.id, "message": "Satellite imagery pipeline initiated."}

@app.post("/api/chat")
def chat(req: ChatRequest):
    response = get_ai_response(req.message)
    return {"reply": response}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)

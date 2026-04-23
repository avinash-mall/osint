from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from database import db
from ai import get_ai_response

app = FastAPI(title="Gotham API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str

@app.on_event("shutdown")
def shutdown_event():
    db.close()

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
        # Get bases and launch points
        result_static = session.run("""
            MATCH (n)
            WHERE (n:Base OR n:LaunchPoint) AND n.latitude IS NOT NULL
            RETURN n
        """)
        static_features = [{"id": r["n"].element_id, "label": list(r["n"].labels)[0], "properties": dict(r["n"])} for r in result_static]

        # Get latest observations for tracks
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

class TargetStatusUpdate(BaseModel):
    status: str

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
        # Note: element_id is passed, we use id() in cypher or elementId() depending on Neo4j version.
        # Assuming elementId() works in neo4j 5+. If not, we can use ID(t) = toInteger($id).
        # Let's use elementId()
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

class IngestRequest(BaseModel):
    image_url: str

@app.post("/api/ingest")
def trigger_ingest(req: IngestRequest):
    # Import locally to avoid circular dependency issues if any
    from worker import process_satellite_imagery
    # Dispatch to Celery worker
    task = process_satellite_imagery.delay(req.image_url)
    return {"success": True, "task_id": task.id, "message": "Satellite imagery pipeline initiated."}

@app.post("/api/chat")
def chat(req: ChatRequest):
    response = get_ai_response(req.message)
    return {"reply": response}

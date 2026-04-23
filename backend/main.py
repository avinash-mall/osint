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
            RETURN n, r, m
            LIMIT 100
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

@app.get("/api/geospatial")
def get_geospatial():
    with db.get_session() as session:
        result = session.run("""
            MATCH (l:Location)
            WHERE l.latitude IS NOT NULL AND l.longitude IS NOT NULL
            RETURN l
        """)
        locations = []
        for record in result:
            l = record["l"]
            locations.append({"id": l.element_id, "properties": dict(l)})
        return {"locations": locations}

@app.post("/api/chat")
def chat(req: ChatRequest):
    response = get_ai_response(req.message)
    return {"reply": response}

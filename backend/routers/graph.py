"""Neo4j graph routes: /api/graph, /api/graph/neighborhood, /api/geotime/features."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from database import db, postgis_db
from schemas import GraphActionRequest

router = APIRouter()


@router.get("/api/graph")
def get_graph(include_candidates: bool = Query(False, description="Include pending candidate links as review-only graph edges")):
    with db.get_session() as session:
        result = session.run("""
            MATCH (n)
            OPTIONAL MATCH (n)-[r]->(m)
            WHERE r IS NULL OR $include_candidates OR NOT type(r) STARTS WITH 'CANDIDATE_'
            RETURN n, r, m
            LIMIT 1500
        """, {"include_candidates": include_candidates})
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
                links.append({
                    "source": n.element_id,
                    "target": m.element_id,
                    "type": r.type,
                    # `predicate` is the semantic edge label the graph UI
                    # renders mid-edge and filters on (UX-AUDIT F22). It is
                    # the Neo4j relationship type — the canonical predicate.
                    "predicate": r.type,
                    "candidate": str(r.type).startswith("CANDIDATE_"),
                    "properties": dict(r),
                })

        if include_candidates:
            with postgis_db.get_cursor() as cursor:
                cursor.execute("""
                    SELECT id, detection_id, target_id, target_name, score, reason, status
                    FROM detection_target_candidates
                    WHERE status = 'pending'
                    ORDER BY score DESC
                    LIMIT 300
                """)
                candidates = [dict(row) for row in cursor.fetchall()]
            if candidates:
                candidate_result = session.run("""
                    UNWIND $candidates AS c
                    MATCH (t:Target)
                    WHERE elementId(t) = c.target_id OR t.id = c.target_id
                    MATCH (d:Detection {postgis_id: c.detection_id})
                    RETURN t, d, c
                """, {"candidates": candidates})
                for record in candidate_result:
                    t = record["t"]
                    d = record["d"]
                    c = record["c"]
                    nodes[t.element_id] = {"id": t.element_id, "label": list(t.labels)[0], "properties": dict(t)}
                    nodes[d.element_id] = {"id": d.element_id, "label": list(d.labels)[0], "properties": dict(d)}
                    links.append({
                        "source": t.element_id,
                        "target": d.element_id,
                        "type": "CANDIDATE_DETECTED_AS",
                        "predicate": "CANDIDATE_DETECTED_AS",
                        "candidate": True,
                        "candidate_id": c["id"],
                        "score": c["score"],
                        "status": c["status"],
                    })

        return {"nodes": list(nodes.values()), "links": links}


@router.post("/api/graph/neighborhood")
def get_graph_neighborhood(req: GraphActionRequest):
    with db.get_session() as session:
        result = session.run("""
            MATCH (n)
            WHERE elementId(n) = $id
            OPTIONAL MATCH (n)-[rel]-(m)
            WITH n, collect(DISTINCT m) AS neighbors, collect(DISTINCT rel) AS rels
            RETURN n, neighbors,
                   [rel IN rels WHERE rel IS NOT NULL |
                    {source: elementId(startNode(rel)), target: elementId(endNode(rel)),
                     type: type(rel), predicate: type(rel)}] AS links
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


@router.get("/api/geotime/features")
def get_geotime_features():
    with db.get_session() as session:
        schema_labels = set(session.run("""
            CALL db.labels() YIELD label
            RETURN collect(label) AS labels
        """).single()["labels"] or [])

        static_features = []
        static_labels = sorted(schema_labels.intersection({"Base", "LaunchPoint"}))
        if static_labels:
            result_static = session.run("""
                MATCH (n)
                WHERE any(label IN labels(n) WHERE label IN $static_labels)
                  AND n.latitude IS NOT NULL
                RETURN n
            """, {"static_labels": static_labels})
            static_features = [{"id": r["n"].element_id, "label": list(r["n"].labels)[0], "properties": dict(r["n"])} for r in result_static]

        tracks = []
        if not {"Asset", "Observation"}.issubset(schema_labels):
            return {"static": static_features, "tracks": tracks}

        result_static = session.run("""
            CALL db.relationshipTypes() YIELD relationshipType
            RETURN collect(relationshipType) AS relationship_types
        """)
        relationship_types = set(result_static.single()["relationship_types"] or [])
        if "OBSERVED_AT" not in relationship_types:
            return {"static": static_features, "tracks": tracks}

        result_tracks = session.run("""
            MATCH (a)-[rel]->(o)
            WHERE 'Asset' IN labels(a)
              AND type(rel) = 'OBSERVED_AT'
              AND 'Observation' IN labels(o)
            WITH a, o ORDER BY o.timestamp DESC
            WITH a, collect(o) as obs
            RETURN a, obs[0] as latest, obs
        """)
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

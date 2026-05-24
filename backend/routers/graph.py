"""Neo4j graph routes.

Existing (back-compat):
- ``GET  /api/graph``                — 1500-node global slice
- ``POST /api/graph/neighborhood``   — 1-hop neighborhood of a seed node
- ``GET  /api/geotime/features``     — static features + asset tracks for the map

Phase 1 (Link Graph redesign):
- ``GET  /api/graph/investigation``                    — bounded slice + 2-hop
- ``POST /api/graph/path``                             — shortest path
- ``GET  /api/graph/site-composition/{base_id}``       — workflow 3 rollup
- ``POST /api/graph/candidate-edges/{candidate_id}/promote`` — graph-side approve
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from database import db, postgis_db
from graph_writes import (
    delete_candidate_detected_as,
    merge_contradicted_by,
    promote_candidate_to_detected_as,
)
from schemas import (
    GraphActionRequest,
    GraphContradictRequest,
    GraphPathRequest,
    GraphPromoteRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


# Node labels treated as "operational" (always rendered in Investigation).
_OPERATIONAL_LABELS = {
    "Target", "Asset", "Base", "LaunchPoint", "Facility", "Unit",
    "Vessel", "Aircraft", "Vehicle",
}
# Node labels treated as "evidence" (rendered inside the seed neighborhood).
_EVIDENCE_LABELS = {
    "Detection", "Observation", "SatellitePass",
    "FMVClip", "FMVDetection", "Document", "Report", "FeedEvent",
}


def _serialise_node(n) -> dict[str, Any]:
    return {
        "id": n.element_id,
        "label": list(n.labels)[0] if n.labels else "Node",
        "labels": list(n.labels),
        "properties": dict(n),
    }


def _serialise_relationship(rel, *, source_id: str | None = None, target_id: str | None = None) -> dict[str, Any]:
    rel_type = rel.type
    return {
        "source": source_id if source_id is not None else rel.start_node.element_id,
        "target": target_id if target_id is not None else rel.end_node.element_id,
        "type": rel_type,
        "predicate": rel_type,
        "candidate": str(rel_type).startswith("CANDIDATE_"),
        "properties": dict(rel),
    }


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        # FastAPI passes strings as-is; accept both `Z` and `+00:00`.
        clean = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid timestamp: {ts}") from exc


# ---------------------------------------------------------------------------
# back-compat endpoints
# ---------------------------------------------------------------------------


@router.get("/api/graph")
def get_graph(include_candidates: bool = Query(False, description="Include pending CANDIDATE_* edges")):
    """Global slice up to 1500 nodes. Used by the legacy Link Graph workspace.

    Candidate edges are now persisted (see
    [decisions/why-candidate-edges-persisted.md](../../docs/decisions/why-candidate-edges-persisted.md)),
    so the prior in-memory synthesis pass has been removed — `include_candidates`
    now just toggles whether the Cypher WHERE-clause filters them out.
    """
    with db.get_session() as session:
        result = session.run(
            """
            MATCH (n)
            OPTIONAL MATCH (n)-[r]->(m)
            WHERE r IS NULL OR $include_candidates OR NOT type(r) STARTS WITH 'CANDIDATE_'
            RETURN n, r, m
            LIMIT 1500
            """,
            {"include_candidates": include_candidates},
        )
        nodes: dict[str, dict[str, Any]] = {}
        links: list[dict[str, Any]] = []
        for record in result:
            n = record["n"]
            m = record["m"]
            r = record["r"]
            nodes[n.element_id] = _serialise_node(n)
            if m is not None:
                nodes[m.element_id] = _serialise_node(m)
            if r is not None and m is not None:
                links.append(_serialise_relationship(r, source_id=n.element_id, target_id=m.element_id))
        return {"nodes": list(nodes.values()), "links": links}


@router.post("/api/graph/neighborhood")
def get_graph_neighborhood(req: GraphActionRequest):
    with db.get_session() as session:
        result = session.run(
            """
            MATCH (n)
            WHERE elementId(n) = $id
            OPTIONAL MATCH (n)-[rel]-(m)
            WITH n, collect(DISTINCT m) AS neighbors, collect(DISTINCT rel) AS rels
            RETURN n, neighbors,
                   [rel IN rels WHERE rel IS NOT NULL |
                    {source: elementId(startNode(rel)), target: elementId(endNode(rel)),
                     type: type(rel), predicate: type(rel), properties: properties(rel)}] AS links
            """,
            {"id": req.node_id},
        )
        record = result.single()
        if not record:
            raise HTTPException(status_code=404, detail="Node not found")

        nodes = {record["n"].element_id: _serialise_node(record["n"])}
        for node in record["neighbors"]:
            if node is not None:
                nodes[node.element_id] = _serialise_node(node)
        return {"nodes": list(nodes.values()), "links": record["links"]}


@router.get("/api/geotime/features")
def get_geotime_features():
    with db.get_session() as session:
        schema_labels = set(
            session.run("CALL db.labels() YIELD label RETURN collect(label) AS labels").single()["labels"] or []
        )

        static_features = []
        static_labels = sorted(schema_labels.intersection({"Base", "LaunchPoint"}))
        if static_labels:
            result_static = session.run(
                """
                MATCH (n)
                WHERE any(label IN labels(n) WHERE label IN $static_labels)
                  AND n.latitude IS NOT NULL
                RETURN n
                """,
                {"static_labels": static_labels},
            )
            static_features = [
                {"id": r["n"].element_id, "label": list(r["n"].labels)[0], "properties": dict(r["n"])}
                for r in result_static
            ]

        tracks = []
        if not {"Asset", "Observation"}.issubset(schema_labels):
            return {"static": static_features, "tracks": tracks}

        relationship_types = set(
            session.run("CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) AS relationship_types")
            .single()["relationship_types"]
            or []
        )
        if "OBSERVED_AT" not in relationship_types:
            return {"static": static_features, "tracks": tracks}

        result_tracks = session.run(
            """
            MATCH (a)-[rel]->(o)
            WHERE 'Asset' IN labels(a)
              AND type(rel) = 'OBSERVED_AT'
              AND 'Observation' IN labels(o)
            WITH a, o ORDER BY o.timestamp DESC
            WITH a, collect(o) as obs
            RETURN a, obs[0] as latest, obs
            """
        )
        for r in result_tracks:
            asset = r["a"]
            latest = r["latest"]
            history = [{"lat": ob["latitude"], "lng": ob["longitude"], "time": ob["timestamp"]} for ob in r["obs"]]
            tracks.append(
                {
                    "id": asset.element_id,
                    "label": list(asset.labels)[0],
                    "asset_id": asset["id"],
                    "properties": dict(asset),
                    "latest": dict(latest),
                    "history": history,
                }
            )
        return {"static": static_features, "tracks": tracks}


# ---------------------------------------------------------------------------
# Phase 1 — redesigned routes
# ---------------------------------------------------------------------------


@router.get("/api/graph/investigation")
def get_graph_investigation(
    class_lens: list[str] = Query(default_factory=list, description="Restrict to these node labels"),
    time_start: str | None = Query(None, description="ISO 8601 — lower bound on Detection/Observation created_at"),
    time_end: str | None = Query(None, description="ISO 8601 — upper bound"),
    aoi_id: int | None = Query(None, description="Scope to nodes mirrored from this AOI"),
    seed_node_id: str | None = Query(None, description="When set, returns 2-hop neighborhood of this node"),
    limit: int = Query(150, ge=1, le=500, description="Cap on total nodes returned"),
):
    """Default Investigation panel feed. Operational nodes + their 1-hop
    neighborhood, capped, optionally scoped by time / AOI / class.

    Operational-only labels never include `Detection`/`SatellitePass` directly
    unless they are pulled in as 1-hop neighbors of an operational node.
    """
    t_start = _parse_iso(time_start)
    t_end = _parse_iso(time_end)
    valid_class_lens = [c for c in class_lens if isinstance(c, str) and c]

    with db.get_session() as session:
        if seed_node_id:
            # 2-hop neighborhood of a seed node, with the same time / class filters.
            result = session.run(
                """
                MATCH (seed)
                WHERE elementId(seed) = $seed
                CALL {
                    WITH seed
                    MATCH (seed)-[*1..2]-(n)
                    RETURN DISTINCT n LIMIT $limit
                }
                WITH seed, collect(DISTINCT n) AS neighbors
                WITH seed, [seed] + neighbors AS all_nodes
                UNWIND all_nodes AS node
                OPTIONAL MATCH (node)-[r]-(other)
                WHERE other IN all_nodes
                RETURN collect(DISTINCT node) AS nodes,
                       collect(DISTINCT r) AS rels
                """,
                {"seed": seed_node_id, "limit": limit},
            )
        else:
            # Global slice: operational nodes first, then 1-hop expansion.
            result = session.run(
                """
                CALL {
                    MATCH (op)
                    WHERE any(l IN labels(op) WHERE l IN $operational_labels)
                      AND (size($class_lens) = 0 OR any(l IN labels(op) WHERE l IN $class_lens))
                    RETURN op LIMIT $op_limit
                }
                WITH collect(DISTINCT op) AS operationals
                UNWIND operationals AS op
                OPTIONAL MATCH (op)-[r]-(neighbor)
                WHERE (size($class_lens) = 0
                       OR any(l IN labels(neighbor) WHERE l IN $class_lens))
                  AND ($t_start IS NULL OR neighbor.created_at IS NULL OR neighbor.created_at >= datetime($t_start))
                  AND ($t_end IS NULL OR neighbor.created_at IS NULL OR neighbor.created_at <= datetime($t_end))
                WITH operationals, collect(DISTINCT neighbor) AS neighbors, collect(DISTINCT r) AS rels
                WITH operationals + neighbors AS all_nodes, rels
                WITH all_nodes[..$limit] AS nodes, rels
                RETURN nodes, rels
                """,
                {
                    "operational_labels": sorted(_OPERATIONAL_LABELS),
                    "class_lens": valid_class_lens,
                    "op_limit": min(80, limit),
                    "limit": limit,
                    "t_start": t_start.isoformat() if t_start else None,
                    "t_end": t_end.isoformat() if t_end else None,
                },
            )
        record = result.single()
        if record is None:
            return {"nodes": [], "links": []}

        # `nodes` is a list of node objects; `rels` may contain Nones for
        # the OPTIONAL MATCH leg.
        raw_nodes = [n for n in record["nodes"] if n is not None]
        raw_rels = [r for r in record["rels"] if r is not None]
        node_index = {n.element_id: _serialise_node(n) for n in raw_nodes}

        # AOI scope: when provided, restrict to nodes related to the AOI
        # (Base/LaunchPoint/Facility mirrored from this AOI, plus their
        # neighborhood). The MERGE writes ``aoi_postgis_id`` on the mirror.
        if aoi_id is not None:
            keep_ids: set[str] = {
                nid for nid, payload in node_index.items()
                if payload["properties"].get("aoi_postgis_id") == aoi_id
            }
            # Expand by one hop so the neighborhood comes along.
            expanded = set(keep_ids)
            for r in raw_rels:
                s = r.start_node.element_id
                t = r.end_node.element_id
                if s in keep_ids or t in keep_ids:
                    expanded.update({s, t})
            node_index = {nid: payload for nid, payload in node_index.items() if nid in expanded}

        links = []
        for r in raw_rels:
            s = r.start_node.element_id
            t = r.end_node.element_id
            if s in node_index and t in node_index:
                links.append(_serialise_relationship(r, source_id=s, target_id=t))

        return {"nodes": list(node_index.values()), "links": links, "limit": limit}


@router.post("/api/graph/path")
def get_graph_path(req: GraphPathRequest):
    """Shortest path between two nodes — `allShortestPaths` capped by max_depth.

    Returns a list of paths (each is `{nodes, links}`) ordered shortest-first.
    """
    with db.get_session() as session:
        result = session.run(
            f"""
            MATCH (a), (b)
            WHERE elementId(a) = $from_id AND elementId(b) = $to_id
            MATCH p = allShortestPaths((a)-[*..{req.max_depth}]-(b))
            RETURN p LIMIT 10
            """,
            {"from_id": req.from_id, "to_id": req.to_id},
        )
        paths_out: list[dict[str, Any]] = []
        for record in result:
            path = record["p"]
            nodes = [_serialise_node(n) for n in path.nodes]
            links = []
            for rel in path.relationships:
                links.append(
                    _serialise_relationship(
                        rel,
                        source_id=rel.start_node.element_id,
                        target_id=rel.end_node.element_id,
                    )
                )
            paths_out.append({"nodes": nodes, "links": links, "length": len(path.relationships)})
    return {"paths": paths_out, "max_depth": req.max_depth, "count": len(paths_out)}


@router.get("/api/graph/site-composition/{base_id}")
def get_site_composition(
    base_id: str,
    radius_m: float = Query(5000.0, ge=10.0, le=50000.0),
    recent_days: int = Query(30, ge=1, le=365),
):
    """Workflow 3: "What's at this site?"

    Returns grouped buckets for a Base/LaunchPoint/Facility node:
    - ``recent_detections`` — PostGIS detections within radius_m of the AOI
      centroid in the last ``recent_days`` days, grouped by class.
    - ``vessels``, ``vehicles``, ``aircraft`` — Neo4j Assets with `:OBSERVED_AT`
      pointing to this site (empty in Phase 1 until projectors run).
    - ``fmv_clips``, ``reports`` — empty placeholders in Phase 1 (Phase 2
      projectors populate them).

    The PostGIS join is a live ST_DWithin until Phase 4's ``worker.tick_near_builder``
    populates ``:NEAR`` edges; this avoids gating Phase 1 on the beat task.
    """
    with db.get_session() as session:
        record = session.run(
            """
            MATCH (b)
            WHERE elementId(b) = $base_id
              AND any(l IN labels(b) WHERE l IN ['Base', 'LaunchPoint', 'Facility'])
            RETURN b, labels(b) AS labels, properties(b) AS props
            """,
            {"base_id": base_id},
        ).single()
        if record is None:
            raise HTTPException(status_code=404, detail="Site (Base/LaunchPoint/Facility) not found")
        props = dict(record["props"]) if record["props"] else {}
        aoi_postgis_id = props.get("aoi_postgis_id")

    # Phase 4.C: prefer precomputed :NEAR edges when worker.tick_near_builder
    # has materialised any for this site; fall back to live PostGIS ST_DWithin
    # otherwise so the endpoint works on day one (before the beat task runs).
    recent_detections: list[dict[str, Any]] = []
    source = "live_st_dwithin"
    used_near = False
    try:
        with db.get_session() as near_session:
            near_record = near_session.run(
                """
                MATCH (s)<-[r:NEAR]-(d:Detection)
                WHERE elementId(s) = $base_id
                RETURN count(r) AS edges
                """,
                {"base_id": base_id},
            ).single()
            near_edge_count = int(near_record["edges"]) if near_record else 0
    except Exception:
        near_edge_count = 0

    if near_edge_count > 0:
        used_near = True
        source = "neo4j_near"
        # Group by class from the NEAR-traversal-attached detections.
        try:
            with db.get_session() as near_session:
                rows = near_session.run(
                    """
                    MATCH (s)<-[:NEAR]-(d:Detection)
                    WHERE elementId(s) = $base_id
                      AND d.created_at IS NOT NULL
                      AND d.created_at >= datetime() - duration({days: $recent_days})
                    RETURN d.class AS class, count(d) AS count, max(d.created_at) AS last_seen
                    ORDER BY count DESC
                    """,
                    {"base_id": base_id, "recent_days": recent_days},
                )
                for r in rows:
                    recent_detections.append({
                        "class": r["class"],
                        "count": int(r["count"]),
                        "last_seen": str(r["last_seen"]) if r["last_seen"] else None,
                    })
        except Exception as exc:  # noqa: BLE001
            logger.warning("site-composition: NEAR traversal failed for %s: %s", base_id, exc)

    if not used_near and aoi_postgis_id is not None:
        try:
            with postgis_db.get_cursor() as cursor:
                cursor.execute(
                    """
                    WITH aoi AS (
                        SELECT geom, ST_Centroid(geom) AS centroid FROM aois WHERE id = %s
                    )
                    SELECT d.class, COUNT(*)::int AS count, MAX(d.created_at) AS last_seen
                    FROM detections d, aoi
                    WHERE d.deleted_at IS NULL
                      AND d.created_at >= NOW() - (%s || ' days')::interval
                      AND ST_DWithin(d.centroid::geography, aoi.centroid::geography, %s)
                    GROUP BY d.class
                    ORDER BY count DESC
                    """,
                    (aoi_postgis_id, str(recent_days), radius_m),
                )
                recent_detections = [dict(r) for r in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("site-composition: PostGIS query failed for aoi=%s: %s", aoi_postgis_id, exc)

    # Neo4j-side groupings (mostly empty until projectors run in later phases).
    with db.get_session() as session:
        observed = session.run(
            """
            MATCH (b)<-[:OBSERVED_AT|:NEAR|:OPERATES_FROM]-(a)
            WHERE elementId(b) = $base_id
            RETURN labels(a) AS labels, properties(a) AS props, elementId(a) AS id
            LIMIT 200
            """,
            {"base_id": base_id},
        )
        vessels, vehicles, aircraft, other_assets = [], [], [], []
        for r in observed:
            labels = set(r["labels"] or [])
            payload = {"id": r["id"], "properties": dict(r["props"] or {}), "labels": list(labels)}
            if "Vessel" in labels:
                vessels.append(payload)
            elif "Vehicle" in labels:
                vehicles.append(payload)
            elif "Aircraft" in labels:
                aircraft.append(payload)
            elif "Asset" in labels:
                other_assets.append(payload)

    return {
        "base_id": base_id,
        "aoi_postgis_id": aoi_postgis_id,
        "labels": record["labels"],
        "properties": props,
        "radius_m": radius_m,
        "recent_days": recent_days,
        "recent_detections": recent_detections,
        "recent_detections_source": source,
        "vessels": vessels,
        "vehicles": vehicles,
        "aircraft": aircraft,
        "other_assets": other_assets,
        "fmv_clips": [],
        "reports": [],
    }


@router.get("/api/graph/ontology")
def get_graph_ontology(
    include_unknown: bool = Query(True, description="Include :UnknownLabel orbit nodes"),
    since: str | None = Query(None, description="ISO-8601 lower bound on UnknownLabel.last_seen"),
    supports_per_unknown: int = Query(5, ge=0, le=25),
    include_cooccurrence: bool = Query(False, description="Add per-OntologyObject co-occurrence count map"),
    cooccurrence_top_k: int = Query(5, ge=1, le=20, description="Top-K adjacent classes per object"),
):
    """Ontology-mode feed: branches + objects + (optionally) UnknownLabel orbits.

    Workflow 4 — analyst triages "is this an ontology problem or an intelligence
    problem?" The response carries:
    - The OntologyBranch tree + OntologyObject children (via HAS_OBJECT edges).
    - UnknownLabel nodes (when ``include_unknown=True``), each with a
      SUGGESTED_BRANCH edge if one was assigned and LABEL_OF edges out to
      recent supporting Detections (capped at ``supports_per_unknown``).
    """
    t_since = _parse_iso(since)

    with db.get_session() as session:
        ontology_record = session.run(
            """
            CALL {
                MATCH (b:OntologyBranch)
                RETURN collect(DISTINCT b) AS branches
            }
            CALL {
                MATCH (o:OntologyObject)
                RETURN collect(DISTINCT o) AS objects
            }
            CALL {
                MATCH (b:OntologyBranch)-[r:HAS_OBJECT|HAS_CHILD]->(other)
                RETURN collect(DISTINCT r) AS ontology_rels
            }
            RETURN branches, objects, ontology_rels
            """
        ).single()

        nodes: dict[str, dict[str, Any]] = {}
        links: list[dict[str, Any]] = []
        if ontology_record:
            for n in (ontology_record["branches"] or []) + (ontology_record["objects"] or []):
                if n is not None:
                    nodes[n.element_id] = _serialise_node(n)
            for r in ontology_record["ontology_rels"] or []:
                if r is not None:
                    s, t = r.start_node.element_id, r.end_node.element_id
                    if s in nodes and t in nodes:
                        links.append(_serialise_relationship(r, source_id=s, target_id=t))

        if include_unknown:
            unknown_record = session.run(
                """
                MATCH (u:UnknownLabel)
                WHERE $since IS NULL OR u.last_seen IS NULL OR u.last_seen >= $since
                WITH u
                OPTIONAL MATCH (u)-[r1:SUGGESTED_BRANCH]->(b:OntologyBranch)
                OPTIONAL MATCH (d:Detection)-[r2:LABEL_OF]->(u)
                WITH u, collect(DISTINCT b) AS branches,
                     collect(DISTINCT r1) AS suggested,
                     collect(DISTINCT d)[..$limit] AS support_dets,
                     collect(DISTINCT r2)[..$limit] AS support_rels
                RETURN collect({u: u, branches: branches, suggested: suggested,
                                supports: support_dets, support_rels: support_rels}) AS rows
                """,
                {"since": t_since.isoformat() if t_since else None, "limit": supports_per_unknown},
            ).single()
            for row in (unknown_record["rows"] or []) if unknown_record else []:
                u = row["u"]
                if u is None:
                    continue
                nodes[u.element_id] = _serialise_node(u)
                for b in row["branches"] or []:
                    if b is not None and b.element_id not in nodes:
                        nodes[b.element_id] = _serialise_node(b)
                for r in row["suggested"] or []:
                    if r is None:
                        continue
                    s, t = r.start_node.element_id, r.end_node.element_id
                    if s in nodes and t in nodes:
                        links.append(_serialise_relationship(r, source_id=s, target_id=t))
                for d in row["supports"] or []:
                    if d is not None and d.element_id not in nodes:
                        nodes[d.element_id] = _serialise_node(d)
                for r in row["support_rels"] or []:
                    if r is None:
                        continue
                    s, t = r.start_node.element_id, r.end_node.element_id
                    if s in nodes and t in nodes:
                        links.append(_serialise_relationship(r, source_id=s, target_id=t))

    cooccurrence: dict[str, dict[str, int]] = {}
    if include_cooccurrence:
        # Phase 5.C: count how often each OntologyObject co-classifies the
        # same Detection with another OntologyObject. Drives the per-object
        # chips in OntologyOrbit.
        try:
            with db.get_session() as session:
                rows = session.run(
                    """
                    MATCH (o:OntologyObject)<-[:LABEL_OF]-(d:Detection)-[:LABEL_OF]->(other:OntologyObject)
                    WHERE o.id <> other.id
                    WITH o.id AS object_id, other.label AS other_label, count(d) AS cnt
                    ORDER BY object_id, cnt DESC
                    RETURN object_id, other_label, cnt
                    """
                )
                tally: dict[str, list[tuple[str, int]]] = {}
                for r in rows:
                    oid = r["object_id"]
                    if not oid:
                        continue
                    tally.setdefault(oid, []).append((str(r["other_label"]), int(r["cnt"])))
                for oid, pairs in tally.items():
                    top = pairs[:cooccurrence_top_k]
                    cooccurrence[oid] = {label: cnt for label, cnt in top}
        except Exception as exc:  # noqa: BLE001
            logger.warning("ontology: cooccurrence computation failed: %s", exc)
            cooccurrence = {}

    return {
        "nodes": list(nodes.values()),
        "links": links,
        "include_unknown": include_unknown,
        "supports_per_unknown": supports_per_unknown,
        "cooccurrence": cooccurrence if include_cooccurrence else None,
    }


@router.get("/api/graph/evidence/{node_id}")
def get_graph_evidence(node_id: str, hops: int = Query(2, ge=1, le=3)):
    """Workflow 5: chain of evidence for one Target/Detection/Asset.

    Returns the Neo4j ``hops``-hop neighborhood of the seed node plus a
    parallel PostGIS pull of related rows (raw image paths, model versions,
    transcripts, fmv frames, feed payloads) keyed by ``postgis_id`` carried on
    the mirror nodes.

    Response shape: ``{focus, nodes, links, evidence_records: {detections, satellite_passes, fmv_clips, documents, reports, feed_events, observations, transcripts}}``.

    Evidence types whose Neo4j projectors haven't shipped yet (FMVClip,
    Document, Report, FeedEvent, Observation arrive in Phase 2) simply
    return as empty arrays. The endpoint is forward-compatible: as projectors
    land, more buckets light up without API changes.
    """
    with db.get_session() as session:
        # Pull the seed + 2-hop neighborhood.
        result = session.run(
            f"""
            MATCH (seed)
            WHERE elementId(seed) = $seed
            CALL {{
                WITH seed
                MATCH (seed)-[*1..{hops}]-(n)
                RETURN DISTINCT n LIMIT 200
            }}
            WITH seed, collect(DISTINCT n) AS neighbors
            WITH [seed] + neighbors AS all_nodes
            UNWIND all_nodes AS node
            OPTIONAL MATCH (node)-[r]-(other)
            WHERE other IN all_nodes
            RETURN collect(DISTINCT node) AS nodes,
                   collect(DISTINCT r) AS rels
            """,
            {"seed": node_id},
        ).single()
    if result is None:
        raise HTTPException(status_code=404, detail="Node not found")

    raw_nodes = [n for n in (result["nodes"] or []) if n is not None]
    raw_rels = [r for r in (result["rels"] or []) if r is not None]
    if not raw_nodes:
        raise HTTPException(status_code=404, detail="Node not found")

    nodes = [_serialise_node(n) for n in raw_nodes]
    links = [
        _serialise_relationship(r, source_id=r.start_node.element_id, target_id=r.end_node.element_id)
        for r in raw_rels
    ]
    focus = nodes[0]

    # Group postgis_ids by label so each PostGIS table is queried once.
    by_label: dict[str, list[int]] = {}
    for node in nodes:
        labels = node.get("labels") or []
        pid = node.get("properties", {}).get("postgis_id")
        if not isinstance(pid, int):
            continue
        for label in labels:
            by_label.setdefault(label, []).append(pid)

    evidence: dict[str, list[dict[str, Any]]] = {
        "detections": [],
        "satellite_passes": [],
        "fmv_clips": [],
        "fmv_frames": [],
        "documents": [],
        "reports": [],
        "feed_events": [],
        "observations": [],
        "transcripts": [],
    }

    def _safe_fetch(sql: str, params: tuple) -> list[dict[str, Any]]:
        try:
            with postgis_db.get_cursor() as cursor:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("evidence: PostGIS fetch failed (%s): %s", sql[:60], exc)
            return []

    detection_ids = by_label.get("Detection", [])
    if detection_ids:
        evidence["detections"] = _safe_fetch(
            """
            SELECT d.id, d.class, d.confidence, d.created_at, d.metadata,
                   d.pass_id, sp.name AS pass_name, sp.sensor_type,
                   sp.acquisition_time,
                   ST_X(d.centroid) AS lon, ST_Y(d.centroid) AS lat
            FROM detections d
            LEFT JOIN satellite_passes sp ON sp.id = d.pass_id
            WHERE d.id = ANY(%s) AND d.deleted_at IS NULL
            """,
            (detection_ids,),
        )

    pass_ids = by_label.get("SatellitePass", [])
    if pass_ids:
        evidence["satellite_passes"] = _safe_fetch(
            """
            SELECT id, name, file_path, sensor_type, acquisition_time, cloud_cover, metadata
            FROM satellite_passes
            WHERE id = ANY(%s)
            """,
            (pass_ids,),
        )

    fmv_clip_ids = by_label.get("FMVClip", [])
    if fmv_clip_ids:
        evidence["fmv_clips"] = _safe_fetch(
            """
            SELECT id, name, file_path, hls_path, duration_seconds, fps, width, height, status, metadata
            FROM fmv_clips WHERE id = ANY(%s)
            """,
            (fmv_clip_ids,),
        )
        # Pull a sample of frames per clip so the analyst can preview without
        # a follow-up fetch. Cap at 8 frames per clip.
        evidence["fmv_frames"] = _safe_fetch(
            """
            SELECT clip_id, frame_index, timestamp_seconds, telemetry
            FROM fmv_frames
            WHERE clip_id = ANY(%s)
            ORDER BY clip_id, frame_index
            LIMIT 8 * (1 + array_length(%s, 1))
            """,
            (fmv_clip_ids, fmv_clip_ids),
        )

    document_ids = by_label.get("Document", [])
    if document_ids:
        evidence["documents"] = _safe_fetch(
            """
            SELECT id, upload_id, domain, title, file_path, source_url, media_type,
                   status, summary, extracted_entities, metadata, created_at
            FROM documents WHERE id = ANY(%s)
            """,
            (document_ids,),
        )
        evidence["transcripts"] = _safe_fetch(
            """
            SELECT id, document_id, language, confidence, segments, created_at
            FROM transcripts WHERE document_id = ANY(%s)
            """,
            (document_ids,),
        )

    report_ids = by_label.get("Report", [])
    if report_ids:
        evidence["reports"] = _safe_fetch(
            "SELECT id, title, target_id, report_type, status, content, created_at FROM reports WHERE id = ANY(%s)",
            (report_ids,),
        )

    feed_event_ids = by_label.get("FeedEvent", [])
    if feed_event_ids:
        evidence["feed_events"] = _safe_fetch(
            """
            SELECT id, source_id, event_type, payload, observed_at, created_at,
                   ST_AsGeoJSON(geom) AS geom
            FROM feed_events WHERE id = ANY(%s)
            """,
            (feed_event_ids,),
        )

    observation_ids = by_label.get("Observation", [])
    if observation_ids:
        evidence["observations"] = _safe_fetch(
            """
            SELECT id, domain, source_id, entity_id, event_type, title, confidence,
                   ST_AsGeoJSON(geom) AS geom, payload, provenance, observed_at
            FROM observations WHERE id = ANY(%s)
            """,
            (observation_ids,),
        )

    return {
        "focus": focus,
        "nodes": nodes,
        "links": links,
        "evidence_records": evidence,
        "hops": hops,
    }


@router.post("/api/graph/contradict")
def post_graph_contradict(req: GraphContradictRequest):
    """Analyst flags evidence-against: write ``(actor)-[:CONTRADICTED_BY]->(:Detection)``.

    Workflow 4/5 — when the analyst opens a Detection in Evidence mode and
    decides it contradicts a Target classification or an OntologyCandidate's
    proposed class, they call this to attach the dissent as a first-class
    graph relationship. Used by [decisions/why-three-graph-modes.md](../../docs/decisions/why-three-graph-modes.md)
    to keep dissent traversable, not buried in a JSONB column.
    """
    analyst = (req.analyst or "analyst").strip() or "analyst"
    ok = merge_contradicted_by(
        actor_element_id=req.actor_id,
        detection_postgis_id=req.detection_postgis_id,
        reason=req.reason,
        analyst=analyst,
    )
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="actor or detection not found in graph (Detection must already exist)",
        )
    return {
        "success": True,
        "actor_id": req.actor_id,
        "detection_postgis_id": req.detection_postgis_id,
        "analyst": analyst,
    }


@router.post("/api/graph/candidate-edges/{candidate_id}/promote")
def promote_candidate_edge(candidate_id: int, req: GraphPromoteRequest = GraphPromoteRequest()):
    """Graph-side promotion: a pending `CANDIDATE_DETECTED_AS` becomes `DETECTED_AS`.

    Mirrors the effect of ``/api/detection-target-candidates/{id}/approve`` —
    PostGIS row flipped to ``approved`` AND the Neo4j edge is promoted. Both
    sides updated so the analyst can drive the workflow from either the
    SelectionPanel (PostGIS-id-based) or the Investigation graph (graph-edge-based).
    """
    analyst = (req.analyst or "analyst").strip() or "analyst"

    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            """
            UPDATE detection_target_candidates
            SET status = 'approved', reviewed_by = %s, reviewed_at = NOW(), updated_at = NOW()
            WHERE id = %s
            RETURNING id, detection_id, target_id, target_name, score, reason, status,
                      evidence, reviewed_by, reviewed_at, created_at, updated_at
            """,
            (analyst, candidate_id),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Candidate link not found")
        updated = dict(row)

    promoted = promote_candidate_to_detected_as(candidate_id=candidate_id, reviewed_by=analyst)
    if promoted is None:
        # The PostGIS row was approved but the graph edge was missing —
        # fall back to delete-by-pair so the candidate edge (if any) is
        # cleared and the caller can re-render. The analyst-approval flow in
        # main.py is the safer path when the graph edge isn't already in place.
        delete_candidate_detected_as(
            detection_id=updated["detection_id"],
            target_id=updated["target_id"],
        )

    return {"success": True, "candidate": updated, "graph": promoted}

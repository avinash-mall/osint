"""Shared Neo4j projection helpers for the Link Graph.

Owns the small set of MERGE / DELETE Cypher patterns that several writer
sites need (candidate-link creation in ``main.py`` and ``worker_legacy.py``,
plus the new ``/api/graph/...`` endpoints). Keeping them here instead of
inlining preserves a single source of truth for the edge shape — analysts
filter on ``predicate`` in the UI, so the predicate string must match
everywhere it's written.

See [docs/architecture/link-graph-redesign.md](../docs/architecture/link-graph-redesign.md)
for the full edge-predicate inventory.
"""

from __future__ import annotations

import logging
from typing import Any

from database import db

logger = logging.getLogger(__name__)


def merge_candidate_detected_as(
    *,
    detection_id: int,
    detection_class: str | None,
    detection_confidence: float | None,
    detection_lat: float | None,
    detection_lon: float | None,
    target_id: str,
    candidate_id: int,
    score: float,
    reason: str | None,
) -> bool:
    """Persist a pending ``CANDIDATE_DETECTED_AS`` edge.

    Lazily MERGEs the ``:Detection`` node if it isn't already in Neo4j
    (pass-based detections are created by the satellite worker; manual
    or LLM-derived detections may not be). Returns ``True`` if the edge
    was written, ``False`` if the Target wasn't found in Neo4j.

    Property semantics:
    - ``candidate_id`` mirrors the PostGIS ``detection_target_candidates.id``,
      so the ``/promote`` endpoint can locate the row.
    - ``status='pending'`` until promoted to ``DETECTED_AS`` (approve) or
      cleared (reject).
    - ``score`` and ``reason`` mirror the PostGIS row so the graph view can
      surface them without a join.
    """
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (t:Target)
                WHERE elementId(t) = $target_id OR t.id = $target_id
                MERGE (d:Detection {postgis_id: $det_id})
                  ON CREATE SET d.class = $det_class,
                                d.confidence = $confidence,
                                d.latitude = $lat,
                                d.longitude = $lon,
                                d.created_at = datetime()
                MERGE (t)-[rel:CANDIDATE_DETECTED_AS]->(d)
                  ON CREATE SET rel.created_at = datetime()
                SET rel.candidate_id = $candidate_id,
                    rel.score = $score,
                    rel.reason = $reason,
                    rel.status = 'pending',
                    rel.updated_at = datetime()
                RETURN elementId(rel) AS rel_id
                """,
                {
                    "target_id": target_id,
                    "det_id": detection_id,
                    "det_class": detection_class,
                    "confidence": detection_confidence,
                    "lat": detection_lat,
                    "lon": detection_lon,
                    "candidate_id": candidate_id,
                    "score": score,
                    "reason": reason,
                },
            )
            record = result.single()
            return record is not None
    except Exception as exc:  # noqa: BLE001
        # Candidate scoring is a hot path — never let a graph blip fail the
        # PostGIS write. The PostGIS row remains the source of truth.
        logger.warning(
            "graph_writes: merge_candidate_detected_as(det=%s, target=%s) failed: %s",
            detection_id,
            target_id,
            exc,
        )
        return False


def delete_candidate_detected_as(*, detection_id: int, target_id: str) -> int:
    """Delete the ``CANDIDATE_DETECTED_AS`` edge for one (target, detection).

    Called from the approve and reject endpoints. Idempotent — returns the
    number of edges removed (0 or 1 in practice).
    """
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (t:Target)-[rel:CANDIDATE_DETECTED_AS]->(d:Detection {postgis_id: $det_id})
                WHERE elementId(t) = $target_id OR t.id = $target_id
                DELETE rel
                RETURN count(rel) AS removed
                """,
                {"det_id": detection_id, "target_id": target_id},
            )
            record = result.single()
            return int(record["removed"]) if record else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_writes: delete_candidate_detected_as(det=%s, target=%s) failed: %s",
            detection_id,
            target_id,
            exc,
        )
        return 0


_SITE_KIND_TO_LABEL = {
    "base": "Base",
    "launchpoint": "LaunchPoint",
    "launch_point": "LaunchPoint",
    "facility": "Facility",
}


def merge_site_from_aoi(
    *,
    aoi_postgis_id: int,
    kind: str,
    name: str,
    latitude: float | None = None,
    longitude: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """MERGE a ``Base``/``LaunchPoint``/``Facility`` node mirroring an AOI row.

    ``kind`` is matched case-insensitively against ``aoi_kind`` (``base``,
    ``launchpoint``/``launch_point``, ``facility``). Returns the Neo4j
    ``elementId`` of the merged node, or ``None`` if the kind is unrecognised
    or the write fails (logged).

    Identity: ``id = f"aoi-{aoi_postgis_id}"`` (the uniqueness constraint
    is on ``n.id``); ``aoi_postgis_id`` is set as a property so PostGIS
    joins can find the AOI polygon for spatial queries.
    """
    label = _SITE_KIND_TO_LABEL.get((kind or "").lower())
    if label is None:
        return None
    site_id = f"aoi-{aoi_postgis_id}"
    extras = {k: v for k, v in (metadata or {}).items() if k not in {"aoi_kind"}}
    try:
        with db.get_session() as session:
            result = session.run(
                f"""
                MERGE (n:{label} {{id: $id}})
                  ON CREATE SET n.created_at = datetime()
                SET n.aoi_postgis_id = $aoi_postgis_id,
                    n.name = $name,
                    n.latitude = $latitude,
                    n.longitude = $longitude,
                    n.metadata = $metadata,
                    n.updated_at = datetime()
                RETURN elementId(n) AS element_id
                """,
                {
                    "id": site_id,
                    "aoi_postgis_id": aoi_postgis_id,
                    "name": name,
                    "latitude": latitude,
                    "longitude": longitude,
                    "metadata": extras,
                },
            )
            record = result.single()
            return record["element_id"] if record else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_writes: merge_site_from_aoi(aoi=%s, kind=%s) failed: %s",
            aoi_postgis_id,
            kind,
            exc,
        )
        return None


def delete_site_for_aoi(*, aoi_postgis_id: int) -> int:
    """Remove any Base/LaunchPoint/Facility mirror tied to this AOI row.

    Used when an AOI is deleted or its ``aoi_kind`` is cleared. Returns the
    number of nodes detached + deleted (0 or 1 in practice).
    """
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (n)
                WHERE n.aoi_postgis_id = $aoi_postgis_id
                  AND any(l IN labels(n) WHERE l IN ['Base', 'LaunchPoint', 'Facility'])
                DETACH DELETE n
                RETURN count(n) AS removed
                """,
                {"aoi_postgis_id": aoi_postgis_id},
            )
            record = result.single()
            return int(record["removed"]) if record else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_writes: delete_site_for_aoi(aoi=%s) failed: %s",
            aoi_postgis_id,
            exc,
        )
        return 0


def project_fmv_clip_and_tracks(
    *,
    clip_id: int,
    clip_name: str,
    duration_seconds: float | None,
    fps: float | None,
    width: int | None,
    height: int | None,
    tracks: list[dict[str, Any]],
) -> dict[str, int]:
    """MERGE one ``:FMVClip`` stub + one ``:FMVDetection`` per consolidated track.

    Each ``tracks`` row is expected to carry ``track_uid`` (string), ``cls``,
    ``confidence``, ``first_frame``, ``last_frame``. The clip-side node uses
    ``postgis_id`` as identity (matches the constraint registered in
    [graph_schema.py](graph_schema.py)). Per-track identity is the composite
    ``(clip_id, track_uid)`` per the same schema.

    Returns ``{clip: 0|1, tracks: N}`` reporting how many rows were touched.
    Best-effort: Neo4j failures log and return zeros.
    """
    if not tracks:
        # Still MERGE the clip stub so a clip with zero consolidated tracks
        # still shows up in Evidence mode.
        try:
            with db.get_session() as session:
                session.run(
                    """
                    MERGE (c:FMVClip {postgis_id: $clip_id})
                      ON CREATE SET c.created_at = datetime()
                    SET c.name = $clip_name,
                        c.duration_seconds = $duration_seconds,
                        c.fps = $fps,
                        c.width = $width,
                        c.height = $height,
                        c.updated_at = datetime()
                    """,
                    {
                        "clip_id": clip_id,
                        "clip_name": clip_name,
                        "duration_seconds": duration_seconds,
                        "fps": fps,
                        "width": width,
                        "height": height,
                    },
                )
            return {"clip": 1, "tracks": 0}
        except Exception as exc:  # noqa: BLE001
            logger.warning("graph_writes: project_fmv_clip(clip=%s) failed: %s", clip_id, exc)
            return {"clip": 0, "tracks": 0}

    try:
        with db.get_session() as session:
            session.run(
                """
                MERGE (c:FMVClip {postgis_id: $clip_id})
                  ON CREATE SET c.created_at = datetime()
                SET c.name = $clip_name,
                    c.duration_seconds = $duration_seconds,
                    c.fps = $fps,
                    c.width = $width,
                    c.height = $height,
                    c.updated_at = datetime()
                WITH c
                UNWIND $tracks AS t
                MERGE (d:FMVDetection {clip_id: $clip_id, track_uid: t.track_uid})
                  ON CREATE SET d.created_at = datetime()
                SET d.class = t.cls,
                    d.confidence = t.confidence,
                    d.first_frame = t.first_frame,
                    d.last_frame = t.last_frame,
                    d.updated_at = datetime()
                MERGE (c)-[:CONTAINS_DETECTION]->(d)
                """,
                {
                    "clip_id": clip_id,
                    "clip_name": clip_name,
                    "duration_seconds": duration_seconds,
                    "fps": fps,
                    "width": width,
                    "height": height,
                    "tracks": tracks,
                },
            )
        return {"clip": 1, "tracks": len(tracks)}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_writes: project_fmv_clip_and_tracks(clip=%s, tracks=%d) failed: %s",
            clip_id,
            len(tracks),
            exc,
        )
        return {"clip": 0, "tracks": 0}


def project_document_with_mentions(
    *,
    document_id: int,
    title: str,
    media_type: str | None,
    summary: str | None,
    extracted_entities: list[dict[str, Any]] | None,
    entity_label_index: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, int]:
    """MERGE one ``:Document`` stub + ``:MENTIONS`` edges to resolvable entities.

    ``entity_label_index`` is an optional preloaded ``{label_lowercased: [{element_id, label, id, name}, ...]}``
    of analyst-asserted entities (Target / Asset / Vessel / Aircraft / Vehicle).
    When provided, the projector resolves each ``extracted_entities[].label`` to
    every node whose name contains the extracted label (case-insensitive
    substring match — cheap, offline-safe). When ``None``, no edges are written
    and only the Document stub is MERGEd.

    Returns ``{document: 0|1, mentions: N}``. Best-effort.
    """
    entities = extracted_entities or []
    edges_to_write: list[dict[str, Any]] = []
    if entity_label_index:
        for ent in entities:
            label = str(ent.get("label") or "").strip().lower()
            if not label:
                continue
            confidence = ent.get("confidence")
            for needle, matches in entity_label_index.items():
                if needle and (needle in label or label in needle):
                    for m in matches:
                        edges_to_write.append({
                            "target_element_id": m["element_id"],
                            "confidence": confidence,
                            "source_label": label,
                        })

    try:
        with db.get_session() as session:
            session.run(
                """
                MERGE (d:Document {postgis_id: $doc_id})
                  ON CREATE SET d.created_at = datetime()
                SET d.title = $title,
                    d.media_type = $media_type,
                    d.summary = $summary,
                    d.extracted_entity_count = $entity_count,
                    d.updated_at = datetime()
                """,
                {
                    "doc_id": document_id,
                    "title": title,
                    "media_type": media_type,
                    "summary": summary,
                    "entity_count": len(entities),
                },
            )
            mentions = 0
            if edges_to_write:
                result = session.run(
                    """
                    MATCH (d:Document {postgis_id: $doc_id})
                    UNWIND $edges AS e
                    MATCH (other) WHERE elementId(other) = e.target_element_id
                    MERGE (d)-[m:MENTIONS]->(other)
                      ON CREATE SET m.created_at = datetime()
                    SET m.confidence = e.confidence,
                        m.source_label = e.source_label,
                        m.updated_at = datetime()
                    RETURN count(m) AS mentions
                    """,
                    {"doc_id": document_id, "edges": edges_to_write},
                )
                record = result.single()
                mentions = int(record["mentions"]) if record else 0
            return {"document": 1, "mentions": mentions}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_writes: project_document_with_mentions(doc=%s) failed: %s",
            document_id,
            exc,
        )
        return {"document": 0, "mentions": 0}


def load_entity_label_index() -> dict[str, list[dict[str, Any]]]:
    """Build an index of analyst-asserted entity nodes keyed by lowercased name.

    Used by ``project_document_with_mentions`` for cheap substring matching.
    Returns ``{name_lower: [{element_id, label, id, name}, ...]}``.
    """
    index: dict[str, list[dict[str, Any]]] = {}
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (n)
                WHERE any(l IN labels(n) WHERE l IN ['Target', 'Asset', 'Vessel', 'Aircraft', 'Vehicle', 'Unit'])
                  AND n.name IS NOT NULL
                RETURN elementId(n) AS element_id, labels(n) AS label_set, n.id AS id, n.name AS name
                """,
            )
            for record in result:
                name = (record["name"] or "").strip().lower()
                if not name:
                    continue
                index.setdefault(name, []).append({
                    "element_id": record["element_id"],
                    "label": (record["label_set"] or ["Node"])[0],
                    "id": record["id"],
                    "name": record["name"],
                })
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph_writes: load_entity_label_index failed: %s", exc)
    return index


def project_observation_batch(rows: list[dict[str, Any]]) -> int:
    """MERGE one ``:Observation {postgis_id}`` per row and an ``:OBSERVED_AT``
    edge from any analyst-asserted operational node whose ``id`` matches
    ``entity_id``.

    Each row is expected to carry ``postgis_id``, ``entity_id``,
    ``observed_at`` (ISO string), and optional ``latitude``/``longitude``.
    Observations whose ``entity_id`` does not resolve still create the
    ``:Observation`` node — they appear in Evidence-mode as orphan events;
    a later analyst can attach them.

    Returns the number of observation nodes touched.
    """
    if not rows:
        return 0
    try:
        with db.get_session() as session:
            result = session.run(
                """
                UNWIND $rows AS row
                MERGE (o:Observation {postgis_id: row.postgis_id})
                  ON CREATE SET o.created_at = datetime()
                SET o.entity_id = row.entity_id,
                    o.event_type = row.event_type,
                    o.title = row.title,
                    o.confidence = row.confidence,
                    o.latitude = row.latitude,
                    o.longitude = row.longitude,
                    o.timestamp = row.observed_at,
                    o.updated_at = datetime()
                WITH o, row
                OPTIONAL MATCH (op)
                  WHERE op.id IS NOT NULL
                    AND op.id = row.entity_id
                    AND any(l IN labels(op) WHERE l IN ['Target', 'Asset', 'Vessel', 'Aircraft', 'Vehicle', 'Unit'])
                FOREACH (_ IN CASE WHEN op IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (op)-[:OBSERVED_AT]->(o)
                )
                RETURN count(DISTINCT o) AS observations
                """,
                {"rows": rows},
            )
            record = result.single()
            return int(record["observations"]) if record else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph_writes: project_observation_batch(%d rows) failed: %s", len(rows), exc)
        return 0


def merge_contradicted_by(
    *,
    actor_element_id: str,
    detection_postgis_id: int,
    reason: str | None,
    analyst: str,
) -> bool:
    """MERGE ``(actor)-[:CONTRADICTED_BY {reason, analyst, created_at}]->(d:Detection)``.

    The ``actor`` is the OntologyCandidate or Target the analyst is contradicting
    *via* this Detection. Both sides must already exist in Neo4j: detection by
    ``postgis_id``, actor by ``elementId``. Returns ``True`` on success.
    """
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (a) WHERE elementId(a) = $actor
                MATCH (d:Detection {postgis_id: $det_id})
                MERGE (a)-[rel:CONTRADICTED_BY]->(d)
                  ON CREATE SET rel.created_at = datetime()
                SET rel.reason = $reason,
                    rel.analyst = $analyst,
                    rel.updated_at = datetime()
                RETURN elementId(rel) AS rel_id
                """,
                {
                    "actor": actor_element_id,
                    "det_id": detection_postgis_id,
                    "reason": reason,
                    "analyst": analyst,
                },
            )
            return result.single() is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_writes: merge_contradicted_by(actor=%s, det=%s) failed: %s",
            actor_element_id,
            detection_postgis_id,
            exc,
        )
        return False


def project_ontology_branches_and_objects(
    *,
    branches: list[dict[str, Any]],
    objects: list[dict[str, Any]],
) -> dict[str, int]:
    """MERGE every ``OntologyBranch`` and ``OntologyObject`` + their ``HAS_OBJECT`` edges.

    Each branch dict carries ``id, label, parent_id, color, short``. Each
    object dict carries ``id, branch_id, label, prompt, icon_key``. Identity
    is the row ``id`` (matches the constraint registered in
    [graph_schema.py](graph_schema.py)).

    Returns ``{branches: N, objects: M, edges: K}``. Best-effort; failures
    log and return zero.
    """
    if not branches and not objects:
        return {"branches": 0, "objects": 0, "edges": 0}
    try:
        with db.get_session() as session:
            session.run(
                """
                UNWIND $branches AS b
                MERGE (n:OntologyBranch {id: b.id})
                  ON CREATE SET n.created_at = datetime()
                SET n.label = b.label,
                    n.parent_id = b.parent_id,
                    n.color = b.color,
                    n.short = b.short,
                    n.updated_at = datetime()
                """,
                {"branches": branches},
            )
            # Branch parent edges (for branches that have a parent).
            session.run(
                """
                UNWIND $branches AS b
                WITH b WHERE b.parent_id IS NOT NULL
                MATCH (child:OntologyBranch {id: b.id})
                MATCH (parent:OntologyBranch {id: b.parent_id})
                MERGE (parent)-[:HAS_CHILD]->(child)
                """,
                {"branches": branches},
            )
            result = session.run(
                """
                UNWIND $objects AS o
                MERGE (n:OntologyObject {id: o.id})
                  ON CREATE SET n.created_at = datetime()
                SET n.label = o.label,
                    n.prompt = o.prompt,
                    n.icon_key = o.icon_key,
                    n.branch_id = o.branch_id,
                    n.updated_at = datetime()
                WITH n, o
                MATCH (b:OntologyBranch {id: o.branch_id})
                MERGE (b)-[:HAS_OBJECT]->(n)
                RETURN count(DISTINCT n) AS objects
                """,
                {"objects": objects},
            )
            obj_count = int(result.single()["objects"]) if objects else 0
            return {"branches": len(branches), "objects": obj_count, "edges": obj_count}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_writes: project_ontology_branches_and_objects(b=%d, o=%d) failed: %s",
            len(branches), len(objects), exc,
        )
        return {"branches": 0, "objects": 0, "edges": 0}


def project_unknown_label(
    *,
    label: str,
    layer: str | None,
    count: int,
    first_seen: str | None,
    last_seen: str | None,
    suggested_branch_id: str | None,
    supporting_detection_ids: list[int] | None = None,
) -> bool:
    """MERGE ``:UnknownLabel`` + ``:SUGGESTED_BRANCH`` + ``:LABEL_OF`` edges.

    ``supporting_detection_ids`` is an optional list of recent PostGIS detection
    ids whose class equals ``label`` — used to wire ``(d:Detection)-[:LABEL_OF]->(u:UnknownLabel)``
    so analysts in Ontology mode can see "where this label came from."
    Detections that aren't already in Neo4j are skipped (we don't lazily MERGE
    them — that's the satellite worker's job).
    """
    try:
        with db.get_session() as session:
            session.run(
                """
                MERGE (u:UnknownLabel {label: $label})
                  ON CREATE SET u.created_at = datetime()
                SET u.layer = $layer,
                    u.count = $count,
                    u.first_seen = $first_seen,
                    u.last_seen = $last_seen,
                    u.updated_at = datetime()
                """,
                {
                    "label": label, "layer": layer, "count": count,
                    "first_seen": first_seen, "last_seen": last_seen,
                },
            )
            if suggested_branch_id:
                session.run(
                    """
                    MATCH (u:UnknownLabel {label: $label})
                    OPTIONAL MATCH (b:OntologyBranch {id: $branch_id})
                    FOREACH (_ IN CASE WHEN b IS NOT NULL THEN [1] ELSE [] END |
                        MERGE (u)-[:SUGGESTED_BRANCH]->(b)
                    )
                    """,
                    {"label": label, "branch_id": suggested_branch_id},
                )
            if supporting_detection_ids:
                session.run(
                    """
                    MATCH (u:UnknownLabel {label: $label})
                    UNWIND $det_ids AS det_id
                    OPTIONAL MATCH (d:Detection {postgis_id: det_id})
                    FOREACH (_ IN CASE WHEN d IS NOT NULL THEN [1] ELSE [] END |
                        MERGE (d)-[:LABEL_OF]->(u)
                    )
                    """,
                    {"label": label, "det_ids": supporting_detection_ids},
                )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_writes: project_unknown_label(label=%s) failed: %s", label, exc,
        )
        return False


def project_label_of_for_detection_class(
    *,
    detection_class: str,
    ontology_object_id: str,
    detection_postgis_ids: list[int],
) -> int:
    """MERGE ``(d:Detection)-[:LABEL_OF]->(o:OntologyObject)`` for a batch of
    detections sharing the same normalized class.

    Returns the number of edges written. Both sides must already exist —
    Detection is MERGEd by the satellite worker, OntologyObject by
    ``project_ontology_branches_and_objects``.
    """
    if not detection_postgis_ids:
        return 0
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (o:OntologyObject {id: $object_id})
                UNWIND $det_ids AS det_id
                MATCH (d:Detection {postgis_id: det_id})
                MERGE (d)-[r:LABEL_OF]->(o)
                  ON CREATE SET r.created_at = datetime()
                SET r.detection_class = $detection_class
                RETURN count(r) AS edges
                """,
                {
                    "object_id": ontology_object_id,
                    "det_ids": detection_postgis_ids,
                    "detection_class": detection_class,
                },
            )
            record = result.single()
            return int(record["edges"]) if record else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_writes: project_label_of_for_detection_class(class=%s, obj=%s) failed: %s",
            detection_class, ontology_object_id, exc,
        )
        return 0


_OPERATIONAL_KIND_TO_LABEL = {
    "vessel": "Vessel",
    "aircraft": "Aircraft",
    "vehicle": "Vehicle",
    "facility": "Facility",
    "unit": "Unit",
    "asset": "Asset",
}


def merge_operational_entity(
    *,
    entity_id: str,
    kind: str,
    name: str,
    properties: dict[str, Any] | None = None,
) -> bool:
    """MERGE a Vessel/Aircraft/Vehicle/Facility/Unit node.

    All operational entities except Unit also carry the secondary ``:Asset``
    label so generic ``MATCH (a:Asset)`` queries hit them. Identity is
    ``n.id`` (matches the constraints registered in [graph_schema.py](graph_schema.py)).
    """
    label = _OPERATIONAL_KIND_TO_LABEL.get((kind or "").lower())
    if label is None:
        return False
    secondary = ":Asset" if label in {"Vessel", "Aircraft", "Vehicle"} else ""
    cypher = f"""
        MERGE (n:{label}{secondary} {{id: $id}})
          ON CREATE SET n.created_at = datetime()
        SET n.kind = $kind,
            n.name = $name,
            n.metadata = $metadata,
            n.updated_at = datetime()
    """
    try:
        with db.get_session() as session:
            session.run(
                cypher,
                {
                    "id": entity_id,
                    "kind": kind,
                    "name": name,
                    "metadata": properties or {},
                },
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph_writes: merge_operational_entity(id=%s) failed: %s", entity_id, exc)
        return False


def delete_operational_entity(*, entity_id: str) -> int:
    """DETACH DELETE the operational entity by id. Returns count removed."""
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (n) WHERE n.id = $id
                  AND any(l IN labels(n) WHERE l IN ['Vessel', 'Aircraft', 'Vehicle', 'Facility', 'Unit', 'Asset'])
                DETACH DELETE n
                RETURN count(n) AS removed
                """,
                {"id": entity_id},
            )
            record = result.single()
            return int(record["removed"]) if record else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph_writes: delete_operational_entity(id=%s) failed: %s", entity_id, exc)
        return 0


def merge_part_of_edge(*, child_id: str, parent_id: str) -> bool:
    """MERGE ``(child)-[:PART_OF]->(parent)``. Used by Asset→Unit and Unit→Unit."""
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (child) WHERE child.id = $child_id
                MATCH (parent) WHERE parent.id = $parent_id
                MERGE (child)-[r:PART_OF]->(parent)
                  ON CREATE SET r.created_at = datetime()
                RETURN elementId(r) AS rel_id
                """,
                {"child_id": child_id, "parent_id": parent_id},
            )
            return result.single() is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph_writes: merge_part_of_edge(%s -> %s) failed: %s", child_id, parent_id, exc)
        return False


def merge_operates_from_edge(
    *,
    asset_id: str,
    base_id: str,
    confidence: float | None = None,
    source: str = "analyst",
) -> bool:
    """MERGE ``(asset)-[:OPERATES_FROM {confidence, source}]->(base)``."""
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (asset) WHERE asset.id = $asset_id
                MATCH (base) WHERE base.id = $base_id
                  AND any(l IN labels(base) WHERE l IN ['Base', 'LaunchPoint', 'Facility'])
                MERGE (asset)-[r:OPERATES_FROM]->(base)
                  ON CREATE SET r.created_at = datetime()
                SET r.confidence = $confidence,
                    r.source = $source,
                    r.updated_at = datetime()
                RETURN elementId(r) AS rel_id
                """,
                {
                    "asset_id": asset_id, "base_id": base_id,
                    "confidence": confidence, "source": source,
                },
            )
            return result.single() is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph_writes: merge_operates_from_edge(%s -> %s) failed: %s", asset_id, base_id, exc)
        return False


def merge_observed_at_for_asset(*, asset_id: str, observation_postgis_id: int) -> bool:
    """MERGE ``(asset)-[:OBSERVED_AT]->(:Observation)`` connecting an analyst-
    asserted asset to an existing Observation node."""
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (a) WHERE a.id = $asset_id
                MATCH (o:Observation {postgis_id: $obs_id})
                MERGE (a)-[r:OBSERVED_AT]->(o)
                  ON CREATE SET r.created_at = datetime()
                RETURN elementId(r) AS rel_id
                """,
                {"asset_id": asset_id, "obs_id": observation_postgis_id},
            )
            return result.single() is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph_writes: merge_observed_at_for_asset(%s -> %s) failed: %s",
                       asset_id, observation_postgis_id, exc)
        return False


def merge_same_as(*, entity_a_id: str, entity_b_id: str, merged_by: str) -> bool:
    """MERGE the analyst-approved ``(a)-[:SAME_AS]->(b)`` edge.

    Bidirectional in spirit, written one-directionally — graph queries can
    `MATCH (a)-[:SAME_AS]-(b)` without the arrow. The matching POSSIBLY_SAME_AS
    edge (if any) is removed by the caller.
    """
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (a) WHERE a.id = $a_id
                MATCH (b) WHERE b.id = $b_id
                MERGE (a)-[r:SAME_AS]->(b)
                  ON CREATE SET r.created_at = datetime()
                SET r.merged_by = $merged_by,
                    r.merged_at = datetime()
                WITH a, b
                OPTIONAL MATCH (a)-[p:POSSIBLY_SAME_AS]-(b)
                DELETE p
                RETURN 1
                """,
                {"a_id": entity_a_id, "b_id": entity_b_id, "merged_by": merged_by},
            )
            return result.single() is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph_writes: merge_same_as(%s ~ %s) failed: %s", entity_a_id, entity_b_id, exc)
        return False


def delete_possibly_same_as(*, a_id: str, b_id: str) -> int:
    """Phase 5.F: remove pending POSSIBLY_SAME_AS edges between two entities.

    Direction-agnostic (matches either A→B or B→A). Returns the count of edges
    removed. Used by the SAME_AS review-screen reject action.
    """
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (a)-[r:POSSIBLY_SAME_AS]-(b)
                WHERE a.id = $a_id AND b.id = $b_id
                DELETE r
                RETURN count(r) AS removed
                """,
                {"a_id": a_id, "b_id": b_id},
            )
            record = result.single()
            return int(record["removed"]) if record else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph_writes: delete_possibly_same_as(%s ~ %s) failed: %s", a_id, b_id, exc)
        return 0


def merge_possibly_same_as_batch(rows: list[dict[str, Any]]) -> int:
    """MERGE many ``POSSIBLY_SAME_AS`` candidate edges in one UNWIND.

    Each row carries ``a_id, b_id, score, source``. Used by
    ``worker.tick_entity_resimilarity``.
    """
    if not rows:
        return 0
    try:
        with db.get_session() as session:
            result = session.run(
                """
                UNWIND $rows AS row
                MATCH (a) WHERE a.id = row.a_id
                MATCH (b) WHERE b.id = row.b_id
                MERGE (a)-[r:POSSIBLY_SAME_AS {source: row.source}]->(b)
                  ON CREATE SET r.created_at = datetime()
                SET r.score = row.score,
                    r.status = 'pending',
                    r.updated_at = datetime()
                RETURN count(r) AS edges
                """,
                {"rows": rows},
            )
            record = result.single()
            return int(record["edges"]) if record else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph_writes: merge_possibly_same_as_batch(%d) failed: %s", len(rows), exc)
        return 0


def project_near_edges_batch(rows: list[dict[str, Any]]) -> int:
    """MERGE ``(d:Detection)-[:NEAR {distance_m, computed_at}]->(s)`` edges.

    Each row carries ``detection_postgis_id, site_id, distance_m``. Both ends
    must already exist (Detection projected by the satellite worker, site
    projected by [aois router](../docs/backend-routers/aois-router.md)).
    Returns the number of edges written.
    """
    if not rows:
        return 0
    try:
        with db.get_session() as session:
            result = session.run(
                """
                UNWIND $rows AS row
                MATCH (d:Detection {postgis_id: row.detection_postgis_id})
                MATCH (s) WHERE s.id = row.site_id
                  AND any(l IN labels(s) WHERE l IN ['Base', 'LaunchPoint', 'Facility'])
                MERGE (d)-[r:NEAR]->(s)
                  ON CREATE SET r.created_at = datetime()
                SET r.distance_m = row.distance_m,
                    r.computed_at = datetime()
                RETURN count(r) AS edges
                """,
                {"rows": rows},
            )
            record = result.single()
            return int(record["edges"]) if record else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph_writes: project_near_edges_batch(%d rows) failed: %s", len(rows), exc)
        return 0


def project_repeated_at_batch(rows: list[dict[str, Any]]) -> int:
    """MERGE ``(any:Detection)-[:REPEATED_AT {detection_class, count, window_days, radius_m}]->(site)``
    representative edges.

    Each row carries ``site_id, detection_class, sample_detection_id, count,
    window_days, radius_m``. The relationship hangs off ONE sample detection
    (not every detection in the cluster) — analysts see "TEL launcher seen
    14 times at this LaunchPoint" via one edge instead of 14 edges.
    Returns the number of edges written.
    """
    if not rows:
        return 0
    try:
        with db.get_session() as session:
            result = session.run(
                """
                UNWIND $rows AS row
                MATCH (s) WHERE s.id = row.site_id
                  AND any(l IN labels(s) WHERE l IN ['Base', 'LaunchPoint', 'Facility'])
                MATCH (d:Detection {postgis_id: row.sample_detection_id})
                MERGE (d)-[r:REPEATED_AT]->(s)
                  ON CREATE SET r.created_at = datetime()
                SET r.detection_class = row.detection_class,
                    r.count = row.count,
                    r.window_days = row.window_days,
                    r.radius_m = row.radius_m,
                    r.updated_at = datetime()
                RETURN count(r) AS edges
                """,
                {"rows": rows},
            )
            record = result.single()
            return int(record["edges"]) if record else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph_writes: project_repeated_at_batch(%d) failed: %s", len(rows), exc)
        return 0


def promote_candidate_to_detected_as(
    *,
    candidate_id: int,
    reviewed_by: str,
) -> dict[str, Any] | None:
    """Convert the pending ``CANDIDATE_DETECTED_AS`` for a candidate row into
    an approved ``DETECTED_AS`` edge.

    Locates the candidate via the property ``rel.candidate_id`` (set when the
    edge was MERGEd). Returns ``{detection_id, target_id}`` on success or
    ``None`` if no matching pending edge exists.

    This is the Cypher analogue of the PostGIS ``UPDATE
    detection_target_candidates SET status='approved'`` done at the same time
    by the caller. The two updates are not transactional across DBs — the
    caller is responsible for the order (PostGIS first, then graph).
    """
    try:
        with db.get_session() as session:
            result = session.run(
                """
                MATCH (t:Target)-[c:CANDIDATE_DETECTED_AS {candidate_id: $cid}]->(d:Detection)
                WITH t, d, c
                MERGE (t)-[rel:DETECTED_AS]->(d)
                  ON CREATE SET rel.created_at = datetime()
                SET rel.status = 'approved',
                    rel.reviewed_by = $reviewed_by,
                    rel.reviewed_at = datetime()
                DELETE c
                RETURN d.postgis_id AS detection_id, coalesce(t.id, elementId(t)) AS target_id
                """,
                {"cid": candidate_id, "reviewed_by": reviewed_by},
            )
            record = result.single()
            if record is None:
                return None
            return {"detection_id": record["detection_id"], "target_id": record["target_id"]}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "graph_writes: promote_candidate_to_detected_as(cid=%s) failed: %s",
            candidate_id,
            exc,
        )
        return None

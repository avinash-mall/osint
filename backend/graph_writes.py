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

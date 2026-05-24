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

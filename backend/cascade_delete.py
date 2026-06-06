"""Application-level cascade cleanup for detection / imagery deletes.

PostGIS foreign keys already cascade ``detection_target_candidates``,
``detection_track_members`` and ``platform_identification_candidates`` off a
deleted ``detections`` row. Three classes of data are **not** reachable by those
cascades and must be purged explicitly:

  * ``object_details`` — analyst-asserted designation / threat / affiliation,
    keyed by the polymorphic string ``(source, source_id)`` with no FK to
    ``detections``, so nothing cascades.
  * ``detection_tracks`` — the parent track row survives after its last member is
    cascade-deleted, leaving an empty (member-less) track that still renders.
  * ``operational_entity_tracks`` — analyst track attachments keyed by
    ``track_id`` with no FK; they dangle once the track row is gone.

Plus the Neo4j ``:Detection`` mirror node, which only the hard-delete paths
removed.

These helpers are shared by ``delete_imagery``, ``clear_existing_detections``
(re-ingest/replace), ``delete_fmv_clip`` and the per-detection soft-delete so
every delete path leaves zero orphans. We use explicit application-level
cascades rather than new FK migrations to match the existing delete design —
and ``object_details`` cannot use an FK anyway (its ``source_id`` is polymorphic
text). See ``docs/decisions/why-deletable-imagery-and-clips.md``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def affected_track_ids(cursor, detection_ids) -> list[int]:
    """Track ids whose membership includes any of ``detection_ids``.

    Call this **before** the ``detections`` rows are deleted: the FK cascade
    removes the ``detection_track_members`` rows, after which the link is
    unrecoverable.
    """
    ids = list(detection_ids or [])
    if not ids:
        return []
    cursor.execute(
        "SELECT DISTINCT track_id FROM detection_track_members WHERE detection_id = ANY(%s)",
        (ids,),
    )
    return [r["track_id"] for r in cursor.fetchall()]


def purge_object_details(cursor, source: str, source_ids) -> int:
    """Delete ``object_details`` rows for ``(source, source_id IN ids)``.

    ``source_id`` is TEXT, so integer detection / fmv ids are cast to ``str``.
    Returns the number of rows removed.
    """
    ids = [str(i) for i in (source_ids or [])]
    if not ids:
        return 0
    cursor.execute(
        "DELETE FROM object_details WHERE source = %s AND source_id = ANY(%s)",
        (source, ids),
    )
    return cursor.rowcount


def purge_detection_children(cursor, detection_ids) -> None:
    """Explicitly delete the FK-children that a HARD delete would cascade.

    Needed only on the **soft**-delete path, where the ``detections`` row
    survives (so the FK cascade never fires) but its track membership and
    candidate links must still be removed.
    """
    ids = list(detection_ids or [])
    if not ids:
        return
    cursor.execute("DELETE FROM detection_track_members WHERE detection_id = ANY(%s)", (ids,))
    cursor.execute("DELETE FROM detection_target_candidates WHERE detection_id = ANY(%s)", (ids,))
    cursor.execute(
        "DELETE FROM platform_identification_candidates WHERE detection_id = ANY(%s)", (ids,)
    )


def purge_empty_tracks(cursor, track_ids) -> int:
    """Among ``track_ids``, delete tracks that now have no members, plus their
    ``operational_entity_tracks`` attachments.

    Scoped to the passed ids — never a global sweep. Returns the count removed.
    """
    ids = list(track_ids or [])
    if not ids:
        return 0
    cursor.execute(
        """
        DELETE FROM detection_tracks dt
        WHERE dt.id = ANY(%s)
          AND NOT EXISTS (
              SELECT 1 FROM detection_track_members m WHERE m.track_id = dt.id
          )
        RETURNING dt.id
        """,
        (ids,),
    )
    emptied = [r["id"] for r in cursor.fetchall()]
    if emptied:
        cursor.execute(
            "DELETE FROM operational_entity_tracks WHERE track_id = ANY(%s)",
            (emptied,),
        )
    return len(emptied)


def detach_delete_detection_nodes(neo, detection_ids) -> None:
    """``DETACH DELETE`` the Neo4j ``:Detection`` mirror nodes for ``detection_ids``."""
    ids = list(detection_ids or [])
    if not ids:
        return
    neo.run(
        "MATCH (d:Detection) WHERE d.postgis_id IN $ids DETACH DELETE d",
        {"ids": ids},
    )

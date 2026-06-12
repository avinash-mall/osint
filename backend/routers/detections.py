"""Map-side detections — details, operator draw, delete.

Extracted from main.py to keep the entry-point slim. Uses the existing
``object_details`` table (discriminated by ``source = 'detection' | 'fmv_detection'``),
preserving all data and the publish_event broadcasts that downstream SSE
listeners rely on.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from auth import SessionUser, get_current_user
from cascade_delete import (
    affected_track_ids,
    detach_delete_detection_nodes,
    purge_detection_children,
    purge_empty_tracks,
    purge_object_details,
)
from database import db, postgis_db
from detection_helpers import (
    _normalize_affiliation,
    _normalize_threat,
    _read_object_details,
    _upsert_object_details,
)
from detection_policy import parent_class_for_label
from events import publish_event
from platform_schema import bump_tile_version, ensure_platform_tables, get_tile_version
from schemas import ManualDetectionBody, ObjectDetailsBody


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/detections/tile-version")
def get_detections_tile_version():
    """Current detection vector-tile cache-bust token. The map appends it to MVT

    URLs (``?v=…``); a write bumps it so cached tiles refresh. See
    docs/decisions/why-detection-mvt-tiles.md.
    """
    return {"version": get_tile_version()}


@router.get("/api/detections/{detection_id}/details")
def get_detection_details(detection_id: int, user: SessionUser = Depends(get_current_user)):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            "SELECT id, class, source, deleted_at FROM detections WHERE id = %s",
            (detection_id,),
        )
        row = cursor.fetchone()
    if not row or row.get("deleted_at"):
        raise HTTPException(status_code=404, detail="detection not found")
    return {
        "detection_id": detection_id,
        "source": row.get("source") or "ai",
        "object_class": row.get("class"),
        "details": _read_object_details("detection", str(detection_id)),
    }


@router.put("/api/detections/{detection_id}/details")
def put_detection_details(
    detection_id: int,
    body: ObjectDetailsBody,
    user: SessionUser = Depends(get_current_user),
):
    """Write operator-edited metadata. Also writes threat/affiliation onto the
    underlying detection row so existing GeoJSON / track queries see it."""
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            "SELECT id, class, source, deleted_at FROM detections WHERE id = %s",
            (detection_id,),
        )
        row = cursor.fetchone()
    if not row or row.get("deleted_at"):
        raise HTTPException(status_code=404, detail="detection not found")

    saved = _upsert_object_details("detection", str(detection_id), body, user.username)
    threat = saved.get("threat_level")
    affiliation = saved.get("affiliation")
    with postgis_db.get_cursor(commit=True) as cursor:
        meta_patch: dict = {}
        if threat:
            meta_patch["threat_level"] = threat
        if affiliation:
            meta_patch["allegiance"] = affiliation
        if body.designation:
            meta_patch["designation"] = body.designation
        if body.military_classification:
            meta_patch["military_classification"] = body.military_classification
        cursor.execute(
            """
            UPDATE detections SET
                threat_level = COALESCE(%s, threat_level),
                affiliation  = COALESCE(%s, affiliation),
                metadata     = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
            WHERE id = %s
            """,
            (threat, affiliation, json.dumps(meta_patch), detection_id),
        )
    publish_event(
        "detections",
        {"type": "detection_details_updated", "id": detection_id, "details": saved},
    )
    return {"detection_id": detection_id, "details": saved}


@router.post("/api/detections/manual", status_code=201)
def create_manual_detection(
    body: ManualDetectionBody,
    user: SessionUser = Depends(get_current_user),
):
    ensure_platform_tables()
    geom = body.geometry
    if not isinstance(geom, dict) or geom.get("type") not in {"Polygon", "MultiPolygon"}:
        raise HTTPException(
            status_code=400,
            detail="geometry must be a GeoJSON Polygon or MultiPolygon",
        )
    # detections.geom is GEOMETRY(POLYGON, 4326): a single-part MultiPolygon is
    # stored as its one polygon; multi-part input is rejected rather than
    # silently dropping parts.
    if geom.get("type") == "MultiPolygon" and len(geom.get("coordinates") or []) != 1:
        raise HTTPException(
            status_code=400,
            detail="MultiPolygon must contain exactly one polygon (detections store a single Polygon)",
        )
    threat = _normalize_threat(body.threat_level) or "medium"
    affiliation = _normalize_affiliation(body.affiliation) or "unknown"
    cls = (body.object_class or "unknown").strip().lower() or "unknown"

    geom_json = json.dumps(geom)
    metadata = {
        "manual": True,
        "operator": user.username,
        "designation": body.designation or "",
        "military_classification": body.military_classification or "",
        "review_status": "operator",
        "branch_id": parent_class_for_label(cls) or "Other",
        "original_class": cls,
        "threshold_profile": "manual",
        "model_version": "operator",
    }
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            """
            INSERT INTO detections (pass_id, class, confidence, geom, centroid, metadata, threat_level, affiliation, source)
            VALUES (
                %s,
                %s,
                %s,
                CASE WHEN %s = 'Polygon'
                     THEN ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
                     ELSE ST_GeometryN(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 1)
                END,
                ST_Centroid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)),
                %s::jsonb,
                %s,
                %s,
                'operator'
            )
            RETURNING id, class, confidence, metadata,
                      ST_X(centroid) AS lon, ST_Y(centroid) AS lat,
                      ST_AsGeoJSON(geom)::jsonb AS geometry,
                      created_at, threat_level, affiliation, source
            """,
            (
                body.pass_id,
                cls,
                float(body.confidence if body.confidence is not None else 1.0),
                geom.get("type"),
                geom_json,
                geom_json,
                geom_json,
                json.dumps(metadata),
                threat,
                affiliation,
            ),
        )
        row = cursor.fetchone()

    detail_body = ObjectDetailsBody(
        designation=body.designation,
        object_class=cls,
        military_classification=body.military_classification,
        threat_level=threat,
        affiliation=affiliation,
        confidence_override=float(body.confidence) if body.confidence is not None else None,
        notes=body.notes,
    )
    _upsert_object_details("detection", str(row["id"]), detail_body, user.username)

    bump_tile_version()  # new operator-drawn box → bust the tile cache
    publish_event(
        "detections",
        {"type": "detection_created", "id": row["id"], "source": "operator"},
    )
    return {
        "id": row["id"],
        "class": row["class"],
        "confidence": float(row["confidence"]),
        "threat_level": row.get("threat_level"),
        "affiliation": row.get("affiliation"),
        "geometry": row.get("geometry"),
        "lat": float(row["lat"]) if row.get("lat") is not None else None,
        "lon": float(row["lon"]) if row.get("lon") is not None else None,
        "metadata": row.get("metadata") or {},
        "source": "operator",
        "created_at": row.get("created_at"),
    }


@router.delete("/api/detections/{detection_id}")
def delete_detection(detection_id: int, user: SessionUser = Depends(get_current_user)):
    """Soft-delete a detection. Admins can delete anything; analysts can only
    delete operator-drawn boxes."""
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            "SELECT id, source, deleted_at FROM detections WHERE id = %s",
            (detection_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="detection not found")
        if row.get("deleted_at"):
            return {"id": detection_id, "deleted": True, "already_deleted": True}
        is_operator = (row.get("source") or "ai") == "operator"
        if not is_operator and user.role != "admin":
            raise HTTPException(
                status_code=403,
                detail="only admins can delete AI detections; analysts can delete operator-drawn boxes",
            )
        # Capture track membership before purge_detection_children removes it.
        track_ids = affected_track_ids(cursor, [detection_id])
        cursor.execute(
            "UPDATE detections SET deleted_at = NOW() WHERE id = %s RETURNING id",
            (detection_id,),
        )
        # The row survives as a tombstone for audit, but its downstream
        # projections must not keep rendering: drop candidate links + track
        # membership (no FK cascade fires on a soft delete), any now-empty
        # parent track, and the analyst object_details row.
        purge_detection_children(cursor, [detection_id])
        purge_empty_tracks(cursor, track_ids)
        purge_object_details(cursor, "detection", [detection_id])
    try:
        with db.get_session() as neo:
            detach_delete_detection_nodes(neo, [detection_id])
    except Exception:  # noqa: BLE001 — graph cleanup must not fail the delete
        logger.warning(
            "delete_detection: Neo4j cleanup failed for detection %s", detection_id, exc_info=True
        )
    bump_tile_version()  # row tombstoned → bust the tile cache so it disappears
    publish_event(
        "detections",
        {"type": "detection_deleted", "id": detection_id, "by": user.username},
    )
    return {"id": detection_id, "deleted": True}

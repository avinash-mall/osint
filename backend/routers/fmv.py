"""FMV-side detection details + delete.

Extracted from main.py; uses the existing ``object_details`` table
(``source = 'fmv_detection'``).
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from auth import SessionUser, get_current_user
from database import postgis_db
from detection_helpers import (
    _read_object_details,
    _upsert_object_details,
)
from events import publish_event
from platform_schema import ensure_platform_tables
from schemas import ObjectDetailsBody


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/fmv/detections/{detection_id}/details")
def get_fmv_detection_details(detection_id: int, user: SessionUser = Depends(get_current_user)):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            "SELECT id, class, clip_id, deleted_at FROM fmv_detections WHERE id = %s",
            (detection_id,),
        )
        row = cursor.fetchone()
    if not row or row.get("deleted_at"):
        raise HTTPException(status_code=404, detail="fmv detection not found")
    return {
        "detection_id": detection_id,
        "clip_id": row.get("clip_id"),
        "object_class": row.get("class"),
        "details": _read_object_details("fmv_detection", str(detection_id)),
    }


@router.put("/api/fmv/detections/{detection_id}/details")
def put_fmv_detection_details(
    detection_id: int,
    body: ObjectDetailsBody,
    user: SessionUser = Depends(get_current_user),
):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            "SELECT id, class, clip_id, deleted_at FROM fmv_detections WHERE id = %s",
            (detection_id,),
        )
        row = cursor.fetchone()
    if not row or row.get("deleted_at"):
        raise HTTPException(status_code=404, detail="fmv detection not found")

    saved = _upsert_object_details("fmv_detection", str(detection_id), body, user.username)
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
            UPDATE fmv_detections SET
                threat_level = COALESCE(%s, threat_level),
                affiliation  = COALESCE(%s, affiliation),
                metadata     = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
            WHERE id = %s
            """,
            (threat, affiliation, json.dumps(meta_patch), detection_id),
        )
    publish_event(
        f"fmv:{row.get('clip_id')}",
        {"type": "fmv_detection_details_updated", "id": detection_id, "details": saved},
    )
    return {"detection_id": detection_id, "details": saved}


@router.delete("/api/fmv/detections/{detection_id}")
def delete_fmv_detection(detection_id: int, user: SessionUser = Depends(get_current_user)):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            "SELECT id, clip_id, deleted_at, metadata FROM fmv_detections WHERE id = %s",
            (detection_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="fmv detection not found")
        if row.get("deleted_at"):
            return {"id": detection_id, "deleted": True, "already_deleted": True}
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="admin role required to delete FMV detections")
        cursor.execute(
            "UPDATE fmv_detections SET deleted_at = NOW() WHERE id = %s RETURNING id",
            (detection_id,),
        )
    publish_event(
        f"fmv:{row.get('clip_id')}",
        {"type": "fmv_detection_deleted", "id": detection_id, "by": user.username},
    )
    return {"id": detection_id, "deleted": True}

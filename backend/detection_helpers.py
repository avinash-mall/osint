"""Shared helpers for detection / FMV detail endpoints.

Hoisted out of main.py so the new ``routers/detections.py`` and
``routers/fmv.py`` can use them without circular-importing main.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from database import postgis_db
from schemas import ObjectDetailsBody


THREAT_LEVELS = {"critical", "high", "medium", "low", "none"}
AFFILIATIONS = {"friend", "friendly", "hostile", "neutral", "unknown"}


def _normalize_threat(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    norm = value.strip().lower()
    if not norm:
        return None
    if norm not in THREAT_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"threat_level must be one of {sorted(THREAT_LEVELS)}",
        )
    return norm


def _normalize_affiliation(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    norm = value.strip().lower()
    if not norm:
        return None
    if norm == "friendly":
        norm = "friend"
    if norm not in {"friend", "hostile", "neutral", "unknown"}:
        raise HTTPException(
            status_code=400,
            detail="affiliation must be friend/hostile/neutral/unknown",
        )
    return norm


def _read_object_details(source: str, source_id: str) -> dict:
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT designation, object_class, military_classification,
                   threat_level, affiliation, confidence_override, notes,
                   updated_at, updated_by
            FROM object_details
            WHERE source = %s AND source_id = %s
            """,
            (source, str(source_id)),
        )
        row = cursor.fetchone()
    if not row:
        return {}
    return dict(row)


def _upsert_object_details(
    source: str,
    source_id: str,
    body: ObjectDetailsBody,
    updated_by: str,
) -> dict:
    threat = _normalize_threat(body.threat_level)
    affiliation = _normalize_affiliation(body.affiliation)
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            """
            INSERT INTO object_details (
                source, source_id, designation, object_class, military_classification,
                threat_level, affiliation, confidence_override, notes, updated_at, updated_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
            ON CONFLICT (source, source_id) DO UPDATE SET
                designation             = COALESCE(EXCLUDED.designation, object_details.designation),
                object_class            = COALESCE(EXCLUDED.object_class, object_details.object_class),
                military_classification = COALESCE(EXCLUDED.military_classification, object_details.military_classification),
                threat_level            = COALESCE(EXCLUDED.threat_level, object_details.threat_level),
                affiliation             = COALESCE(EXCLUDED.affiliation, object_details.affiliation),
                confidence_override     = COALESCE(EXCLUDED.confidence_override, object_details.confidence_override),
                notes                   = COALESCE(EXCLUDED.notes, object_details.notes),
                updated_at              = NOW(),
                updated_by              = EXCLUDED.updated_by
            RETURNING designation, object_class, military_classification, threat_level,
                      affiliation, confidence_override, notes, updated_at, updated_by
            """,
            (
                source,
                str(source_id),
                body.designation,
                body.object_class,
                body.military_classification,
                threat,
                affiliation,
                body.confidence_override,
                body.notes,
                updated_by,
            ),
        )
        row = cursor.fetchone()
    return dict(row) if row else {}

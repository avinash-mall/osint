"""Admin CRUD for ``repeat_detector_thresholds`` — Phase 5.B.

Per-class admin-editable values that replace the env-var defaults in
``worker.tick_near_builder`` (NEAR radius per site kind) and
``worker.tick_repeat_detector`` (window_days + min_count). Modelled on the
prompt_profiles CRUD in [routers/ontology.py](ontology.py): one row per
``(kind, version)``, with ``current=TRUE`` marking the active row per kind.

The worker reads the active row via ``get_current_threshold(kind)`` and
falls back to env defaults when nothing is configured, so this router is
purely additive — env-only deployments keep working until an analyst
opts into per-class tuning.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from database import postgis_db
from platform_schema import ensure_platform_tables

logger = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_KINDS = {"base", "launchpoint", "facility"}


class ThresholdBody(BaseModel):
    kind: str
    window_days: int = Field(default=30, ge=1, le=3650)
    min_count: int = Field(default=5, ge=1, le=10000)
    near_radius_m: float = Field(default=5000, ge=10, le=50000)
    notes: Optional[str] = None
    make_current: bool = True


@router.get("/api/admin/repeat-thresholds")
def list_thresholds(kind: Optional[str] = Query(None)):
    ensure_platform_tables()
    where = "WHERE 1=1"
    params: list = []
    if kind:
        if kind.lower() not in _ALLOWED_KINDS:
            raise HTTPException(status_code=400, detail=f"invalid kind: {kind}")
        where += " AND kind = %s"
        params.append(kind.lower())
    with postgis_db.get_cursor() as cur:
        cur.execute(
            f"""
            SELECT id, kind, window_days, min_count, near_radius_m, current,
                   notes, created_at, created_by
            FROM repeat_detector_thresholds
            {where}
            ORDER BY kind, current DESC, created_at DESC
            """,
            tuple(params),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"thresholds": rows, "count": len(rows)}


@router.post("/api/admin/repeat-thresholds", status_code=201)
def create_threshold(body: ThresholdBody):
    ensure_platform_tables()
    kind = body.kind.lower()
    if kind not in _ALLOWED_KINDS:
        raise HTTPException(status_code=400, detail=f"invalid kind: {kind}")
    with postgis_db.get_cursor(commit=True) as cur:
        if body.make_current:
            cur.execute("UPDATE repeat_detector_thresholds SET current = FALSE WHERE kind = %s", (kind,))
        cur.execute(
            """
            INSERT INTO repeat_detector_thresholds
              (kind, window_days, min_count, near_radius_m, current, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, kind, window_days, min_count, near_radius_m, current, notes, created_at, created_by
            """,
            (kind, body.window_days, body.min_count, body.near_radius_m, body.make_current, body.notes),
        )
        row = dict(cur.fetchone())
    return row


@router.put("/api/admin/repeat-thresholds/{threshold_id}/activate")
def activate_threshold(threshold_id: int):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("SELECT kind FROM repeat_detector_thresholds WHERE id = %s", (threshold_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="threshold not found")
        kind = row["kind"]
        cur.execute("UPDATE repeat_detector_thresholds SET current = FALSE WHERE kind = %s", (kind,))
        cur.execute(
            """
            UPDATE repeat_detector_thresholds SET current = TRUE WHERE id = %s
            RETURNING id, kind, window_days, min_count, near_radius_m, current
            """,
            (threshold_id,),
        )
        out = dict(cur.fetchone())
    return out


@router.delete("/api/admin/repeat-thresholds/{threshold_id}")
def delete_threshold(threshold_id: int):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM repeat_detector_thresholds WHERE id = %s RETURNING id",
            (threshold_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="threshold not found")
    return {"id": threshold_id, "deleted": True}


def get_current_threshold(kind: str) -> dict | None:
    """Worker-side helper. Returns the active threshold row for ``kind`` or
    ``None`` if no row is configured. Callers fall back to env-var defaults.
    """
    kind = (kind or "").lower()
    if kind not in _ALLOWED_KINDS:
        return None
    try:
        with postgis_db.get_cursor() as cur:
            cur.execute(
                """
                SELECT window_days, min_count, near_radius_m
                FROM repeat_detector_thresholds
                WHERE kind = %s AND current = TRUE
                LIMIT 1
                """,
                (kind,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception:
        logger.warning("admin_thresholds: get_current_threshold(%s) failed", kind, exc_info=True)
        return None

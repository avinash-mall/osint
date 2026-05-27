"""HTTP endpoints for the Reference Embedding DB.

See docs/backend-routers/reference-platforms-router.md for the route catalogue
and docs/backend/reference-platform-db.md for the schema this router queries.
"""

from __future__ import annotations

import base64
import json
import logging
import math
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from auth import SessionUser, get_current_user
from database import postgis_db
from events import publish_event
from reference_platform_db import (
    _upsert_platform_identification,
    attach_identification_candidates,
    find_similar_platforms,
)
from schemas import (
    ApproveRejectResponse,
    IdentificationCandidate,
    IdentificationCandidatesList,
    IdentifyRequest,
    IdentifyResponse,
    ReferenceChipRef,
    ReferencePlatformDetail,
    ReferencePlatformSummary,
    ReferencePlatformsList,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["reference-platforms"])


def _raise_404_or_409(cur, candidate_id: str) -> None:
    """Called when the guarded UPDATE returned 0 rows. Disambiguates between
    'candidate doesn't exist' (404) and 'candidate already reviewed' (409)
    with a follow-up SELECT, and raises the appropriate HTTPException."""
    cur.execute(
        "SELECT status, reviewed_by, reviewed_at "
        "FROM platform_identification_candidates WHERE id = %s",
        (candidate_id,),
    )
    existing = cur.fetchone()
    if existing is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    raise HTTPException(
        status_code=409,
        detail={
            "error": "candidate already reviewed",
            "status": existing["status"],
            "reviewed_by": existing["reviewed_by"],
            "reviewed_at": existing["reviewed_at"].isoformat()
            if existing["reviewed_at"] else None,
        },
    )


def _decode_embedding_anchor(emb: dict) -> Optional[np.ndarray]:
    """Decode metadata['embedding'] = {model, dim, fp16_b64} to a float32 ndarray.

    Returns an ndarray (not a list) so the pgvector adapter in
    `_VectorAwareConnection` round-trips it as a `vector(N)` parameter. A
    Python list adapts as `numeric[]`, which the `<=>` operator rejects.
    """
    fp16_b64 = (emb or {}).get("fp16_b64")
    if not fp16_b64:
        return None
    try:
        raw = base64.b64decode(fp16_b64)
        arr = np.frombuffer(raw, dtype=np.float16).astype(np.float32)
        if arr.shape != (1024,):
            return None
        return arr
    except Exception:
        return None


def _candidate_row_to_model(row: dict) -> IdentificationCandidate:
    return IdentificationCandidate(
        id=str(row["id"]),
        detection_id=row["detection_id"],
        platform_id=str(row["platform_id"]),
        platform_name=row["platform_name"],
        platform_family=row["platform_family"],
        score=float(row["score"]),
        rank=row["rank"],
        matched_chip_ids=[str(x) for x in (row["matched_chip_ids"] or [])],
        status=row["status"],
        applied_at=row["applied_at"].isoformat() if row.get("applied_at") else None,
        reviewed_by=row.get("reviewed_by"),
        reviewed_at=row["reviewed_at"].isoformat() if row.get("reviewed_at") else None,
        created_at=row["created_at"].isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /api/reference-platforms — list (paginated)
# ---------------------------------------------------------------------------


@router.get("/api/reference-platforms", response_model=ReferencePlatformsList)
def list_reference_platforms(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    family: Optional[str] = Query(None, description="Exact match on platform_family"),
    country: Optional[str] = Query(None, description="Exact match on country_of_origin"),
    ontology_object_id: Optional[str] = Query(None),
    user: SessionUser = Depends(get_current_user),
) -> ReferencePlatformsList:
    where = []
    params: list = []
    if family:
        where.append("platform_family = %s")
        params.append(family)
    if country:
        where.append("country_of_origin = %s")
        params.append(country)
    if ontology_object_id:
        where.append("ontology_object_id = %s")
        params.append(ontology_object_id)
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    with postgis_db.get_cursor(commit=False) as cur:
        # Count first (no LIMIT/OFFSET) so the UI can show "showing N of M"
        cur.execute(
            f"SELECT COUNT(*) AS total FROM reference_platforms {where_clause}",
            tuple(params),
        )
        total = cur.fetchone()["total"]

        cur.execute(
            f"""
            SELECT id::text AS id, platform_name, platform_family,
                   ontology_object_id, country_of_origin, role,
                   view_domains, attributes
              FROM reference_platforms
              {where_clause}
             ORDER BY platform_name
             LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )
        rows = cur.fetchall()

    platforms = [
        ReferencePlatformSummary(
            id=r["id"],
            platform_name=r["platform_name"],
            platform_family=r["platform_family"],
            ontology_object_id=r["ontology_object_id"],
            country_of_origin=r["country_of_origin"],
            role=r["role"],
            view_domains=list(r["view_domains"] or []),
            attributes=r["attributes"] or {},
        )
        for r in rows
    ]
    return ReferencePlatformsList(platforms=platforms, count=len(platforms), total=total)


# ---------------------------------------------------------------------------
# GET /api/reference-platforms/{platform_id} — detail with chips
# ---------------------------------------------------------------------------


@router.get(
    "/api/reference-platforms/{platform_id}",
    response_model=ReferencePlatformDetail,
)
def get_reference_platform(
    platform_id: str,
    max_chips: int = Query(20, ge=1, le=100),
    user: SessionUser = Depends(get_current_user),
) -> ReferencePlatformDetail:
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT id::text AS id, platform_name, platform_family,
                   ontology_object_id, country_of_origin, role,
                   view_domains, attributes
              FROM reference_platforms
             WHERE id = %s
            """,
            (platform_id,),
        )
        platform_row = cur.fetchone()
        if not platform_row:
            raise HTTPException(status_code=404, detail="reference_platform not found")
        cur.execute(
            """
            SELECT id::text AS id, chip_path, source_dataset, source_url,
                   license_spdx, attribution
              FROM reference_chips
             WHERE platform_id = %s
             ORDER BY created_at DESC
             LIMIT %s
            """,
            (platform_id, max_chips),
        )
        chip_rows = cur.fetchall()

    chips = [
        ReferenceChipRef(
            id=r["id"],
            chip_path=r["chip_path"],
            source_dataset=r["source_dataset"],
            source_url=r["source_url"],
            license_spdx=r["license_spdx"],
            attribution=r["attribution"],
        )
        for r in chip_rows
    ]
    return ReferencePlatformDetail(
        id=platform_row["id"],
        platform_name=platform_row["platform_name"],
        platform_family=platform_row["platform_family"],
        ontology_object_id=platform_row["ontology_object_id"],
        country_of_origin=platform_row["country_of_origin"],
        role=platform_row["role"],
        view_domains=list(platform_row["view_domains"] or []),
        attributes=platform_row["attributes"] or {},
        chips=chips,
    )


# ---------------------------------------------------------------------------
# POST /api/detections/{detection_id}/identify — re-run lookup
# ---------------------------------------------------------------------------


@router.post(
    "/api/detections/{detection_id}/identify",
    response_model=IdentifyResponse,
)
def identify_detection(
    detection_id: int,
    body: IdentifyRequest,
    user: SessionUser = Depends(get_current_user),
) -> IdentifyResponse:
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "SELECT metadata FROM detections WHERE id = %s",
            (detection_id,),
        )
        det_row = cur.fetchone()
        if not det_row:
            raise HTTPException(status_code=404, detail="detection not found")
        metadata = det_row["metadata"] or {}
        emb_dict = metadata.get("embedding") if isinstance(metadata, dict) else None
        if not emb_dict:
            raise HTTPException(
                status_code=400,
                detail="detection has no embedding (cannot identify without one)",
            )
        anchor = _decode_embedding_anchor(emb_dict)
        if anchor is None:
            raise HTTPException(
                status_code=400,
                detail="detection embedding is malformed (cannot decode fp16_b64)",
            )

        # Attach (re-writes the candidate queue idempotently)
        n = attach_identification_candidates(
            cur,
            detection_id=detection_id,
            embedding=anchor,
            view_domain=body.view_domain,
            auto_threshold=999.0,  # disable auto-apply on analyst re-runs
            top_k=body.top_k,
        )

        cur.execute(
            """
            SELECT c.id::text AS id, c.detection_id, c.platform_id::text AS platform_id,
                   p.platform_name, p.platform_family,
                   c.score, c.rank, c.matched_chip_ids::text[] AS matched_chip_ids,
                   c.status, c.applied_at, c.reviewed_by, c.reviewed_at, c.created_at
              FROM platform_identification_candidates c
              JOIN reference_platforms p ON c.platform_id = p.id
             WHERE c.detection_id = %s
             ORDER BY c.rank
            """,
            (detection_id,),
        )
        rows = cur.fetchall()

    candidates = [_candidate_row_to_model(r) for r in rows]
    publish_event(
        "identifications",
        {
            "type": "identification_refreshed",
            "detection_id": detection_id,
            "candidates_written": n,
            "reviewed_by": user.username,
        },
    )
    return IdentifyResponse(
        detection_id=detection_id,
        candidates_written=n,
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# GET /api/detections/{detection_id}/identification-candidates — read queue
# ---------------------------------------------------------------------------


@router.get(
    "/api/detections/{detection_id}/identification-candidates",
    response_model=IdentificationCandidatesList,
)
def get_identification_candidates(
    detection_id: int,
    user: SessionUser = Depends(get_current_user),
) -> IdentificationCandidatesList:
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT c.id::text AS id, c.detection_id, c.platform_id::text AS platform_id,
                   p.platform_name, p.platform_family,
                   c.score, c.rank, c.matched_chip_ids::text[] AS matched_chip_ids,
                   c.status, c.applied_at, c.reviewed_by, c.reviewed_at, c.created_at
              FROM platform_identification_candidates c
              JOIN reference_platforms p ON c.platform_id = p.id
             WHERE c.detection_id = %s
             ORDER BY c.rank
            """,
            (detection_id,),
        )
        rows = cur.fetchall()
    candidates = [_candidate_row_to_model(r) for r in rows]
    return IdentificationCandidatesList(
        detection_id=detection_id,
        candidates=candidates,
        count=len(candidates),
    )


# ---------------------------------------------------------------------------
# POST /api/identification-candidates/{candidate_id}/approve
# ---------------------------------------------------------------------------


@router.post(
    "/api/identification-candidates/{candidate_id}/approve",
    response_model=ApproveRejectResponse,
)
def approve_identification_candidate(
    candidate_id: str,
    user: SessionUser = Depends(get_current_user),
) -> ApproveRejectResponse:
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE platform_identification_candidates
               SET status = 'approved',
                   reviewed_by = %s,
                   reviewed_at = NOW(),
                   applied_at = NOW()
             WHERE id = %s AND status = 'pending'
            RETURNING id::text AS id, detection_id, platform_id::text AS platform_id,
                      score, reviewed_by, reviewed_at
            """,
            (user.username, candidate_id),
        )
        cand = cur.fetchone()
        if not cand:
            _raise_404_or_409(cur, candidate_id)
        # Look up platform name/family for the upsert
        cur.execute(
            "SELECT platform_name, platform_family FROM reference_platforms WHERE id = %s",
            (cand["platform_id"],),
        )
        plat = cur.fetchone()
        if not plat:
            # Defensive — the FK should make this impossible
            raise HTTPException(status_code=500, detail="referenced platform missing")
        # Clamp the score into the [0,1] CHECK constraint range. NaN can
        # surface when the centroid cosine is undefined (e.g. zero-vector
        # query); treat as 0 so the analyst's approval still records.
        raw_score = float(cand["score"])
        if not math.isfinite(raw_score):
            confidence = 0.0
        else:
            confidence = max(0.0, min(1.0, raw_score))
        _upsert_platform_identification(
            cur,
            detection_id=cand["detection_id"],
            platform_name=plat["platform_name"],
            platform_family=plat["platform_family"],
            platform_confidence=confidence,
            platform_source="analyst",
            updated_by=user.username,
        )

    publish_event(
        "identifications",
        {
            "type": "identification_approved",
            "detection_id": cand["detection_id"],
            "candidate_id": cand["id"],
            "platform_id": cand["platform_id"],
            "platform_name": plat["platform_name"],
            "reviewed_by": user.username,
            "score": float(cand["score"]),
        },
    )
    return ApproveRejectResponse(
        candidate_id=cand["id"],
        status="approved",
        detection_id=cand["detection_id"],
        platform_id=cand["platform_id"],
        reviewed_by=cand["reviewed_by"],
        reviewed_at=cand["reviewed_at"].isoformat(),
    )


# ---------------------------------------------------------------------------
# POST /api/identification-candidates/{candidate_id}/reject
# ---------------------------------------------------------------------------


@router.post(
    "/api/identification-candidates/{candidate_id}/reject",
    response_model=ApproveRejectResponse,
)
def reject_identification_candidate(
    candidate_id: str,
    user: SessionUser = Depends(get_current_user),
) -> ApproveRejectResponse:
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE platform_identification_candidates
               SET status = 'rejected',
                   reviewed_by = %s,
                   reviewed_at = NOW()
             WHERE id = %s AND status = 'pending'
            RETURNING id::text AS id, detection_id, platform_id::text AS platform_id,
                      reviewed_by, reviewed_at
            """,
            (user.username, candidate_id),
        )
        cand = cur.fetchone()
        if not cand:
            _raise_404_or_409(cur, candidate_id)
    publish_event(
        "identifications",
        {
            "type": "identification_rejected",
            "detection_id": cand["detection_id"],
            "candidate_id": cand["id"],
            "platform_id": cand["platform_id"],
            "reviewed_by": user.username,
        },
    )
    return ApproveRejectResponse(
        candidate_id=cand["id"],
        status="rejected",
        detection_id=cand["detection_id"],
        platform_id=cand["platform_id"],
        reviewed_by=cand["reviewed_by"],
        reviewed_at=cand["reviewed_at"].isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /api/reference-chips/{chip_id}/image — serve a chip thumbnail
# ---------------------------------------------------------------------------

# Constant root every chip_path must be under. Set at import time so any
# misconfiguration surfaces immediately rather than per-request.
_REFERENCE_CHIPS_ROOT = Path("/data/datasets").resolve()


@router.get("/api/reference-chips/{chip_id}/image")
def serve_reference_chip_image(
    chip_id: str,
    user: SessionUser = Depends(get_current_user),
):
    """Stream the chip PNG/JPEG at `reference_chips.chip_path`.

    Defense in depth: the resolved chip_path MUST be under `/data/datasets/`.
    A row pointing anywhere else (data corruption, malicious migration)
    returns 403, NOT the file. Prevents path traversal even if the DB is
    compromised.
    """
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT chip_path FROM reference_chips WHERE id = %s",
            (chip_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="reference_chip not found")

    raw_path = row["chip_path"]
    try:
        resolved = Path(raw_path).resolve()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid chip path: {e}")

    # Validate resolved path is under the allowed root.
    try:
        resolved.relative_to(_REFERENCE_CHIPS_ROOT)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="chip_path is not under /data/datasets/ (refusing to serve)",
        )

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="chip file not on disk")

    # Infer media type from extension; reject unknown extensions with 415.
    ext = resolved.suffix.lower()
    _MEDIA_TYPES = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    media_type = _MEDIA_TYPES.get(ext)
    if media_type is None:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported chip extension {ext!r}; expected one of {sorted(_MEDIA_TYPES)}",
        )

    return FileResponse(
        path=str(resolved),
        media_type=media_type,
        content_disposition_type="inline",
    )

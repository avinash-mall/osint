"""Operational-entity CRUD + Neo4j projection — Phase 4 of the Link Graph redesign.

Vessel / Aircraft / Vehicle / Facility / Unit / Asset entities are
analyst-asserted (with an optional LLM-proposal queue under
``entity_candidates`` — Phase 4.F). Each create/update/delete projects the
matching node into Neo4j with the secondary ``:Asset`` label where
applicable so generic queries can find them.

See [docs/architecture/link-graph-redesign.md](../../docs/architecture/link-graph-redesign.md)
for the operational-entity model.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from database import postgis_db
from graph_writes import (
    delete_operational_entity,
    merge_observed_at_for_asset,
    merge_operates_from_edge,
    merge_operational_entity,
    merge_part_of_edge,
    merge_same_as,
)
from platform_schema import ensure_platform_tables

logger = logging.getLogger(__name__)
router = APIRouter()


_ALLOWED_KINDS = {"vessel", "aircraft", "vehicle", "facility", "unit", "asset"}
_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _slug(value: str) -> str:
    return _SLUG_RE.sub("-", value.lower()).strip("-")[:64] or uuid.uuid4().hex[:12]


class OperationalEntityCreate(BaseModel):
    kind: str
    name: str
    id: Optional[str] = Field(None, description="Stable analyst-friendly id; auto-derived from name if omitted")
    callsign: Optional[str] = None
    hull: Optional[str] = None
    entity_class: Optional[str] = None
    unit_id: Optional[str] = None
    operates_from_base_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperationalEntityUpdate(BaseModel):
    name: Optional[str] = None
    callsign: Optional[str] = None
    hull: Optional[str] = None
    entity_class: Optional[str] = None
    unit_id: Optional[str] = None
    operates_from_base_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class SameAsRequest(BaseModel):
    analyst: Optional[str] = None


class AttachObservationRequest(BaseModel):
    observation_postgis_id: int


def _row_to_dict(row: Any) -> dict[str, Any]:
    out = dict(row)
    metadata = out.get("metadata")
    if isinstance(metadata, str):
        try:
            out["metadata"] = json.loads(metadata)
        except json.JSONDecodeError:
            out["metadata"] = {}
    return out


def _project_to_graph(row: dict[str, Any]) -> bool:
    """Mirror an operational_entities row into Neo4j + wire its convenience edges."""
    ok = merge_operational_entity(
        entity_id=row["id"], kind=row["kind"], name=row["name"],
        properties={
            "callsign": row.get("callsign"),
            "hull": row.get("hull"),
            "entity_class": row.get("entity_class"),
            **(row.get("metadata") or {}),
        },
    )
    if not ok:
        return False
    if row.get("unit_id"):
        merge_part_of_edge(child_id=row["id"], parent_id=row["unit_id"])
    if row.get("operates_from_base_id"):
        merge_operates_from_edge(
            asset_id=row["id"], base_id=row["operates_from_base_id"],
            confidence=None, source="analyst",
        )
    return True


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("/api/operational-entities")
def list_operational_entities(
    kind: Optional[str] = Query(None, description="Filter by entity kind"),
    limit: int = Query(200, ge=1, le=1000),
):
    ensure_platform_tables()
    where = "WHERE 1=1"
    params: list[Any] = []
    if kind:
        if kind.lower() not in _ALLOWED_KINDS:
            raise HTTPException(status_code=400, detail=f"invalid kind: {kind}")
        where += " AND kind = %s"
        params.append(kind.lower())
    params.append(limit)
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, kind, name, callsign, hull, entity_class, unit_id,
                   operates_from_base_id, metadata, created_by, created_at
            FROM operational_entities
            {where}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows = [_row_to_dict(r) for r in cursor.fetchall()]
    return {"entities": rows, "count": len(rows)}


@router.get("/api/operational-entities/{entity_id}")
def get_operational_entity(entity_id: str):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, kind, name, callsign, hull, entity_class, unit_id,
                   operates_from_base_id, metadata, created_by, created_at
            FROM operational_entities WHERE id = %s
            """,
            (entity_id,),
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="entity not found")
    return _row_to_dict(row)


@router.post("/api/operational-entities")
def create_operational_entity(body: OperationalEntityCreate):
    ensure_platform_tables()
    kind = body.kind.lower()
    if kind not in _ALLOWED_KINDS:
        raise HTTPException(status_code=400, detail=f"invalid kind: {body.kind}")
    entity_id = (body.id or _slug(body.name) or uuid.uuid4().hex[:12])

    with postgis_db.get_cursor(commit=True) as cursor:
        try:
            cursor.execute(
                """
                INSERT INTO operational_entities (id, kind, name, callsign, hull,
                                                  entity_class, unit_id, operates_from_base_id,
                                                  metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, kind, name, callsign, hull, entity_class, unit_id,
                          operates_from_base_id, metadata, created_by, created_at
                """,
                (
                    entity_id, kind, body.name, body.callsign, body.hull,
                    body.entity_class, body.unit_id, body.operates_from_base_id,
                    json.dumps(body.metadata),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=f"insert failed: {exc}") from exc
        row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=500, detail="insert returned no row")
    record = _row_to_dict(row)
    graph_ok = _project_to_graph(record)
    return {"success": True, "entity": record, "graph_written": graph_ok}


@router.patch("/api/operational-entities/{entity_id}")
def update_operational_entity(entity_id: str, body: OperationalEntityUpdate):
    ensure_platform_tables()
    updates: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("name", body.name), ("callsign", body.callsign), ("hull", body.hull),
        ("entity_class", body.entity_class), ("unit_id", body.unit_id),
        ("operates_from_base_id", body.operates_from_base_id),
    ):
        if value is not None:
            updates.append(f"{column} = %s"); params.append(value)
    if body.metadata is not None:
        updates.append("metadata = %s"); params.append(json.dumps(body.metadata))
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")
    updates.append("updated_at = NOW()")
    params.append(entity_id)

    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            f"""
            UPDATE operational_entities SET {', '.join(updates)}
            WHERE id = %s
            RETURNING id, kind, name, callsign, hull, entity_class, unit_id,
                      operates_from_base_id, metadata, created_by, created_at
            """,
            tuple(params),
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="entity not found")
    record = _row_to_dict(row)
    _project_to_graph(record)
    return {"success": True, "entity": record}


@router.delete("/api/operational-entities/{entity_id}")
def delete_operational_entity_route(entity_id: str):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            "DELETE FROM operational_entities WHERE id = %s RETURNING id",
            (entity_id,),
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="entity not found")
    removed = delete_operational_entity(entity_id=entity_id)
    return {"success": True, "id": entity_id, "graph_nodes_removed": removed}


# ---------------------------------------------------------------------------
# Edge actions
# ---------------------------------------------------------------------------


@router.post("/api/operational-entities/{entity_id}/attach-observation")
def attach_observation(entity_id: str, body: AttachObservationRequest):
    ensure_platform_tables()
    ok = merge_observed_at_for_asset(
        asset_id=entity_id, observation_postgis_id=body.observation_postgis_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="entity or observation not found in graph")
    return {"success": True, "entity_id": entity_id, "observation_postgis_id": body.observation_postgis_id}


@router.post("/api/operational-entities/{entity_id}/operates-from/{base_id}")
def set_operates_from(entity_id: str, base_id: str, confidence: float | None = Query(None, ge=0.0, le=1.0)):
    ensure_platform_tables()
    # Also update the PostGIS column so list() reflects the relationship.
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            "UPDATE operational_entities SET operates_from_base_id = %s, updated_at = NOW() WHERE id = %s RETURNING id",
            (base_id, entity_id),
        )
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="entity not found")
    ok = merge_operates_from_edge(asset_id=entity_id, base_id=base_id, confidence=confidence, source="analyst")
    if not ok:
        raise HTTPException(status_code=404, detail="base not found in graph")
    return {"success": True, "entity_id": entity_id, "base_id": base_id}


@router.post("/api/operational-entities/{entity_id}/part-of/{unit_id}")
def set_part_of(entity_id: str, unit_id: str):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            "UPDATE operational_entities SET unit_id = %s, updated_at = NOW() WHERE id = %s RETURNING id",
            (unit_id, entity_id),
        )
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="entity not found")
    ok = merge_part_of_edge(child_id=entity_id, parent_id=unit_id)
    if not ok:
        raise HTTPException(status_code=404, detail="unit not found in graph")
    return {"success": True, "entity_id": entity_id, "unit_id": unit_id}


# ---------------------------------------------------------------------------
# Entity-candidate review (Phase 4.F)
# ---------------------------------------------------------------------------


@router.get("/api/operational-entity-candidates")
def list_entity_candidates(
    status: str = Query("pending", description="pending|approved|rejected"),
    kind: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    ensure_platform_tables()
    where = "WHERE status = %s"
    params: list[Any] = [status]
    if kind:
        if kind.lower() not in _ALLOWED_KINDS:
            raise HTTPException(status_code=400, detail=f"invalid kind: {kind}")
        where += " AND entity_kind = %s"
        params.append(kind.lower())
    params.append(limit)
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, entity_kind, proposed_name, seed_detection_ids, score, reason,
                   status, proposed_metadata, reviewed_by, reviewed_at,
                   approved_entity_id, created_at
            FROM entity_candidates {where}
            ORDER BY score DESC NULLS LAST, created_at DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows = [dict(r) for r in cursor.fetchall()]
    return {"candidates": rows, "count": len(rows)}


@router.post("/api/operational-entity-candidates/{candidate_id}/approve")
def approve_entity_candidate(candidate_id: int, analyst: Optional[str] = None):
    """Approve a proposed entity: create the operational_entities row + project."""
    ensure_platform_tables()
    analyst = (analyst or "analyst").strip() or "analyst"
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            "SELECT id, entity_kind, proposed_name, proposed_metadata FROM entity_candidates WHERE id = %s AND status = 'pending'",
            (candidate_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="pending candidate not found")
        candidate = dict(row)

        entity_id = _slug(candidate["proposed_name"]) or uuid.uuid4().hex[:12]
        metadata = candidate.get("proposed_metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}

        try:
            cursor.execute(
                """
                INSERT INTO operational_entities (id, kind, name, metadata, created_by)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, kind, name, callsign, hull, entity_class, unit_id,
                          operates_from_base_id, metadata, created_by, created_at
                """,
                (entity_id, candidate["entity_kind"], candidate["proposed_name"], json.dumps(metadata), analyst),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=f"insert failed: {exc}") from exc
        entity_row = cursor.fetchone()
        if not entity_row:
            raise HTTPException(status_code=500, detail="entity insert returned no row")

        cursor.execute(
            """
            UPDATE entity_candidates
            SET status = 'approved', reviewed_by = %s, reviewed_at = NOW(),
                approved_entity_id = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING id, entity_kind, proposed_name, status, reviewed_by, reviewed_at, approved_entity_id
            """,
            (analyst, entity_id, candidate_id),
        )
        updated = dict(cursor.fetchone())

    record = _row_to_dict(entity_row)
    _project_to_graph(record)
    return {"success": True, "entity": record, "candidate": updated}


@router.post("/api/operational-entity-candidates/{candidate_id}/reject")
def reject_entity_candidate(candidate_id: int, analyst: Optional[str] = None):
    ensure_platform_tables()
    analyst = (analyst or "analyst").strip() or "analyst"
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            """
            UPDATE entity_candidates
            SET status = 'rejected', reviewed_by = %s, reviewed_at = NOW(), updated_at = NOW()
            WHERE id = %s AND status = 'pending'
            RETURNING id, entity_kind, proposed_name, status, reviewed_by, reviewed_at
            """,
            (analyst, candidate_id),
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="pending candidate not found")
    return {"success": True, "candidate": dict(row)}


@router.post("/api/operational-entities/{entity_id}/same-as/{other_id}")
def set_same_as(entity_id: str, other_id: str, body: SameAsRequest = SameAsRequest()):
    """Analyst approves: two operational entities are the same thing.

    Writes the canonical ``:SAME_AS`` edge (and deletes the matching
    ``POSSIBLY_SAME_AS`` candidate edge if any was proposed). Does NOT merge
    PostGIS rows — that's a follow-up action with conflict-resolution UI.
    """
    ensure_platform_tables()
    analyst = (body.analyst or "analyst").strip() or "analyst"
    ok = merge_same_as(entity_a_id=entity_id, entity_b_id=other_id, merged_by=analyst)
    if not ok:
        raise HTTPException(status_code=404, detail="one or both entities not found in graph")
    return {"success": True, "a": entity_id, "b": other_id, "merged_by": analyst}

"""Ontology router — branches, objects, prompt profiles, version history, unknown labels.

Extracted from backend/main.py. Wires every ``/api/ontology*`` route plus
the read-side ``/api/detections/prithvi-overlays`` helper that depends on
``ontology_default_prompts``.

Endpoints (12):
  GET    /api/ontology                                — full tree (+ sensor filter)
  GET    /api/ontology/version                        — current version_id
  GET    /api/ontology/default-prompts                — sensor-default prompts
  GET    /api/ontology/unknown-labels                 — triage queue
  POST   /api/ontology/unknown-labels/{label}/assign  — promote/merge
  POST   /api/ontology/branches                       — create branch (admin)
  PATCH  /api/ontology/branches/{branch_id}           — edit branch (admin)
  DELETE /api/ontology/branches/{branch_id}           — delete branch (admin)
  POST   /api/ontology/objects                        — create object (admin)
  PATCH  /api/ontology/objects/{object_id}            — edit object (admin)
  DELETE /api/ontology/objects/{object_id}            — delete object (admin)
  GET    /api/ontology/prompt-profiles                — list saved profiles
  POST   /api/ontology/prompt-profiles                — create / overwrite
  PUT    /api/ontology/prompt-profiles/{id}/activate  — make profile current
  DELETE /api/ontology/prompt-profiles/{id}           — remove profile
  GET    /api/ontology/version-history                — change log

NOTE: every mutation calls ``ontology_bump_version`` so the worker /
inference service caches invalidate.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import SessionUser, get_current_user, require_admin
from database import postgis_db
from ontology import (
    _branch_row_to_dict,
    _filter_branch_by_sensor,
    _filter_object_by_sensor,
    _object_row_to_dict,
    ontology_bump_version,
    ontology_default_prompts,
    ontology_get_version,
)
from platform_schema import ensure_platform_tables
from schemas import (
    OntologyAssignBody,
    OntologyBranchIn,
    OntologyBranchPatch,
    OntologyObjectIn,
    OntologyObjectPatch,
    PromptProfileBody,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ontology", tags=["ontology"])


# ─── Tree + version ────────────────────────────────────────────────────

@router.get("")
def get_ontology(sensor: Optional[str] = Query(None)):
    """Return the full ontology tree (branches + nested objects) and version_id."""
    sensor_norm = sensor.lower() if sensor else None
    with postgis_db.get_cursor() as cursor:
        cursor.execute("SELECT version_id FROM ontology_version LIMIT 1")
        vrow = cursor.fetchone()
        version_id = int(vrow["version_id"]) if vrow else 0

        cursor.execute(
            "SELECT id, parent_id, label, color, short, icon_key, matchers, "
            "       sensors, order_index "
            "FROM ontology_branches "
            "ORDER BY order_index ASC, id ASC"
        )
        branch_rows = [_branch_row_to_dict(dict(r)) for r in cursor.fetchall()]

        cursor.execute(
            "SELECT id, branch_id, label, prompt, sensors, min_gsd_meters, "
            "       icon_key, order_index "
            "FROM ontology_objects "
            "ORDER BY order_index ASC, id ASC"
        )
        obj_rows = [_object_row_to_dict(dict(r)) for r in cursor.fetchall()]

    objs_by_branch: dict[str, list[dict]] = {}
    for o in obj_rows:
        if not _filter_object_by_sensor(o, sensor_norm):
            continue
        objs_by_branch.setdefault(o["branch_id"], []).append(o)

    children_by_parent: dict[Optional[str], list[dict]] = {}
    for b in branch_rows:
        children_by_parent.setdefault(b.get("parent_id"), []).append(b)

    def build_node(b: dict) -> Optional[dict]:
        node = dict(b)
        node["objects"] = list(objs_by_branch.get(b["id"], []))
        children: list[dict] = []
        for child in children_by_parent.get(b["id"], []):
            built = build_node(child)
            if built is not None:
                children.append(built)
        node["children"] = children
        if sensor_norm and not node["objects"] and not node["children"]:
            if not _filter_branch_by_sensor(b, sensor_norm):
                return None
        return node

    roots: list[dict] = []
    for b in children_by_parent.get(None, []):
        built = build_node(b)
        if built is not None:
            roots.append(built)
    return {"version_id": version_id, "branches": roots}


@router.get("/version")
def get_ontology_version():
    return {"version_id": int(ontology_get_version())}


@router.get("/default-prompts")
def get_ontology_default_prompts(
    sensor: Optional[str] = Query(None),
    branch: Optional[str] = Query(
        None, description="Scope to one branch + its descendants for a smaller, scene-relevant vocabulary"
    ),
):
    return {"prompts": ontology_default_prompts(sensor or None, branch or None)}


# ─── Unknown labels (triage queue) ─────────────────────────────────────

@router.get("/unknown-labels")
def get_ontology_unknown_labels(
    since: Optional[str] = Query(None, description="ISO datetime; only rows last_seen >= since"),
    limit: int = Query(100, ge=1, le=1000),
):
    where_clauses: list[str] = []
    params: list = []
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid since: expected an ISO 8601 datetime")
        where_clauses.append("last_seen >= %s")
        params.append(since_dt)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    params.append(limit)
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT label, layer, first_seen, last_seen, count, suggested_branch_id
            FROM ontology_unknown_labels
            {where_sql}
            ORDER BY last_seen DESC, count DESC
            LIMIT %s
            """,
            params,
        )
        rows = [dict(r) for r in cursor.fetchall()]
    out = []
    for r in rows:
        out.append({
            "label": r["label"],
            "layer": r.get("layer"),
            "first_seen": r["first_seen"].isoformat() if r.get("first_seen") else None,
            "last_seen": r["last_seen"].isoformat() if r.get("last_seen") else None,
            "count": int(r.get("count") or 0),
            "suggested_branch_id": r.get("suggested_branch_id"),
        })
    return {"unknown_labels": out}


@router.post("/unknown-labels/{label}/assign")
def assign_unknown_label(
    label: str,
    body: OntologyAssignBody,
    user: SessionUser = Depends(require_admin),
):
    if body.object_id and body.create_object:
        raise HTTPException(status_code=400, detail="provide object_id OR create_object, not both")
    bid = (body.branch_id or "").strip()
    if not bid:
        raise HTTPException(status_code=400, detail="branch_id is required")
    created_object_id: Optional[str] = None
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("SELECT 1 FROM ontology_unknown_labels WHERE label = %s", (label,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="unknown label not found")
        cursor.execute("SELECT 1 FROM ontology_branches WHERE id = %s", (bid,))
        if not cursor.fetchone():
            raise HTTPException(status_code=400, detail=f"branch_id {bid} does not exist")

        if body.object_id:
            cursor.execute(
                "SELECT 1 FROM ontology_objects WHERE id = %s AND branch_id = %s",
                (body.object_id, bid),
            )
            if not cursor.fetchone():
                cursor.execute("SELECT branch_id FROM ontology_objects WHERE id = %s", (body.object_id,))
                row = cursor.fetchone()
                if not row:
                    raise HTTPException(status_code=400, detail=f"object_id {body.object_id} does not exist")
                raise HTTPException(
                    status_code=400,
                    detail=f"object_id {body.object_id} belongs to branch {row['branch_id']}, not {bid}",
                )

        if body.create_object:
            co = body.create_object
            caller_oid = (co.id or "").strip()
            sensors_json = json.dumps(co.sensors if co.sensors is not None else ["optical"])
            # ON CONFLICT DO NOTHING RETURNING removes the SELECT/INSERT
            # race: two concurrent triagers can no longer both pass the
            # existence check and then conflict on INSERT. Caller-supplied
            # ids still get a 409 (no row returned); generated ids retry
            # with a fresh UUID since 10-hex = 40 bits is not collision-
            # proof under heavy concurrent triage.
            new_oid: Optional[str] = None
            attempts = 1 if caller_oid else 3
            for _ in range(attempts):
                candidate = caller_oid or f"obj_{uuid.uuid4().hex[:10]}"
                cursor.execute(
                    "INSERT INTO ontology_objects "
                    "(id, branch_id, label, prompt, sensors, min_gsd_meters, icon_key, order_index) "
                    "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s) "
                    "ON CONFLICT (id) DO NOTHING RETURNING id",
                    (
                        candidate, bid, co.label, co.prompt, sensors_json,
                        co.min_gsd_meters, co.icon_key,
                        int(co.order_index or 0),
                    ),
                )
                row = cursor.fetchone()
                if row:
                    new_oid = row["id"] if isinstance(row, dict) else row[0]
                    break
            if new_oid is None:
                if caller_oid:
                    raise HTTPException(status_code=409, detail=f"object {caller_oid} already exists")
                raise HTTPException(status_code=500, detail="failed to allocate unique object id after 3 attempts")
            created_object_id = new_oid

        cursor.execute("DELETE FROM ontology_unknown_labels WHERE label = %s", (label,))

    ontology_bump_version(
        summary=f"unknown label assigned: {label} → {created_object_id or 'existing'}",
        changes={"op": "assign_unknown", "label": label, "branch_id": bid, "created_object_id": created_object_id},
        by=user.username,
    )
    return {
        "assigned_to_branch": bid,
        "created_object_id": created_object_id,
        "removed_from_queue": True,
    }


# ─── Branches CRUD ─────────────────────────────────────────────────────

def _fetch_branch(cursor, branch_id: str) -> Optional[dict]:
    cursor.execute(
        "SELECT id, parent_id, label, color, short, icon_key, matchers, "
        "       sensors, order_index, created_at, updated_at "
        "FROM ontology_branches WHERE id = %s",
        (branch_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _fetch_object(cursor, object_id: str) -> Optional[dict]:
    cursor.execute(
        "SELECT id, branch_id, label, prompt, sensors, min_gsd_meters, "
        "       icon_key, order_index, created_at, updated_at "
        "FROM ontology_objects WHERE id = %s",
        (object_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


@router.post("/branches", status_code=201)
def create_ontology_branch(body: OntologyBranchIn, user: SessionUser = Depends(require_admin)):
    bid = (body.id or "").strip()
    if not bid:
        raise HTTPException(status_code=400, detail="id is required")
    if not (body.label or "").strip():
        raise HTTPException(status_code=400, detail="label is required")
    matchers_json = json.dumps(body.matchers or [])
    sensors_json = json.dumps(body.sensors if body.sensors is not None else ["optical"])
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("SELECT 1 FROM ontology_branches WHERE id = %s", (bid,))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail=f"branch {bid} already exists")
        if body.parent_id:
            cursor.execute("SELECT 1 FROM ontology_branches WHERE id = %s", (body.parent_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=400, detail=f"parent_id {body.parent_id} does not exist")
        cursor.execute(
            "INSERT INTO ontology_branches "
            "(id, parent_id, label, color, short, icon_key, matchers, sensors, order_index) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s) "
            "RETURNING id, parent_id, label, color, short, icon_key, matchers, sensors, order_index",
            (
                bid, body.parent_id, body.label, body.color, body.short,
                body.icon_key, matchers_json, sensors_json,
                int(body.order_index or 0),
            ),
        )
        row = dict(cursor.fetchone())
    ontology_bump_version(summary=f"branch created: {bid}", changes={"op": "create_branch", "id": bid}, by=user.username)
    return _branch_row_to_dict(row)


_PATCH_BRANCH_COLUMNS = {
    "parent_id", "label", "color", "short", "icon_key",
    "matchers", "sensors", "order_index",
}


@router.patch("/branches/{branch_id}")
def patch_ontology_branch(branch_id: str, body: OntologyBranchPatch, user: SessionUser = Depends(require_admin)):
    payload = body.dict(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="no fields to update")
    # Defense-in-depth: the Pydantic schema constrains the key set, but
    # an explicit column whitelist keeps a future schema-vs-DB drift
    # from surfacing as a cryptic postgres syntax error.
    bad_keys = set(payload) - _PATCH_BRANCH_COLUMNS
    if bad_keys:
        raise HTTPException(status_code=400, detail=f"unknown fields: {sorted(bad_keys)}")
    set_clauses: list[str] = []
    params: list = []
    for key, val in payload.items():
        if key in ("matchers", "sensors"):
            set_clauses.append(f"{key} = %s::jsonb")
            params.append(json.dumps(val if val is not None else []))
        elif key == "order_index":
            set_clauses.append("order_index = %s")
            params.append(int(val or 0))
        elif key == "parent_id":
            set_clauses.append("parent_id = %s")
            params.append(val)
        else:
            set_clauses.append(f"{key} = %s")
            params.append(val)
    set_clauses.append("updated_at = now()")
    params.append(branch_id)
    with postgis_db.get_cursor(commit=True) as cursor:
        existing = _fetch_branch(cursor, branch_id)
        if not existing:
            raise HTTPException(status_code=404, detail="branch not found")
        if "parent_id" in payload and payload["parent_id"]:
            if payload["parent_id"] == branch_id:
                raise HTTPException(status_code=400, detail="branch cannot be its own parent")
            cursor.execute("SELECT 1 FROM ontology_branches WHERE id = %s", (payload["parent_id"],))
            if not cursor.fetchone():
                raise HTTPException(status_code=400, detail=f"parent_id {payload['parent_id']} does not exist")
        cursor.execute(
            f"UPDATE ontology_branches SET {', '.join(set_clauses)} "
            "WHERE id = %s "
            "RETURNING id, parent_id, label, color, short, icon_key, matchers, sensors, order_index",
            params,
        )
        row = dict(cursor.fetchone())
    ontology_bump_version(summary=f"branch updated: {branch_id}", changes={"op": "patch_branch", "id": branch_id, "fields": list(payload.keys())}, by=user.username)
    return _branch_row_to_dict(row)


@router.delete("/branches/{branch_id}")
def delete_ontology_branch(branch_id: str, force: bool = Query(False), user: SessionUser = Depends(require_admin)):
    with postgis_db.get_cursor(commit=True) as cursor:
        # Serialise on this branch id so concurrent admins counting then
        # deleting can't race with concurrent inserts of child branches or
        # detections referencing this branch. Released on transaction end.
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"ontology_branch_delete:{branch_id}",),
        )
        existing = _fetch_branch(cursor, branch_id)
        if not existing:
            raise HTTPException(status_code=404, detail="branch not found")
        cursor.execute(
            "SELECT count(*) AS c FROM detections "
            "WHERE metadata->>'branch_id' = %s",
            (branch_id,),
        )
        affected = int(cursor.fetchone()["c"])

        cursor.execute(
            "SELECT count(*) AS c FROM ontology_branches WHERE parent_id = %s",
            (branch_id,),
        )
        child_branches = int(cursor.fetchone()["c"])
        if child_branches > 0:
            raise HTTPException(
                status_code=409,
                detail={"error": "branch_has_children", "child_branches": child_branches},
            )

        if affected > 0 and not force:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "branch_has_detections",
                    "affected_detections": affected,
                    "hint": "retry with ?force=true to reassign to Other",
                },
            )

        if affected > 0 and force:
            cursor.execute(
                "SELECT class AS label, "
                "       coalesce(metadata->>'layer', '') AS layer, "
                "       count(*) AS c "
                "FROM detections "
                "WHERE metadata->>'branch_id' = %s "
                "GROUP BY class, coalesce(metadata->>'layer', '')",
                (branch_id,),
            )
            for r in cursor.fetchall():
                lbl = (r["label"] or "").strip()
                if not lbl:
                    continue
                cursor.execute(
                    "INSERT INTO ontology_unknown_labels (label, layer, count) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (label) DO UPDATE SET "
                    "  count = ontology_unknown_labels.count + EXCLUDED.count, "
                    "  last_seen = now(), "
                    "  layer = COALESCE(NULLIF(EXCLUDED.layer, ''), ontology_unknown_labels.layer)",
                    (lbl, r["layer"] or None, int(r["c"])),
                )
            cursor.execute(
                """
                UPDATE detections SET metadata =
                    (coalesce(metadata, '{}'::jsonb)
                     - 'icon_key' - 'canonical_label' - 'ontology_object_id')
                    || jsonb_build_object('branch_id', 'Other',
                                          'icon_key', 'circle_help')
                WHERE metadata->>'branch_id' = %s
                """,
                (branch_id,),
            )

        cursor.execute("DELETE FROM ontology_branches WHERE id = %s", (branch_id,))

    ontology_bump_version(
        summary=f"branch deleted: {branch_id}",
        changes={"op": "delete_branch", "id": branch_id, "affected_detections": affected},
        by=user.username,
    )
    return {"deleted": 1, "affected_detections": affected}


# ─── Objects CRUD ──────────────────────────────────────────────────────

@router.post("/objects", status_code=201)
def create_ontology_object(body: OntologyObjectIn, user: SessionUser = Depends(require_admin)):
    oid = (body.id or "").strip()
    bid = (body.branch_id or "").strip()
    if not oid:
        raise HTTPException(status_code=400, detail="id is required")
    if not bid:
        raise HTTPException(status_code=400, detail="branch_id is required")
    if not (body.label or "").strip():
        raise HTTPException(status_code=400, detail="label is required")
    if not (body.prompt or "").strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    sensors_json = json.dumps(body.sensors if body.sensors is not None else ["optical"])
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("SELECT 1 FROM ontology_branches WHERE id = %s", (bid,))
        if not cursor.fetchone():
            raise HTTPException(status_code=400, detail=f"branch_id {bid} does not exist")
        cursor.execute("SELECT 1 FROM ontology_objects WHERE id = %s", (oid,))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail=f"object {oid} already exists")
        cursor.execute(
            "INSERT INTO ontology_objects "
            "(id, branch_id, label, prompt, sensors, min_gsd_meters, icon_key, order_index) "
            "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s) "
            "RETURNING id, branch_id, label, prompt, sensors, min_gsd_meters, icon_key, order_index",
            (
                oid, bid, body.label, body.prompt, sensors_json,
                body.min_gsd_meters, body.icon_key,
                int(body.order_index or 0),
            ),
        )
        row = dict(cursor.fetchone())
    ontology_bump_version(summary=f"object created: {oid}", changes={"op": "create_object", "id": oid, "branch_id": bid}, by=user.username)
    return _object_row_to_dict(row)


@router.patch("/objects/{object_id}")
def patch_ontology_object(object_id: str, body: OntologyObjectPatch, user: SessionUser = Depends(require_admin)):
    payload = body.dict(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="no fields to update")
    set_clauses: list[str] = []
    params: list = []
    for key, val in payload.items():
        if key == "sensors":
            set_clauses.append("sensors = %s::jsonb")
            params.append(json.dumps(val if val is not None else []))
        elif key == "order_index":
            set_clauses.append("order_index = %s")
            params.append(int(val or 0))
        else:
            set_clauses.append(f"{key} = %s")
            params.append(val)
    set_clauses.append("updated_at = now()")
    params.append(object_id)
    with postgis_db.get_cursor(commit=True) as cursor:
        existing = _fetch_object(cursor, object_id)
        if not existing:
            raise HTTPException(status_code=404, detail="object not found")
        if "branch_id" in payload and payload["branch_id"]:
            cursor.execute("SELECT 1 FROM ontology_branches WHERE id = %s", (payload["branch_id"],))
            if not cursor.fetchone():
                raise HTTPException(status_code=400, detail=f"branch_id {payload['branch_id']} does not exist")
        cursor.execute(
            f"UPDATE ontology_objects SET {', '.join(set_clauses)} "
            "WHERE id = %s "
            "RETURNING id, branch_id, label, prompt, sensors, min_gsd_meters, icon_key, order_index",
            params,
        )
        row = dict(cursor.fetchone())
    ontology_bump_version(summary=f"object updated: {object_id}", changes={"op": "patch_object", "id": object_id, "fields": list(payload.keys())}, by=user.username)
    return _object_row_to_dict(row)


@router.delete("/objects/{object_id}")
def delete_ontology_object(object_id: str, user: SessionUser = Depends(require_admin)):
    with postgis_db.get_cursor(commit=True) as cursor:
        existing = _fetch_object(cursor, object_id)
        if not existing:
            raise HTTPException(status_code=404, detail="object not found")
        cursor.execute("DELETE FROM ontology_objects WHERE id = %s", (object_id,))
    ontology_bump_version(summary=f"object deleted: {object_id}", changes={"op": "delete_object", "id": object_id}, by=user.username)
    return {"deleted": 1}


# ─── Prompt profiles ───────────────────────────────────────────────────

@router.get("/prompt-profiles")
def list_prompt_profiles(user: SessionUser = Depends(get_current_user)):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cur:
        cur.execute(
            "SELECT id, sensor, name, version, prompts, current, notes, created_at, created_by "
            "FROM prompt_profiles ORDER BY sensor, created_at DESC"
        )
        rows = [dict(r) for r in cur.fetchall()]
    try:
        defaults = ontology_default_prompts()
    except Exception:
        defaults = {}
    return {"profiles": rows, "ontology_defaults": defaults}


@router.post("/prompt-profiles", status_code=201)
def create_prompt_profile(body: PromptProfileBody, user: SessionUser = Depends(require_admin)):
    ensure_platform_tables()
    if not (body.sensor or "").strip() or not (body.version or "").strip() or not (body.name or "").strip():
        raise HTTPException(status_code=400, detail="sensor, name and version are required")
    sensor = body.sensor.strip().lower()
    with postgis_db.get_cursor(commit=True) as cur:
        if body.make_current:
            cur.execute("UPDATE prompt_profiles SET current = FALSE WHERE sensor = %s", (sensor,))
        cur.execute(
            """
            INSERT INTO prompt_profiles (sensor, name, version, prompts, current, notes, created_by)
            VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)
            ON CONFLICT (sensor, version) DO UPDATE
              SET name = EXCLUDED.name,
                  prompts = EXCLUDED.prompts,
                  current = EXCLUDED.current,
                  notes = EXCLUDED.notes
            RETURNING id, sensor, name, version, prompts, current, notes, created_at, created_by
            """,
            (sensor, body.name, body.version, json.dumps(body.prompts), body.make_current, body.notes, user.username),
        )
        row = dict(cur.fetchone())
    return row


@router.put("/prompt-profiles/{profile_id}/activate")
def activate_prompt_profile(profile_id: int, user: SessionUser = Depends(require_admin)):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("SELECT sensor FROM prompt_profiles WHERE id = %s", (profile_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="profile not found")
        sensor = row["sensor"]
        cur.execute("UPDATE prompt_profiles SET current = FALSE WHERE sensor = %s", (sensor,))
        cur.execute(
            "UPDATE prompt_profiles SET current = TRUE WHERE id = %s RETURNING id, sensor, name, version, current",
            (profile_id,),
        )
        out = dict(cur.fetchone())
    return out


@router.delete("/prompt-profiles/{profile_id}")
def delete_prompt_profile(profile_id: int, user: SessionUser = Depends(require_admin)):
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM prompt_profiles WHERE id = %s RETURNING id", (profile_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="profile not found")
    return {"id": profile_id, "deleted": True}


# ─── Version history ───────────────────────────────────────────────────

@router.get("/version-history")
def get_version_history(limit: int = Query(100, ge=1, le=1000), user: SessionUser = Depends(get_current_user)):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cur:
        cur.execute(
            "SELECT id, version_id, summary, changes, detections_at_cut, created_at, created_by "
            "FROM ontology_version_history ORDER BY version_id DESC, id DESC LIMIT %s",
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT version_id FROM ontology_version LIMIT 1")
        cur_row = cur.fetchone()
        current = int(cur_row["version_id"]) if cur_row else None
    return {"current_version_id": current, "versions": rows}


# ─── Ontology Updates ──────────────────────────────────────────────────

@router.get("/updates")
def get_ontology_updates(limit: int = Query(8, ge=1, le=100), user: SessionUser = Depends(get_current_user)):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cur:
        cur.execute(
            "SELECT id, source_type, source_id, domain, status, summary, "
            "proposed_entities, proposed_relationships, context, error, created_at, updated_at "
            "FROM ontology_updates ORDER BY id DESC LIMIT %s",
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"updates": rows}

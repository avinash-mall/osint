"""AOI (Area Of Interest) CRUD + Neo4j mirror projection.

Before Phase 1.D the ``aois`` PostGIS table existed but had no HTTP write
path; only the worker read ``default_allegiance`` from it. This router
exposes a minimal CRUD so analysts can create AOIs from the UI and so the
graph projector has somewhere to hook: when ``metadata.aoi_kind`` is one of
``base`` / ``launchpoint`` / ``facility``, a matching Neo4j node is MERGEd.

See [docs/architecture/link-graph-redesign.md](../../docs/architecture/link-graph-redesign.md)
for the operational-entity model. The Neo4j helpers live in
[backend/graph_writes.py](../graph_writes.py).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database import postgis_db
from graph_writes import delete_site_for_aoi, merge_site_from_aoi
from platform_schema import ensure_platform_tables

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# request/response models (kept local — schemas.py is dense; only used here)
# ---------------------------------------------------------------------------


class AOICreate(BaseModel):
    """Body for ``POST /api/aois``. Geometry as GeoJSON Polygon."""

    name: str
    geometry: dict[str, Any] = Field(..., description="GeoJSON Polygon geometry")
    priority: str = "Medium"
    metadata: dict[str, Any] = Field(default_factory=dict)
    default_allegiance: str = "unknown"


class AOIUpdate(BaseModel):
    name: Optional[str] = None
    priority: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    default_allegiance: Optional[str] = None


def _row_to_dict(row: Any) -> dict[str, Any]:
    out = dict(row)
    metadata = out.get("metadata")
    if isinstance(metadata, str):
        try:
            out["metadata"] = json.loads(metadata)
        except json.JSONDecodeError:
            out["metadata"] = {}
    return out


def _maybe_centroid(geom_geojson: dict[str, Any]) -> tuple[float | None, float | None]:
    """Approximate the polygon centroid from GeoJSON coords (server-side avoids a round-trip).

    Returns ``(lat, lon)`` or ``(None, None)`` if the geometry is malformed.
    Adequate for the Neo4j mirror node — the spatial source of truth stays
    in PostGIS.
    """
    try:
        coords = geom_geojson.get("coordinates")
        if not coords:
            return None, None
        # Polygon: coordinates = [exterior_ring, ...holes]
        ring = coords[0] if isinstance(coords[0][0], list) else coords
        xs = [pt[0] for pt in ring]
        ys = [pt[1] for pt in ring]
        if not xs or not ys:
            return None, None
        return sum(ys) / len(ys), sum(xs) / len(xs)
    except (TypeError, IndexError, ValueError):
        return None, None


def _project_aoi_to_graph(aoi_row: dict[str, Any]) -> Optional[str]:
    """If the AOI carries ``aoi_kind``, MERGE the matching Neo4j mirror node.

    Returns the Neo4j ``elementId`` of the mirror, or ``None`` if no projection
    was applicable.
    """
    metadata = aoi_row.get("metadata") or {}
    kind = metadata.get("aoi_kind")
    if not kind:
        return None
    lat = aoi_row.get("centroid_lat")
    lon = aoi_row.get("centroid_lon")
    return merge_site_from_aoi(
        aoi_postgis_id=aoi_row["id"],
        kind=kind,
        name=aoi_row.get("name") or f"aoi-{aoi_row['id']}",
        latitude=lat,
        longitude=lon,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.get("/api/aois")
def list_aois(limit: int = 200):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, name, priority, metadata, default_allegiance, created_at,
                   ST_AsGeoJSON(geom) AS geometry,
                   ST_Y(ST_Centroid(geom)) AS centroid_lat,
                   ST_X(ST_Centroid(geom)) AS centroid_lon
            FROM aois
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall()
    out = []
    for row in rows:
        record = _row_to_dict(row)
        if record.get("geometry"):
            try:
                record["geometry"] = json.loads(record["geometry"])
            except json.JSONDecodeError:
                record["geometry"] = None
        out.append(record)
    return {"aois": out, "count": len(out)}


@router.get("/api/aois/{aoi_id}")
def get_aoi(aoi_id: int):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, name, priority, metadata, default_allegiance, created_at,
                   ST_AsGeoJSON(geom) AS geometry,
                   ST_Y(ST_Centroid(geom)) AS centroid_lat,
                   ST_X(ST_Centroid(geom)) AS centroid_lon
            FROM aois
            WHERE id = %s
            """,
            (aoi_id,),
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="AOI not found")
    record = _row_to_dict(row)
    if record.get("geometry"):
        try:
            record["geometry"] = json.loads(record["geometry"])
        except json.JSONDecodeError:
            record["geometry"] = None
    return record


@router.post("/api/aois")
def create_aoi(body: AOICreate):
    ensure_platform_tables()
    if body.geometry.get("type") != "Polygon":
        raise HTTPException(status_code=400, detail="geometry must be a GeoJSON Polygon")

    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            """
            INSERT INTO aois (name, priority, geom, metadata, default_allegiance)
            VALUES (%s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s, %s)
            RETURNING id, name, priority, metadata, default_allegiance, created_at,
                      ST_Y(ST_Centroid(geom)) AS centroid_lat,
                      ST_X(ST_Centroid(geom)) AS centroid_lon
            """,
            (
                body.name,
                body.priority,
                json.dumps(body.geometry),
                json.dumps(body.metadata),
                body.default_allegiance,
            ),
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=500, detail="failed to insert AOI")

    aoi_row = _row_to_dict(row)
    # Carry the parsed geometry back so the client doesn't need a second fetch.
    aoi_row["geometry"] = body.geometry
    # Fall back to client-side centroid if PostGIS lat/lon are NULL (shouldn't be).
    if aoi_row.get("centroid_lat") is None or aoi_row.get("centroid_lon") is None:
        lat, lon = _maybe_centroid(body.geometry)
        aoi_row.setdefault("centroid_lat", lat)
        aoi_row.setdefault("centroid_lon", lon)

    graph_id = _project_aoi_to_graph(aoi_row)
    return {"success": True, "aoi": aoi_row, "graph_node_id": graph_id}


@router.patch("/api/aois/{aoi_id}")
def update_aoi(aoi_id: int, body: AOIUpdate):
    ensure_platform_tables()
    updates: list[str] = []
    params: list[Any] = []
    if body.name is not None:
        updates.append("name = %s")
        params.append(body.name)
    if body.priority is not None:
        updates.append("priority = %s")
        params.append(body.priority)
    if body.metadata is not None:
        updates.append("metadata = %s")
        params.append(json.dumps(body.metadata))
    if body.default_allegiance is not None:
        updates.append("default_allegiance = %s")
        params.append(body.default_allegiance)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")

    params.append(aoi_id)
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            f"""
            UPDATE aois
            SET {', '.join(updates)}
            WHERE id = %s
            RETURNING id, name, priority, metadata, default_allegiance, created_at,
                      ST_Y(ST_Centroid(geom)) AS centroid_lat,
                      ST_X(ST_Centroid(geom)) AS centroid_lon
            """,
            tuple(params),
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="AOI not found")
    aoi_row = _row_to_dict(row)

    # Re-project: if aoi_kind is now set (or changed), MERGE; if it was cleared,
    # remove the mirror.
    metadata = aoi_row.get("metadata") or {}
    if metadata.get("aoi_kind"):
        _project_aoi_to_graph(aoi_row)
    else:
        delete_site_for_aoi(aoi_postgis_id=aoi_id)

    return {"success": True, "aoi": aoi_row}


@router.delete("/api/aois/{aoi_id}")
def delete_aoi(aoi_id: int):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("DELETE FROM aois WHERE id = %s RETURNING id", (aoi_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="AOI not found")

    removed = delete_site_for_aoi(aoi_postgis_id=aoi_id)
    return {"success": True, "id": aoi_id, "graph_nodes_removed": removed}

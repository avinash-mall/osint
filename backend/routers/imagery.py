"""Satellite-imagery catalog routes + basemap countries fallback + change endpoint."""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth import SessionUser, require_admin
from cascade_delete import affected_track_ids, purge_empty_tracks, purge_object_details
from change_detection import compute_change
from database import db, postgis_db
from geometry import parse_bbox
from imagery_metadata import native_max_zoom

logger = logging.getLogger(__name__)
router = APIRouter()


class ChangeRequest(BaseModel):
    before_pass_id: int
    after_pass_id: int
    # "diff" (optical, default) or "sar_logratio" (Sentinel-1 dB log-ratio).
    method: str = "diff"


@router.post("/api/imagery/change")
def post_imagery_change(body: ChangeRequest):
    """Raster-diff change detection between two satellite passes.

    Returns a GeoJSON FeatureCollection of changed regions plus a summary
    block (bounds, threshold, peak_diff, changed_pixels). ``404`` when the
    two passes share no spatial overlap or either pass is missing.
    """
    if body.before_pass_id == body.after_pass_id:
        raise HTTPException(status_code=400, detail="before/after passes must differ")
    try:
        result = compute_change(body.before_pass_id, body.after_pass_id, body.method)
    except Exception as exc:  # rasterio I/O / projection failures
        logger.exception("change_detection failed for %s vs %s", body.before_pass_id, body.after_pass_id)
        raise HTTPException(status_code=503, detail=f"change detection unavailable: {exc}")
    if result is None:
        raise HTTPException(status_code=404, detail="no spatial overlap between passes")
    return result


@router.get("/api/imagery")
def get_imagery(
    bbox: Optional[str] = Query(None, description="min_lon,min_lat,max_lon,max_lat"),
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    sensor_type: Optional[str] = None,
):
    """Query satellite passes from the PostGIS catalog."""
    query = """
        SELECT id, name, file_path, sensor_type, acquisition_time, cloud_cover,
               ST_AsGeoJSON(footprint) as footprint_geojson, crs, metadata,
               source_hash, source_filename, created_at, updated_at
        FROM satellite_passes
        WHERE 1=1
    """
    params: list = []

    if bbox:
        min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
        query += " AND ST_Intersects(footprint, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
        params.extend([min_lon, min_lat, max_lon, max_lat])

    if start_time:
        query += " AND acquisition_time >= %s"
        params.append(start_time)
    if end_time:
        query += " AND acquisition_time <= %s"
        params.append(end_time)
    if sensor_type:
        query += " AND sensor_type = %s"
        params.append(sensor_type)

    query += " ORDER BY acquisition_time DESC"

    with postgis_db.get_cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()

    imagery = []
    for r in rows:
        row = dict(r)
        # Per-pass WebMercatorQuad native-zoom ceiling, derived from the COG's
        # GSD. The map's SAT TileLayer caps `maxNativeZoom` here so high-GSD
        # passes can be inspected tight without TiTiler upsampling tiles that
        # exist only above the COG's real resolution.
        row["native_max_zoom"] = native_max_zoom(row.get("metadata") or {})
        imagery.append(row)
    return {"imagery": imagery}


@router.get("/api/imagery/{pass_id}/tiles")
def get_imagery_tiles(pass_id: int):
    """Return the TiTiler tile-URL template for a given satellite pass."""
    with postgis_db.get_cursor() as cursor:
        cursor.execute("SELECT file_path FROM satellite_passes WHERE id = %s", (pass_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Satellite pass not found")

        titiler_url = os.getenv("PUBLIC_TITILER_URL", "/tiles")
        tile_url = f"{titiler_url}/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}?url={row['file_path']}"
        return {"pass_id": pass_id, "tile_url": tile_url, "file_path": row["file_path"]}


@router.delete("/api/imagery/{pass_id}")
def delete_imagery(pass_id: int, user: SessionUser = Depends(require_admin)):
    """Hard-delete a satellite pass: its detections + the pass row (PostGIS), the
    COG file on disk, and the matching Neo4j SatellitePass/Detection nodes.

    Mirrors ``worker.clear_existing_detections``' PostGIS+Neo4j cascade. File and
    graph cleanup are best-effort so a half-missing artifact still frees the row.
    """
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("SELECT file_path FROM satellite_passes WHERE id = %s", (pass_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Satellite pass not found")
        file_path = row["file_path"]
        cursor.execute("SELECT id FROM detections WHERE pass_id = %s", (pass_id,))
        det_ids = [r["id"] for r in cursor.fetchall()]
        # Capture track membership before the cascade removes the member rows.
        track_ids = affected_track_ids(cursor, det_ids)
        cursor.execute("DELETE FROM detections WHERE pass_id = %s", (pass_id,))
        cursor.execute("DELETE FROM satellite_passes WHERE id = %s", (pass_id,))
        # FK cascades cleared candidates + track members; purge the rows no FK
        # reaches: analyst object_details and now-empty parent tracks.
        purge_object_details(cursor, "detection", det_ids)
        purge_empty_tracks(cursor, track_ids)

    if file_path:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            logger.warning("delete_imagery: could not remove %s", file_path, exc_info=True)

    try:
        with db.get_session() as neo:
            if det_ids:
                neo.run(
                    "MATCH (d:Detection) WHERE d.postgis_id IN $ids DETACH DELETE d",
                    {"ids": det_ids},
                )
            neo.run(
                "MATCH (sp:SatellitePass {postgis_id: $pid}) DETACH DELETE sp",
                {"pid": pass_id},
            )
    except Exception:  # noqa: BLE001 — graph cleanup must not fail the delete
        logger.warning("delete_imagery: Neo4j cleanup failed for pass %s", pass_id, exc_info=True)

    return {"id": pass_id, "deleted": True, "detections_removed": len(det_ids)}


@router.get("/api/imagery/{pass_id}/bands")
def get_imagery_bands(pass_id: int):
    with postgis_db.get_cursor() as cursor:
        cursor.execute("SELECT file_path, sensor_type FROM satellite_passes WHERE id = %s", (pass_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Satellite pass not found")

    try:
        import rasterio

        with rasterio.open(row["file_path"]) as src:
            stats = []
            for index in range(1, min(src.count, 8) + 1):
                band = src.read(index, masked=True)
                stats.append({
                    "band": index,
                    "dtype": str(band.dtype),
                    "min": float(band.min()) if band.count() else None,
                    "max": float(band.max()) if band.count() else None,
                    "mean": float(band.mean()) if band.count() else None,
                })
            return {
                "pass_id": pass_id,
                "sensor_type": row["sensor_type"],
                "band_count": src.count,
                "crs": str(src.crs),
                "width": src.width,
                "height": src.height,
                "statistics": stats,
                "render_modes": ["rgb", "single", "ndvi", "ndwi", "nbr", "sar_db", "thermal_k"],
            }
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Unable to inspect imagery bands: {exc}")


@router.get("/api/basemap/countries")
def get_basemap_countries():
    """Natural Earth countries layer as a GeoJSON FeatureCollection.

    Backs the offline globe overlay when Martin's vector-tile layer is not
    in use.
    """
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT jsonb_build_object(
                'type', 'FeatureCollection',
                'features', coalesce(jsonb_agg(jsonb_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(geom)::jsonb,
                    'properties', jsonb_build_object('name', name, 'admin', admin, 'iso_a3', iso_a3)
                )), '[]'::jsonb)
            ) AS geojson
            FROM ne_countries
        """)
        row = cursor.fetchone()
        return row["geojson"] if row else {"type": "FeatureCollection", "features": []}


@router.get("/api/dossier")
def get_area_dossier(
    lat: float = Query(..., description="latitude"),
    lon: float = Query(..., description="longitude"),
):
    """Offline right-click area dossier for a map point.

    Resolves the country at (lat, lon) by point-in-polygon over the locally-baked
    ``ne_countries`` table (name / admin / ISO3 / pop / GDP), and counts Sentinel's
    own detections nearby. No internet, no Wikipedia/Wikidata — everything comes
    from data already on the box, so it works air-gapped (Hard rule #8). This is
    the offline analogue of ShadowBroker's online country dossier.
    """
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT name, admin, iso_a3, pop_est, gdp_md_est
            FROM ne_countries
            WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            LIMIT 1
            """,
            (lon, lat),
        )
        country = cursor.fetchone()

        # Detections within 25 km of the click — a quick "what have we seen here".
        cursor.execute(
            """
            SELECT count(*) AS n
            FROM detections
            WHERE deleted_at IS NULL
              AND ST_DWithin(
                    geom::geography,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                    25000)
            """,
            (lon, lat),
        )
        det = cursor.fetchone()

    return {
        "point": {"lat": lat, "lon": lon},
        "country": dict(country) if country else None,
        "detections_within_25km": int(det["n"]) if det else 0,
        "source": "ne_countries (offline)",
    }

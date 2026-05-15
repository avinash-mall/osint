"""Satellite-imagery catalog routes + basemap countries fallback."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from database import postgis_db
from geometry import parse_bbox

router = APIRouter()


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
        return {"imagery": [dict(r) for r in rows]}


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

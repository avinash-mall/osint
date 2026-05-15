"""Analytics routes: change, viewshed, LOS, routes, POL, job list.

All five POST endpoints return placeholder GeoJSON fixtures — the platform
ships without a real raster-analytics backend. They exist so the frontend
can wire its analytics tab against a stable contract; replace the bodies
with real implementations as those features come online.
"""

from __future__ import annotations

import json
import math

from fastapi import APIRouter

from database import postgis_db
from events import publish_event
from geometry import make_square_feature
from platform_schema import ensure_platform_tables
from schemas import AnalyticsRequest

router = APIRouter()


def _store_analytics_result(job_type: str, req: dict, result: dict) -> dict:
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO analytics_jobs (job_type, status, input, result)
            VALUES (%s, 'complete', %s, %s)
            RETURNING id, job_type, status, input, result, created_at
        """, (job_type, json.dumps(req), json.dumps(result)))
        job = dict(cursor.fetchone())
    publish_event("analytics", {"type": "analytics_complete", "job": job})
    publish_event("ops", {"type": "analytics_complete", "job": job})
    return job


@router.post("/api/analytics/change")
def run_change_detection(req: AnalyticsRequest):
    center = req.observer or {"latitude": 25.078, "longitude": 55.179}
    lat = float(center.get("latitude", center.get("lat", 25.078)))
    lon = float(center.get("longitude", center.get("lon", 55.179)))
    features = [
        make_square_feature(lon - 0.018, lat + 0.012, 0.012, {"score": 0.82, "label": "new construction"}),
        make_square_feature(lon + 0.015, lat - 0.01, 0.009, {"score": 0.64, "label": "surface disturbance"}),
    ]
    result = {"type": "FeatureCollection", "features": features, "mode": "offline_fixture"}
    return {"job": _store_analytics_result("change", req.dict(), result), "result": result}


@router.post("/api/analytics/viewshed")
def run_viewshed(req: AnalyticsRequest):
    observer = req.observer or {"latitude": 25.078, "longitude": 55.179}
    lat = float(observer.get("latitude", observer.get("lat", 25.078)))
    lon = float(observer.get("longitude", observer.get("lon", 55.179)))
    radius = float(req.radius_m or 5000)
    points = []
    for idx in range(0, 361, 12):
        angle = math.radians(idx)
        scale = (0.65 + 0.35 * abs(math.sin(angle * 2.7))) * radius / 111_000
        points.append([lon + math.cos(angle) * scale, lat + math.sin(angle) * scale])
    result = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [points]}, "properties": {"radius_m": radius, "mode": "offline_fixture"}}],
    }
    return {"job": _store_analytics_result("viewshed", req.dict(), result), "result": result}


@router.post("/api/analytics/los")
def run_los(req: AnalyticsRequest):
    observer = req.observer or {"latitude": 25.078, "longitude": 55.179}
    destination = req.destination or {"latitude": 25.12, "longitude": 55.22}
    coords = [
        [float(observer.get("longitude", observer.get("lon", 55.179))), float(observer.get("latitude", observer.get("lat", 25.078)))],
        [float(destination.get("longitude", destination.get("lon", 55.22))), float(destination.get("latitude", destination.get("lat", 25.12)))],
    ]
    result = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": {"visible": True, "clearance_m": 42.0}}],
    }
    return {"job": _store_analytics_result("los", req.dict(), result), "result": result}


@router.post("/api/analytics/routes")
def run_route_options(req: AnalyticsRequest):
    observer = req.observer or {"latitude": 25.078, "longitude": 55.179}
    destination = req.destination or {"latitude": 25.276987, "longitude": 55.296249}
    start = [float(observer.get("longitude", observer.get("lon", 55.179))), float(observer.get("latitude", observer.get("lat", 25.078)))]
    end = [float(destination.get("longitude", destination.get("lon", 55.296249))), float(destination.get("latitude", destination.get("lat", 25.276987)))]
    routes = []
    for idx, offset in enumerate([-0.03, 0.0, 0.03], start=1):
        mid = [(start[0] + end[0]) / 2 + offset, (start[1] + end[1]) / 2 - offset / 2]
        routes.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [start, mid, end]},
            "properties": {"option": idx, "risk": ["least exposure", "shortest", "least risk"][idx - 1], "duration_minutes": 68 + idx * 7},
        })
    result = {"type": "FeatureCollection", "features": routes}
    return {"job": _store_analytics_result("routes", req.dict(), result), "result": result}


@router.post("/api/analytics/pol")
def run_pattern_of_life(req: AnalyticsRequest):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat, count(*) AS count
            FROM track_points
            WHERE geom IS NOT NULL
            GROUP BY ST_SnapToGrid(geom, 0.02), lon, lat
            ORDER BY count DESC
            LIMIT 50
        """)
        rows = cursor.fetchall()
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]}, "properties": {"count": row["count"]}}
        for row in rows
    ] or [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [55.179, 25.078]}, "properties": {"count": 7, "mode": "offline_fixture"}}
    ]
    result = {"type": "FeatureCollection", "features": features}
    return {"job": _store_analytics_result("pol", req.dict(), result), "result": result}


@router.get("/api/analytics/jobs")
def list_analytics_jobs(limit: int = 100):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, job_type, status, input, result, created_at
            FROM analytics_jobs
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,))
        return {"jobs": [dict(row) for row in cursor.fetchall()]}

"""Analytics routes: change, viewshed, LOS, routes, POL, job list.

Viewshed, LOS, and Routes use real terrain/graph data when a DEM
(``DEM_PATH`` → ``/data/dem/dem.tif``) and routing graph
(``ROUTING_GRAPH_PATH`` → ``/data/routing/graph.pkl``) are present. When
either resource is absent the endpoints return the offline GeoJSON fixtures
the frontend was originally wired against, with ``mode: "fixture_no_dem"``
or ``mode: "fixture_no_graph"`` on the result so the UI can warn.

Change and POL remain as the previous module — POL already issues real
PostGIS queries against ``track_points``; change is still a fixture pending
a real raster differencer.
"""

from __future__ import annotations

import json
import logging
import math

from fastapi import APIRouter

from database import postgis_db
from events import publish_event
from geometry import make_square_feature
from platform_schema import ensure_platform_tables
from schemas import AnalyticsRequest
from terrain import dem_available, line_of_sight, viewshed as compute_viewshed
from routing import compute_routes, graph_available

logger = logging.getLogger(__name__)
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


def _observer_lat_lon(payload: dict | None, default_lat: float, default_lon: float) -> tuple[float, float]:
    payload = payload or {}
    lat = float(payload.get("latitude", payload.get("lat", default_lat)))
    lon = float(payload.get("longitude", payload.get("lon", default_lon)))
    return lat, lon


@router.post("/api/analytics/change")
def run_change_detection(req: AnalyticsRequest):
    lat, lon = _observer_lat_lon(req.observer, 25.078, 55.179)
    features = [
        make_square_feature(lon - 0.018, lat + 0.012, 0.012, {"score": 0.82, "label": "new construction"}),
        make_square_feature(lon + 0.015, lat - 0.01, 0.009, {"score": 0.64, "label": "surface disturbance"}),
    ]
    result = {"type": "FeatureCollection", "features": features, "mode": "offline_fixture"}
    return {"job": _store_analytics_result("change", req.dict(), result), "result": result}


def _viewshed_fixture(lat: float, lon: float, radius: float) -> dict:
    points = []
    for idx in range(0, 361, 12):
        angle = math.radians(idx)
        scale = (0.65 + 0.35 * abs(math.sin(angle * 2.7))) * radius / 111_000
        points.append([lon + math.cos(angle) * scale, lat + math.sin(angle) * scale])
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [points]},
            "properties": {"radius_m": radius, "mode": "fixture_no_dem"},
        }],
        "mode": "fixture_no_dem",
    }


@router.post("/api/analytics/viewshed")
def run_viewshed(req: AnalyticsRequest):
    lat, lon = _observer_lat_lon(req.observer, 25.078, 55.179)
    radius = float(req.radius_m or 5000)
    observer_height_m = float(req.observer_height_m if req.observer_height_m is not None else 1.8)
    target_height_m = float(req.target_height_m if req.target_height_m is not None else 0.0)

    real = None
    if dem_available():
        try:
            real = compute_viewshed(
                lat, lon,
                radius_m=radius,
                observer_height_m=observer_height_m,
                target_height_m=target_height_m,
            )
        except Exception as exc:
            logger.warning("viewshed: DEM ray-cast failed (%s), falling back to fixture", exc)
            real = None
    if real is None:
        result = _viewshed_fixture(lat, lon, radius)
    else:
        result = {**real, "mode": "dem"}
    return {"job": _store_analytics_result("viewshed", req.dict(), result), "result": result}


def _los_fixture(obs: tuple[float, float], dst: tuple[float, float]) -> dict:
    coords = [[obs[1], obs[0]], [dst[1], dst[0]]]
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"visible": True, "clearance_m": 42.0, "mode": "fixture_no_dem"},
        }],
        "mode": "fixture_no_dem",
    }


@router.post("/api/analytics/los")
def run_los(req: AnalyticsRequest):
    obs_lat, obs_lon = _observer_lat_lon(req.observer, 25.078, 55.179)
    dst_lat, dst_lon = _observer_lat_lon(req.destination, 25.12, 55.22)
    observer_height_m = float(req.observer_height_m if req.observer_height_m is not None else 1.8)
    target_height_m = float(req.target_height_m if req.target_height_m is not None else 0.0)

    real = None
    if dem_available():
        try:
            real = line_of_sight(
                obs_lat, obs_lon, dst_lat, dst_lon,
                observer_height_m=observer_height_m,
                target_height_m=target_height_m,
            )
        except Exception as exc:
            logger.warning("los: DEM ray-cast failed (%s), falling back to fixture", exc)
            real = None

    if real is None:
        result = _los_fixture((obs_lat, obs_lon), (dst_lat, dst_lon))
    else:
        line_feature = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[obs_lon, obs_lat], [dst_lon, dst_lat]],
            },
            "properties": {
                "visible": real["visible"],
                "clearance_m": real["clearance_m"],
                "blocking_points": len(real["blocking_points"]),
                "mode": "dem",
            },
        }
        features = [line_feature]
        if real["blocking_points"]:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "MultiPoint",
                    "coordinates": [[b["lon"], b["lat"]] for b in real["blocking_points"]],
                },
                "properties": {"role": "obstruction", "count": len(real["blocking_points"])},
            })
        result = {"type": "FeatureCollection", "features": features, "mode": "dem"}
    return {"job": _store_analytics_result("los", req.dict(), result), "result": result}


def _routes_fixture(obs: tuple[float, float], dst: tuple[float, float]) -> dict:
    start = [obs[1], obs[0]]
    end = [dst[1], dst[0]]
    routes = []
    for idx, offset in enumerate([-0.03, 0.0, 0.03], start=1):
        mid = [(start[0] + end[0]) / 2 + offset, (start[1] + end[1]) / 2 - offset / 2]
        routes.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [start, mid, end]},
            "properties": {
                "option": idx,
                "risk": ["least exposure", "shortest", "least risk"][idx - 1],
                "duration_minutes": 68 + idx * 7,
                "mode": "fixture_no_graph",
            },
        })
    return {"type": "FeatureCollection", "features": routes, "mode": "fixture_no_graph"}


@router.post("/api/analytics/routes")
def run_route_options(req: AnalyticsRequest):
    obs_lat, obs_lon = _observer_lat_lon(req.observer, 25.078, 55.179)
    dst_lat, dst_lon = _observer_lat_lon(req.destination, 25.276987, 55.296249)

    real_features = None
    if graph_available():
        try:
            real_features = compute_routes(
                obs_lat, obs_lon, dst_lat, dst_lon,
                strategy=req.strategy,
            )
        except Exception as exc:
            logger.warning("routes: graph routing failed (%s), falling back to fixture", exc)
            real_features = None

    if not real_features:
        result = _routes_fixture((obs_lat, obs_lon), (dst_lat, dst_lon))
    else:
        result = {"type": "FeatureCollection", "features": real_features, "mode": "graph"}
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


@router.get("/api/analytics/capabilities")
def analytics_capabilities():
    """Reports whether real DEM and routing-graph backends are wired up so the
    UI can show a warning chip when results fall back to fixtures."""
    return {
        "dem": dem_available(),
        "routing_graph": graph_available(),
    }


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

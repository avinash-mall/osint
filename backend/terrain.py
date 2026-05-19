"""DEM-backed terrain helpers for the analytics router.

Provides ray-cast viewshed and line-of-sight against a single GeoTIFF DEM
mounted at ``DEM_PATH`` (defaults to ``/data/dem/dem.tif``). Earth curvature
is applied with the standard k=0.13 atmospheric-refraction adjustment.

The module is intentionally pure-Python with numpy + rasterio so it can be
unit-tested without a Celery worker, and degrades gracefully when the DEM is
missing — callers receive ``None`` and should surface an unavailable state
unless an explicit demo-fixture mode was requested.
"""

from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import rasterio
    from rasterio.features import shapes as rio_shapes
    from rasterio.transform import rowcol
except Exception:  # pragma: no cover - rasterio is in requirements
    rasterio = None  # type: ignore[assignment]
    rio_shapes = None  # type: ignore[assignment]
    rowcol = None  # type: ignore[assignment]


EARTH_RADIUS_M = 6_371_008.8
REFRACTION_K = 0.13  # standard atmospheric refraction coefficient


def dem_path() -> Path:
    return Path(os.getenv("DEM_PATH", "/data/dem/dem.tif"))


def dem_available() -> bool:
    return rasterio is not None and dem_path().exists()


@lru_cache(maxsize=1)
def _open_dem():  # type: ignore[no-untyped-def]
    if not dem_available():
        return None
    return rasterio.open(dem_path())


def reset_dem_cache() -> None:
    _open_dem.cache_clear()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _meters_per_degree(lat: float) -> tuple[float, float]:
    rlat = math.radians(lat)
    m_per_deg_lat = 111_132.954 - 559.822 * math.cos(2 * rlat) + 1.175 * math.cos(4 * rlat)
    m_per_deg_lon = (math.pi / 180) * EARTH_RADIUS_M * math.cos(rlat)
    return m_per_deg_lat, m_per_deg_lon


def _curvature_drop_m(distance_m: float) -> float:
    """Apparent elevation drop due to earth curvature with refraction correction."""
    if distance_m <= 0:
        return 0.0
    return (1 - REFRACTION_K) * (distance_m ** 2) / (2 * EARTH_RADIUS_M)


def sample_elevation(lat: float, lon: float) -> Optional[float]:
    src = _open_dem()
    if src is None:
        return None
    try:
        row, col = rowcol(src.transform, lon, lat)
    except Exception:
        return None
    if row < 0 or col < 0 or row >= src.height or col >= src.width:
        return None
    band = src.read(1, window=((row, row + 1), (col, col + 1)))
    if band.size == 0:
        return None
    val = float(band[0, 0])
    if src.nodata is not None and val == src.nodata:
        return None
    if not np.isfinite(val):
        return None
    return val


def line_of_sight(
    obs_lat: float,
    obs_lon: float,
    tgt_lat: float,
    tgt_lon: float,
    *,
    observer_height_m: float = 1.8,
    target_height_m: float = 0.0,
    samples: int = 128,
) -> Optional[dict]:
    """Walk a great-circle path between observer and target, sampling the DEM.

    Returns a dict with:
        visible           bool
        clearance_m       minimum clearance along the path (negative means blocked)
        blocking_points   list of {lat, lon, distance_m, elevation_m, los_m}
    Returns ``None`` when no DEM is available.
    """
    if not dem_available():
        return None

    obs_elev = sample_elevation(obs_lat, obs_lon)
    tgt_elev = sample_elevation(tgt_lat, tgt_lon)
    if obs_elev is None or tgt_elev is None:
        return None

    obs_h = obs_elev + observer_height_m
    tgt_h = tgt_elev + target_height_m
    total_m = haversine_m(obs_lat, obs_lon, tgt_lat, tgt_lon)
    if total_m <= 0:
        return {"visible": True, "clearance_m": 0.0, "blocking_points": []}

    min_clearance = math.inf
    blocking: list[dict] = []
    for k in range(1, samples):
        f = k / samples
        lat = obs_lat + (tgt_lat - obs_lat) * f
        lon = obs_lon + (tgt_lon - obs_lon) * f
        ground = sample_elevation(lat, lon)
        if ground is None:
            continue
        d = total_m * f
        los_h = obs_h + (tgt_h - obs_h) * f
        clearance = los_h - (ground + _curvature_drop_m(d))
        if clearance < min_clearance:
            min_clearance = clearance
        if clearance < 0:
            blocking.append({
                "lat": lat,
                "lon": lon,
                "distance_m": d,
                "elevation_m": ground,
                "los_m": los_h,
                "clearance_m": clearance,
            })
    visible = not blocking
    return {
        "visible": visible,
        "clearance_m": float(min_clearance) if math.isfinite(min_clearance) else 0.0,
        "blocking_points": blocking,
    }


def viewshed(
    observer_lat: float,
    observer_lon: float,
    *,
    radius_m: float,
    observer_height_m: float = 1.8,
    target_height_m: float = 0.0,
    azimuth_step_deg: float = 2.0,
) -> Optional[dict]:
    """Ray-cast viewshed: shoots radial sightlines and records the farthest
    visible point along each azimuth. The visible region is returned as a
    GeoJSON Polygon connecting those points.

    Returns ``None`` when the DEM is unavailable.
    """
    if not dem_available():
        return None

    obs_elev = sample_elevation(observer_lat, observer_lon)
    if obs_elev is None:
        return None
    obs_h = obs_elev + observer_height_m

    m_per_deg_lat, m_per_deg_lon = _meters_per_degree(observer_lat)
    src = _open_dem()
    if src is None:
        return None
    # Step along each ray at roughly the DEM resolution (use the larger of the
    # two pixel dimensions, in meters, as the step). Default to 60m if we
    # cannot determine.
    try:
        px_lon = abs(src.transform.a)
        px_lat = abs(src.transform.e)
        step_m = max(30.0, max(px_lon * m_per_deg_lon, px_lat * m_per_deg_lat))
    except Exception:
        step_m = 60.0
    steps = max(1, int(radius_m / step_m))

    boundary: list[list[float]] = []
    visible_count = 0
    for az_deg in np.arange(0.0, 360.0, azimuth_step_deg):
        az = math.radians(az_deg)
        max_slope = -math.inf
        last_visible_lat = observer_lat
        last_visible_lon = observer_lon
        for s in range(1, steps + 1):
            d = s * step_m
            dlat = math.cos(az) * d / m_per_deg_lat
            dlon = math.sin(az) * d / m_per_deg_lon
            lat = observer_lat + dlat
            lon = observer_lon + dlon
            ground = sample_elevation(lat, lon)
            if ground is None:
                break
            effective = ground + target_height_m + _curvature_drop_m(d)
            slope = (effective - obs_h) / d
            if slope >= max_slope:
                max_slope = slope
                last_visible_lat = lat
                last_visible_lon = lon
                visible_count += 1
            # If the slope did not advance, intermediate points are blocked
            # — keep marching to find the eventual far-side ridge.
        boundary.append([last_visible_lon, last_visible_lat])

    if len(boundary) < 3:
        return None
    # Close the ring.
    boundary.append(boundary[0])
    polygon = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [boundary]},
        "properties": {
            "radius_m": radius_m,
            "observer_height_m": observer_height_m,
            "target_height_m": target_height_m,
            "rays": int(360.0 / azimuth_step_deg),
            "samples_per_ray": steps,
            "visible_sample_count": visible_count,
        },
    }
    return {"type": "FeatureCollection", "features": [polygon]}

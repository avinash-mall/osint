"""Real-world size estimation for detections sitting on a georeferenced raster.

The worker already computes per-pixel GSD in meters and the detection's
``geo_polygon`` (4-corner OBB in raster CRS units). ``estimate_size`` turns
those into length / width / area / orientation in metres + bearing from true
north, with a propagated edge-uncertainty bound that mirrors how
``position_uncertainty_m`` is derived in worker_legacy.py.
"""
from __future__ import annotations

import math
from typing import Any

import pyproj
from shapely.geometry import Polygon
from shapely.ops import transform as shapely_transform


def local_utm_crs(lon: float, lat: float) -> pyproj.CRS:
    # Clamp to UTM zones 1..60 — lon == 180 (or a slightly out-of-range value
    # from an upstream antimeridian wrap) would otherwise yield zone 61 and an
    # invalid EPSG 32661/32761.
    zone = min(60, max(1, int((lon + 180) // 6) + 1))
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return pyproj.CRS.from_epsg(epsg)


def _polygon_from_flat(coords: list[float]) -> Polygon | None:
    if not coords or len(coords) < 6 or len(coords) % 2 != 0:
        return None
    pts = list(zip(coords[0::2], coords[1::2]))
    poly = Polygon(pts)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty or poly.area <= 0:
        return None
    return poly


def _bearing_from_north_deg(dx: float, dy: float) -> float:
    """Bearing of vector (dx, dy) in projected metres (east, north), CW from north."""
    bearing = math.degrees(math.atan2(dx, dy))
    if bearing < 0:
        bearing += 360.0
    return bearing


def estimate_size(
    *,
    geo_polygon: list[float],
    crs: Any,
    pixel_width_m: float,
    pixel_height_m: float,
    mask_area_px: int,
    edge_uncertainty_px: float = 2.0,
) -> dict[str, Any] | None:
    poly = _polygon_from_flat(geo_polygon)
    if poly is None:
        return None

    crs_obj = pyproj.CRS.from_user_input(crs) if crs is not None else None

    if crs_obj is not None and crs_obj.is_geographic:
        centroid = poly.centroid
        utm = local_utm_crs(centroid.x, centroid.y)
        transformer = pyproj.Transformer.from_crs(crs_obj, utm, always_xy=True)
        projected = shapely_transform(transformer.transform, poly)
        source = "obb_geo_polygon_utm"
    else:
        projected = poly
        source = "obb_projected_native"

    if projected.is_empty or projected.area <= 0:
        return None

    rect = projected.minimum_rotated_rectangle
    if rect.is_empty or rect.area <= 0 or not hasattr(rect, "exterior"):
        return None

    ring = list(rect.exterior.coords)[:-1]
    if len(ring) < 4:
        return None

    edges: list[tuple[float, tuple[float, float]]] = []
    for i in range(4):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % 4]
        dx, dy = x2 - x1, y2 - y1
        edges.append((math.hypot(dx, dy), (dx, dy)))

    edges.sort(key=lambda e: e[0], reverse=True)
    length_m = float(edges[0][0])
    width_m = float(edges[-1][0])
    long_dx, long_dy = edges[0][1]
    orientation_deg = _bearing_from_north_deg(long_dx, long_dy)

    if mask_area_px and mask_area_px > 0:
        area_m2 = float(mask_area_px) * float(pixel_width_m) * float(pixel_height_m)
    else:
        area_m2 = float(projected.area)

    uncertainty_length = float(edge_uncertainty_px) * float(pixel_width_m)
    uncertainty_width = float(edge_uncertainty_px) * float(pixel_height_m)
    perimeter = 2.0 * (length_m + width_m)
    uncertainty_area = perimeter * float(edge_uncertainty_px) * max(float(pixel_width_m), float(pixel_height_m))

    return {
        "length_m": round(length_m, 3),
        "width_m": round(width_m, 3),
        "area_m2": round(area_m2, 3),
        "orientation_deg": round(orientation_deg, 2),
        "uncertainty": {
            "length_m": round(uncertainty_length, 3),
            "width_m": round(uncertainty_width, 3),
            "area_m2": round(uncertainty_area, 3),
        },
        "source": source,
    }

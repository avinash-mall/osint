"""Pure-geometry helpers shared by the API and the Celery worker.

Coordinate conventions are deliberately distinct:

* ``iou_xyxy``    — absolute pixel/world space, ``[x1, y1, x2, y2]``.
* ``iou_cxcywh``  — normalized 0..1 with center+size, ``[cx, cy, w, h]``.

``parse_bbox`` accepts the ``min_lon,min_lat,max_lon,max_lat`` query-string format
and raises ``fastapi.HTTPException`` on malformed input. The remaining helpers
build GeoJSON Features.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException


def parse_bbox(bbox: str) -> tuple[float, float, float, float]:
    """``"min_lon,min_lat,max_lon,max_lat"`` → tuple. Raises HTTP 400 on bad input."""
    try:
        values = tuple(map(float, bbox.split(",")))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid bbox format. Use min_lon,min_lat,max_lon,max_lat")
    if len(values) != 4:
        raise HTTPException(status_code=400, detail="Invalid bbox format. Use min_lon,min_lat,max_lon,max_lat")
    min_lon, min_lat, max_lon, max_lat = values
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(status_code=400, detail="Invalid bbox extents")
    return min_lon, min_lat, max_lon, max_lat


def iou_xyxy(a: list[float], b: list[float]) -> float:
    """IoU between two ``[x1, y1, x2, y2]`` bboxes in the same coordinate space."""
    if len(a) != 4 or len(b) != 4:
        return 0.0
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_area = max(0, inter_x2 - inter_x1) * max(0, inter_y2 - inter_y1)
    if inter_area == 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter_area
    return inter_area / union if union else 0.0


def iou_cxcywh(a: list[float], b: list[float]) -> float:
    """IoU between two cxcywh-normalised bboxes; returns 0 for empty inputs."""
    if not a or not b or len(a) < 4 or len(b) < 4:
        return 0.0
    acx, acy, aw, ah = a[:4]
    bcx, bcy, bw, bh = b[:4]
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0
    ax1, ay1 = acx - aw / 2, acy - ah / 2
    ax2, ay2 = acx + aw / 2, acy + ah / 2
    bx1, by1 = bcx - bw / 2, bcy - bh / 2
    bx2, by2 = bcx + bw / 2, bcy + bh / 2
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def point_payload(payload: dict) -> tuple[Optional[float], Optional[float]]:
    """Extract ``(lat, lon)`` from a freeform payload that may use any of the
    common key spellings. Returns ``(None, None)`` when either is missing or
    non-numeric."""
    lat = payload.get("lat", payload.get("latitude"))
    lon = payload.get("lon", payload.get("lng", payload.get("longitude")))
    try:
        return (float(lat), float(lon)) if lat is not None and lon is not None else (None, None)
    except (TypeError, ValueError):
        return None, None


def make_square_feature(lon: float, lat: float, size_degrees: float, props: Optional[dict] = None) -> dict:
    """GeoJSON Feature for an axis-aligned square centered on ``(lon, lat)``."""
    half = size_degrees / 2
    coords = [[
        [lon - half, lat - half],
        [lon - half, lat + half],
        [lon + half, lat + half],
        [lon + half, lat - half],
        [lon - half, lat - half],
    ]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": coords}, "properties": props or {}}

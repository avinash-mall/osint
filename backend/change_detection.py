"""Raster-based change detection between two satellite passes.

Given two ``satellite_passes`` row IDs, opens both COGs with rasterio, resamples
to the intersection of their footprints on a common grid, computes a per-pixel
absolute difference (mean over bands), thresholds it, and polygonises the
resulting mask into GeoJSON features.

Designed to be cheap (CPU-only, single-thread) and bounded by
``CHANGE_DET_MAX_PIXELS``. Returns ``None`` if either pass is missing or has no
spatial overlap — the caller falls back to the fixture path.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np
import rasterio
from rasterio.features import shapes as rio_shapes
from rasterio.warp import Resampling, reproject
from rasterio.windows import from_bounds
from shapely.geometry import box, mapping, shape

from database import postgis_db

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


CHANGE_DET_THRESHOLD = _env_float("CHANGE_DET_THRESHOLD", 0.18)
CHANGE_DET_MAX_PIXELS = _env_int("CHANGE_DET_MAX_PIXELS", 1024 * 1024)  # 1 MP
CHANGE_DET_MIN_AREA_PX = _env_int("CHANGE_DET_MIN_AREA_PX", 64)
CHANGE_DET_SIMPLIFY_TOL = _env_float("CHANGE_DET_SIMPLIFY_TOLERANCE_DEG", 0.0002)


def _load_pass(pass_id: int) -> Optional[dict]:
    with postgis_db.get_cursor() as cur:
        cur.execute(
            """
            SELECT id, file_path, acquisition_time,
                   ST_XMin(footprint) AS min_lon, ST_YMin(footprint) AS min_lat,
                   ST_XMax(footprint) AS max_lon, ST_YMax(footprint) AS max_lat
            FROM satellite_passes
            WHERE id = %s
            """,
            (pass_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    record = dict(row)
    if not record.get("file_path") or not os.path.exists(record["file_path"]):
        logger.info("change_detection: pass %s file missing", pass_id)
        return None
    return record


def _resample_window(src_path: str, bounds: tuple[float, float, float, float], target_shape: tuple[int, int]) -> Optional[np.ndarray]:
    """Read an EPSG:4326 ``bounds`` window from ``src_path`` resampled to ``target_shape`` (h, w)."""
    height, width = target_shape
    with rasterio.open(src_path) as src:
        dst_transform = rasterio.transform.from_bounds(
            bounds[0], bounds[1], bounds[2], bounds[3], width, height
        )
        band_count = min(src.count, 3)  # cap at first 3 bands for speed
        dst = np.zeros((band_count, height, width), dtype=np.float32)
        for idx in range(band_count):
            try:
                reproject(
                    source=rasterio.band(src, idx + 1),
                    destination=dst[idx],
                    dst_transform=dst_transform,
                    dst_crs="EPSG:4326",
                    resampling=Resampling.bilinear,
                )
            except Exception as exc:
                logger.warning("change_detection: reproject band %s failed: %s", idx + 1, exc)
                return None
    return dst


def compute_change(before_id: int, after_id: int) -> Optional[dict]:
    if before_id == after_id:
        return None
    before = _load_pass(before_id)
    after = _load_pass(after_id)
    if not before or not after:
        return None

    before_box = box(before["min_lon"], before["min_lat"], before["max_lon"], before["max_lat"])
    after_box = box(after["min_lon"], after["min_lat"], after["max_lon"], after["max_lat"])
    intersection = before_box.intersection(after_box)
    if intersection.is_empty:
        logger.info("change_detection: pass %s and %s have no spatial overlap", before_id, after_id)
        return None

    min_lon, min_lat, max_lon, max_lat = intersection.bounds
    deg_w = max_lon - min_lon
    deg_h = max_lat - min_lat
    if deg_w <= 0 or deg_h <= 0:
        return None

    # Target resolution: pick the largest square shape that fits under the pixel cap.
    aspect = deg_w / deg_h
    max_side = int((CHANGE_DET_MAX_PIXELS / max(aspect, 1.0 / aspect)) ** 0.5)
    width = max(64, min(max_side, 1024))
    height = max(64, int(width / aspect))

    before_arr = _resample_window(before["file_path"], (min_lon, min_lat, max_lon, max_lat), (height, width))
    after_arr = _resample_window(after["file_path"], (min_lon, min_lat, max_lon, max_lat), (height, width))
    if before_arr is None or after_arr is None:
        return None

    diff = np.abs(after_arr.astype(np.float32) - before_arr.astype(np.float32))
    diff_mean = diff.mean(axis=0)
    peak = float(diff_mean.max()) or 1.0
    diff_norm = diff_mean / peak

    mask = (diff_norm >= CHANGE_DET_THRESHOLD).astype(np.uint8)
    if mask.sum() < CHANGE_DET_MIN_AREA_PX:
        return {
            "type": "FeatureCollection",
            "features": [],
            "mode": "raster_diff",
            "summary": {
                "before_pass_id": before_id,
                "after_pass_id": after_id,
                "bounds": [min_lon, min_lat, max_lon, max_lat],
                "threshold": CHANGE_DET_THRESHOLD,
                "peak_diff": peak,
                "changed_pixels": int(mask.sum()),
            },
        }

    transform = rasterio.transform.from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)
    features: list[dict] = []
    for geom_raw, value in rio_shapes(mask, transform=transform):
        if int(value) != 1:
            continue
        geom = shape(geom_raw)
        if geom.is_empty:
            continue
        geom_simplified = geom.simplify(CHANGE_DET_SIMPLIFY_TOL, preserve_topology=True)
        if geom_simplified.is_empty:
            continue
        # Compute mean diff inside the polygon's bounding box (cheap approximation).
        minx, miny, maxx, maxy = geom_simplified.bounds
        col_start = max(0, int((minx - min_lon) / (max_lon - min_lon) * width))
        col_end = min(width, int((maxx - min_lon) / (max_lon - min_lon) * width) + 1)
        row_start = max(0, int((max_lat - maxy) / (max_lat - min_lat) * height))
        row_end = min(height, int((max_lat - miny) / (max_lat - min_lat) * height) + 1)
        sub = diff_norm[row_start:row_end, col_start:col_end]
        score = float(sub.mean()) if sub.size else 0.0
        features.append({
            "type": "Feature",
            "geometry": mapping(geom_simplified),
            "properties": {
                "score": round(score, 4),
                "label": "raster_change",
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "mode": "raster_diff",
        "summary": {
            "before_pass_id": before_id,
            "after_pass_id": after_id,
            "bounds": [min_lon, min_lat, max_lon, max_lat],
            "threshold": CHANGE_DET_THRESHOLD,
            "peak_diff": peak,
            "changed_pixels": int(mask.sum()),
            "feature_count": len(features),
        },
    }

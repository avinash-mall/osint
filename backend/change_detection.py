"""Raster-based change detection between two satellite passes.

Given two ``satellite_passes`` row IDs, opens both COGs with rasterio, resamples
to the intersection of their footprints on a common grid, computes a per-pixel
change map, thresholds it, and polygonises the resulting mask into GeoJSON.

Two methods share the same resample → mask → polygonise spine:

* ``"diff"`` (default, optical) — normalised absolute difference (mean over
  the first ≤3 bands), thresholded as a fraction of the peak difference.
* ``"sar_logratio"`` — Sentinel-1 multi-temporal change: the dB log-ratio
  ``10·log10((after+ε)/(before+ε))`` on the VV band, despeckled, thresholded in
  dB. Sees flood / damage / disturbance through cloud and at night. Adapted in
  concept from ShadowBroker's SAR layer; clean-room implementation of the
  standard log-ratio formula. See docs/backend/change-detection-raster.md.

Designed to be cheap (CPU-only, single-thread) and bounded by
``CHANGE_DET_MAX_PIXELS``. Returns ``None`` if either pass is missing or has no
spatial overlap; callers should surface that as unavailable, not fabricate data.
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
from scipy import ndimage
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

# SAR log-ratio knobs. Threshold is in dB on |10·log10(after/before)|; a 3 dB
# change is ~2x backscatter, a robust default for flood/damage detection. The
# despeckle window suppresses single-pixel speckle before thresholding.
CHANGE_DET_SAR_THRESHOLD_DB = _env_float("CHANGE_DET_SAR_THRESHOLD_DB", 3.0)
CHANGE_DET_SAR_DESPECKLE = _env_int("CHANGE_DET_SAR_DESPECKLE", 3)


def _polygonize_mask(
    mask: np.ndarray, diff_norm: np.ndarray, bounds: tuple[float, float, float, float],
    width: int, height: int, *, label: str,
) -> list[dict]:
    """Vectorise a uint8 ``mask`` into simplified GeoJSON Features.

    Shared by every change method: each method produces ``mask`` (changed=1) and
    a 0..1 ``diff_norm`` magnitude map; this turns them into scored polygons.
    """
    min_lon, min_lat, max_lon, max_lat = bounds
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
            "properties": {"score": round(score, 4), "label": label},
        })
    return features


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


def _change_map_optical(before_arr: np.ndarray, after_arr: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Optical change: normalised mean absolute difference over bands.

    Returns ``(diff_norm 0..1, mask uint8, threshold)``.
    """
    diff = np.abs(after_arr.astype(np.float32) - before_arr.astype(np.float32))
    diff_mean = diff.mean(axis=0)
    peak = float(diff_mean.max()) or 1.0
    diff_norm = diff_mean / peak
    mask = (diff_norm >= CHANGE_DET_THRESHOLD).astype(np.uint8)
    return diff_norm, mask, peak


def _change_map_sar(before_arr: np.ndarray, after_arr: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """SAR multi-temporal change via the dB log-ratio on the VV (first) band.

    ``ratio_dB = 10·log10((after+ε)/(before+ε))`` flags both brightening
    (e.g. new structures, rough water) and darkening (e.g. flooding, which
    specularly reflects radar away). A light median despeckle suppresses speckle
    before the |dB| threshold. Returns ``(diff_norm 0..1, mask uint8, peak_dB)``.

    Clean-room implementation of the standard log-ratio operator; only band 1
    (VV) is used so it works whether the GRD is single- or dual-pol.
    """
    eps = 1e-3
    before_vv = np.clip(before_arr[0].astype(np.float32), 0.0, None)
    after_vv = np.clip(after_arr[0].astype(np.float32), 0.0, None)
    ratio_db = 10.0 * np.log10((after_vv + eps) / (before_vv + eps))
    if CHANGE_DET_SAR_DESPECKLE >= 2:
        ratio_db = ndimage.median_filter(ratio_db, size=CHANGE_DET_SAR_DESPECKLE)
    abs_db = np.abs(ratio_db)
    peak = float(abs_db.max()) or 1.0
    mask = (abs_db >= CHANGE_DET_SAR_THRESHOLD_DB).astype(np.uint8)
    # diff_norm in 0..1 for per-feature scoring, normalised by the peak magnitude.
    diff_norm = abs_db / peak
    return diff_norm, mask, peak


_METHODS = {"diff", "sar_logratio"}


def compute_change(before_id: int, after_id: int, method: str = "diff") -> Optional[dict]:
    if before_id == after_id:
        return None
    if method not in _METHODS:
        method = "diff"
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

    bounds = (min_lon, min_lat, max_lon, max_lat)
    before_arr = _resample_window(before["file_path"], bounds, (height, width))
    after_arr = _resample_window(after["file_path"], bounds, (height, width))
    if before_arr is None or after_arr is None:
        return None

    if method == "sar_logratio":
        diff_norm, mask, peak = _change_map_sar(before_arr, after_arr)
        mode = "sar_logratio"
        threshold = CHANGE_DET_SAR_THRESHOLD_DB
        label = "sar_change"
        peak_key, peak_val = "peak_diff_db", round(peak, 2)
    else:
        diff_norm, mask, peak = _change_map_optical(before_arr, after_arr)
        mode = "raster_diff"
        threshold = CHANGE_DET_THRESHOLD
        label = "raster_change"
        peak_key, peak_val = "peak_diff", peak

    summary = {
        "before_pass_id": before_id,
        "after_pass_id": after_id,
        "method": method,
        "bounds": [min_lon, min_lat, max_lon, max_lat],
        "threshold": threshold,
        peak_key: peak_val,
        "changed_pixels": int(mask.sum()),
    }

    if mask.sum() < CHANGE_DET_MIN_AREA_PX:
        return {"type": "FeatureCollection", "features": [], "mode": mode, "summary": summary}

    features = _polygonize_mask(mask, diff_norm, bounds, width, height, label=label)
    summary["feature_count"] = len(features)
    return {"type": "FeatureCollection", "features": features, "mode": mode, "summary": summary}

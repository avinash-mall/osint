import os
import sys
import json
import requests
import subprocess
import uuid
import logging
import math
import concurrent.futures
import tempfile
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from celery import Celery

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from database import db, postgis_db
import rasterio
from rasterio.io import MemoryFile
from rasterio.windows import Window
from shapely.geometry import Polygon, MultiPolygon
import numpy as np
from PIL import Image
from imagery_metadata import extract_raster_metadata
from detection_policy import active_detection_policy, detection_decision, parent_class_for_label
from threat_assessment import assess_detection_threat, clean_detection_class, conservative_detection_ontology
from ontology import normalize as ontology_normalize
import provider_lifecycle

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
INFERENCE_SAM3_URL = os.getenv("INFERENCE_SAM3_URL", "http://inference-sam3:8001")
IMAGERY_PATH = os.getenv("IMAGERY_PATH", "/data/imagery")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


INFERENCE_SPEED_PROFILES = {
    "recall_review": {
        "chip_size": 1008,
        "overlap": 252,
        "max_chips": 0,
        "concurrency": 2,
    },
    "fast_review": {
        "chip_size": 1008,
        "overlap": 252,
        "max_chips": 256,
        "concurrency": 1,
    },
}
INFERENCE_SPEED_PROFILE = os.getenv("INFERENCE_SPEED_PROFILE", "recall_review").strip().lower()
if INFERENCE_SPEED_PROFILE not in INFERENCE_SPEED_PROFILES:
    INFERENCE_SPEED_PROFILE = "recall_review"
_INFERENCE_PROFILE_DEFAULTS = INFERENCE_SPEED_PROFILES[INFERENCE_SPEED_PROFILE]

MAX_INFERENCE_CHIPS = env_int("MAX_INFERENCE_CHIPS", _INFERENCE_PROFILE_DEFAULTS["max_chips"])
DEFAULT_INFERENCE_CHIP_SIZE = env_int("INFERENCE_CHIP_SIZE", _INFERENCE_PROFILE_DEFAULTS["chip_size"])
DEFAULT_INFERENCE_OVERLAP = env_int("INFERENCE_CHIP_OVERLAP", _INFERENCE_PROFILE_DEFAULTS["overlap"])
INFERENCE_CHIP_CONCURRENCY = max(1, env_int("INFERENCE_CHIP_CONCURRENCY", _INFERENCE_PROFILE_DEFAULTS["concurrency"]))
INFERENCE_CHIP_TIMEOUT_S = env_int("INFERENCE_CHIP_TIMEOUT_S", 120)
INFERENCE_MIN_VALID_CHIP_FRACTION = max(0.0, min(1.0, env_float("INFERENCE_MIN_VALID_CHIP_FRACTION", 0.01)))
INFERENCE_MIN_VALID_DETECTION_FRACTION = max(0.0, min(1.0, env_float("INFERENCE_MIN_VALID_DETECTION_FRACTION", 0.20)))
DETECTION_POLICY = active_detection_policy()
INFERENCE_MAX_PENDING_CHIPS = max(
    1,
    env_int("INFERENCE_MAX_PENDING_CHIPS", INFERENCE_CHIP_CONCURRENCY * 2),
)
INFERENCE_CHIP_SPOOL_MAX_BYTES = max(
    64 * 1024,
    env_int("INFERENCE_CHIP_SPOOL_MAX_BYTES", 4 * 1024 * 1024),
)

logger = logging.getLogger(__name__)

celery_app = Celery("sentinel_worker", broker=REDIS_URL, backend=REDIS_URL)


def ensure_worker_imagery_schema() -> None:
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("sentinel_platform_schema",))
        cursor.execute("ALTER TABLE satellite_passes ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'")
        cursor.execute("ALTER TABLE satellite_passes ADD COLUMN IF NOT EXISTS source_hash VARCHAR(64)")
        cursor.execute("ALTER TABLE satellite_passes ADD COLUMN IF NOT EXISTS source_filename VARCHAR(255)")
        cursor.execute("ALTER TABLE satellite_passes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_passes_source_time ON satellite_passes(source_hash, acquisition_time)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS detection_target_candidates (
                id SERIAL PRIMARY KEY,
                detection_id INTEGER REFERENCES detections(id) ON DELETE CASCADE,
                target_id VARCHAR(255) NOT NULL,
                target_name VARCHAR(255),
                score REAL DEFAULT 0,
                reason TEXT,
                status VARCHAR(50) DEFAULT 'pending',
                evidence JSONB DEFAULT '{}',
                reviewed_by VARCHAR(100),
                reviewed_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                UNIQUE (detection_id, target_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_detection_target_candidates_detection ON detection_target_candidates(detection_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_detection_target_candidates_target ON detection_target_candidates(target_id, status)")


def record_timeline_event(domain: str, event_type: str, title: str, payload: dict, occurred_at: str = None) -> None:
    try:
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute("""
                INSERT INTO timeline_events (domain, event_type, title, payload, occurred_at)
                VALUES (%s, %s, %s, %s, COALESCE(%s::timestamptz, NOW()))
            """, (domain, event_type, title, json.dumps(payload or {}, default=str), occurred_at))
    except Exception as exc:
        logger.warning("Failed to record timeline event: %s", exc)


import redis
_REDIS_POOL = None

def get_redis_client():
    global _REDIS_POOL
    if _REDIS_POOL is None:
        _REDIS_POOL = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)
    return redis.Redis(connection_pool=_REDIS_POOL)

def publish_event(topic: str, payload: dict) -> None:
    try:
        client = get_redis_client()
        client.publish(f"events:{topic}", json.dumps(payload, default=str))
    except Exception as e:
        logger.warning("Failed to publish %s event: %s", topic, e)


def update_upload_job(
    upload_id: str = None,
    file_path: str = None,
    status: str = None,
    metadata: dict = None,
    clear_metadata_keys: tuple[str, ...] = (),
) -> None:
    if not upload_id and not file_path:
        return

    clauses = []
    params = []
    if status:
        clauses.append("status = %s")
        params.append(status)
    metadata_expr = "coalesce(metadata, '{}'::jsonb)"
    metadata_params = []
    if metadata:
        metadata_expr = f"({metadata_expr} || %s::jsonb)"
        metadata_params.append(json.dumps(metadata, default=str))
    for key in clear_metadata_keys:
        metadata_expr = f"({metadata_expr} - %s)"
        metadata_params.append(key)
    if metadata or clear_metadata_keys:
        clauses.append(f"metadata = {metadata_expr}")
        params.extend(metadata_params)
    clauses.append("updated_at = NOW()")

    where = []
    if upload_id:
        where.append("upload_id = %s")
        params.append(upload_id)
    if file_path:
        where.append("file_path = %s")
        params.append(file_path)

    try:
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute(f"UPDATE upload_jobs SET {', '.join(clauses)} WHERE {' OR '.join(where)}", params)
    except Exception as exc:
        logger.warning("Failed to update upload job status: %s", exc)


def get_upload_job(upload_id: str = None) -> dict:
    if not upload_id:
        return {}
    try:
        with postgis_db.get_cursor() as cursor:
            cursor.execute("SELECT filename, metadata FROM upload_jobs WHERE upload_id = %s", (upload_id,))
            row = cursor.fetchone()
            return dict(row) if row else {}
    except Exception:
        logger.warning("Failed to fetch upload job %s", upload_id, exc_info=True)
        return {}


def report_progress(task, upload_id: str, file_path: str, stage: str, progress: int, message: str, extra: dict = None) -> None:
    progress = max(0, min(100, int(progress)))
    payload = {
        "upload_id": upload_id,
        "stage": stage,
        "progress": progress,
        "message": message,
        **(extra or {}),
    }
    update_upload_job(upload_id=upload_id, file_path=file_path, metadata=payload)
    if task:
        task.update_state(state="PROGRESS", meta=payload)
    publish_event("imagery", {"type": "imagery_progress", **payload})
    publish_event("ops", {"type": "imagery_progress", **payload})


def ensure_cog(input_path: str, output_path: str) -> str:
    """Convert any raster to Cloud Optimized GeoTIFF."""
    if input_path.endswith(".nc") or input_path.endswith(".netcdf"):
        # Handle NetCDF via rioxarray
        try:
            import rioxarray
            ds = rioxarray.open_rasterio(input_path)
            # If time dimension exists, take the first slice
            if "time" in ds.dims:
                ds = ds.isel(time=0)
            # If band dimension > 1 and we want RGB or single band
            if "band" in ds.dims and ds.sizes.get("band", 0) > 1:
                ds = ds.isel(band=0)
            ds.rio.to_raster(output_path, driver="COG", compress="DEFLATE")
            return output_path
        except Exception as e:
            raise RuntimeError(f"NetCDF conversion failed: {e}")
    else:
        # GeoTIFF / JP2 -> COG via GDAL
        cmd = [
            "gdal_translate",
            input_path,
            output_path,
            "-of", "COG",
            "-co", "COMPRESS=DEFLATE",
            "-co", "OVERVIEWS=AUTO",
            "-co", "BIGTIFF=IF_SAFER",
            "-co", "NUM_THREADS=ALL_CPUS",
        ]
        subprocess.run(cmd, check=True)
        return output_path


def get_raster_footprint(cog_path: str):
    """Extract bounding box as a Shapely Polygon in EPSG:4326."""
    with rasterio.open(cog_path) as src:
        bounds = src.bounds
        crs = src.crs
        # Reproject bounds to WGS84 if needed
        if crs and crs.to_string() != "EPSG:4326":
            from rasterio.warp import transform_bounds
            min_lon, min_lat, max_lon, max_lat = transform_bounds(
                crs, "EPSG:4326", bounds.left, bounds.bottom, bounds.right, bounds.top
            )
        else:
            min_lon, min_lat, max_lon, max_lat = bounds.left, bounds.bottom, bounds.right, bounds.top
        
        footprint = MultiPolygon([Polygon([
            (min_lon, min_lat),
            (min_lon, max_lat),
            (max_lon, max_lat),
            (max_lon, min_lat),
            (min_lon, min_lat)
        ])])
        return footprint, min_lon, min_lat, max_lon, max_lat


def resolve_input_path(image_url: str) -> str:
    """Resolve local, HTTP(S), or unsupported remote imagery references into a local file."""
    parsed = urlparse(image_url)
    incoming_dir = os.path.join(IMAGERY_PATH, "incoming")
    os.makedirs(incoming_dir, exist_ok=True)

    if parsed.scheme in ("http", "https"):
        filename = os.path.basename(parsed.path) or f"{uuid.uuid4()}.tif"
        input_path = os.path.join(incoming_dir, filename)
        with requests.get(image_url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with open(input_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        return input_path

    if parsed.scheme == "s3":
        raise RuntimeError("s3:// imagery ingestion requires an S3 client configuration and is not enabled.")

    if parsed.scheme and parsed.scheme != "file":
        raise RuntimeError(f"Unsupported imagery URL scheme: {parsed.scheme}")

    local_path = parsed.path if parsed.scheme == "file" else image_url
    if os.path.exists(local_path):
        return local_path

    filename = os.path.basename(local_path)
    input_path = os.path.join(incoming_dir, filename)
    if os.path.exists(input_path):
        return input_path

    raise FileNotFoundError(f"Imagery file not found: {image_url}")


def chip_to_uint8_rgb(chip: np.ndarray) -> np.ndarray:
    chip_rgb = chip[:3] if chip.shape[0] >= 3 else np.repeat(chip[:1], 3, axis=0)
    chip_rgb = np.nan_to_num(chip_rgb.astype("float32"), nan=0.0, posinf=0.0, neginf=0.0)
    if chip_rgb.dtype != np.uint8:
        low, high = np.percentile(chip_rgb, [2, 98])
        if high > low:
            chip_rgb = np.clip((chip_rgb - low) / (high - low) * 255, 0, 255).astype(np.uint8)
        else:
            chip_rgb = np.zeros_like(chip_rgb, dtype=np.uint8)
    return np.moveaxis(chip_rgb, 0, -1)


def valid_data_mask(src: rasterio.io.DatasetReader, window: Window) -> np.ndarray | None:
    """Return a boolean valid-data mask for a raster window, or None when the
    dataset does not expose no-data/alpha masking information."""
    try:
        mask = src.dataset_mask(window=window)
    except Exception:
        return None
    if mask is None:
        return None
    valid = np.asarray(mask) > 0
    if valid.size == 0:
        return None
    if np.all(valid):
        return None
    return valid


def clip_box_to_valid_mask(
    valid_mask: np.ndarray | None,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> tuple[float, float, float, float] | None:
    if valid_mask is None:
        return x1, y1, x2, y2
    height, width = valid_mask.shape[:2]
    if width <= 0 or height <= 0:
        return None

    center_x = int(min(width - 1, max(0, round((x1 + x2) / 2.0))))
    center_y = int(min(height - 1, max(0, round((y1 + y2) / 2.0))))
    if not bool(valid_mask[center_y, center_x]):
        return None

    ix1 = max(0, min(width, int(math.floor(x1))))
    iy1 = max(0, min(height, int(math.floor(y1))))
    ix2 = max(0, min(width, int(math.ceil(x2))))
    iy2 = max(0, min(height, int(math.ceil(y2))))
    if ix2 <= ix1 or iy2 <= iy1:
        return None

    box_mask = valid_mask[iy1:iy2, ix1:ix2]
    valid_count = int(np.count_nonzero(box_mask))
    if valid_count <= 0:
        return None
    valid_fraction = valid_count / max(1, box_mask.size)
    if valid_fraction < INFERENCE_MIN_VALID_DETECTION_FRACTION:
        return None

    valid_y, valid_x = np.nonzero(box_mask)
    clipped_x1 = float(ix1 + int(valid_x.min()))
    clipped_y1 = float(iy1 + int(valid_y.min()))
    clipped_x2 = float(ix1 + int(valid_x.max()) + 1)
    clipped_y2 = float(iy1 + int(valid_y.max()) + 1)
    if clipped_x2 <= clipped_x1 or clipped_y2 <= clipped_y1:
        return None
    return clipped_x1, clipped_y1, clipped_x2, clipped_y2


def bbox_iou(a: list[float], b: list[float]) -> float:
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


def polygon_iou(a: list[float], b: list[float]) -> float:
    if len(a) != 8 or len(b) != 8:
        return 0.0
    try:
        poly_a = Polygon(list(zip(a[0::2], a[1::2]))).buffer(0)
        poly_b = Polygon(list(zip(b[0::2], b[1::2]))).buffer(0)
        if poly_a.is_empty or poly_b.is_empty or not poly_a.is_valid or not poly_b.is_valid:
            return 0.0
        inter_area = poly_a.intersection(poly_b).area
        if inter_area <= 0:
            return 0.0
        union_area = poly_a.union(poly_b).area
        return float(inter_area / union_area) if union_area else 0.0
    except Exception:
        return 0.0


def detection_overlap(a: dict, b: dict) -> float:
    obb_iou = polygon_iou(a.get("pixel_obb", []), b.get("pixel_obb", []))
    if obb_iou > 0:
        return obb_iou
    return bbox_iou(a.get("pixel_bbox", []), b.get("pixel_bbox", []))


def clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def deduplicate_detections(
    detections: list,
    iou_threshold: float = 0.45,
) -> list:
    if not detections:
        return []

    raw = sorted(detections, key=lambda item: item.get("confidence", 0), reverse=True)
    kept = []
    buckets: dict[tuple[str, int, int], list[dict]] = {}
    bucket_size = 512

    for det in raw:
        if not det.get("pixel_bbox"):
            kept.append(det)
            continue

        x1, y1, x2, y2 = det["pixel_bbox"]
        cx = int(((x1 + x2) / 2) // bucket_size)
        cy = int(((y1 + y2) / 2) // bucket_size)
        det_class = det.get("parent_class") or det.get("class")
        nearby = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nearby.extend(buckets.get((det_class, cx + dx, cy + dy), []))

        overlap_kept = next(
            (existing for existing in nearby
             if detection_overlap(det, existing) >= iou_threshold),
            None,
        )
        if overlap_kept is None:
            det.setdefault("dedupe_method", "obb_nms")
            kept.append(det)
            buckets.setdefault((det_class, cx, cy), []).append(det)
    return kept


def sample_axis_indices(count: int, sample_count: int) -> list[int]:
    if count <= 0:
        return [0]
    if sample_count >= count:
        return list(range(count))
    if sample_count <= 1:
        return [count // 2]
    return sorted({round(index * (count - 1) / (sample_count - 1)) for index in range(sample_count)})


def plan_inference_grid(width: int, height: int, chip_size: int, overlap: int, max_chips: int) -> dict:
    step = max(1, chip_size - overlap)

    def axis_count(size: int) -> int:
        if size <= chip_size:
            return 1
        return max(1, math.ceil((size - chip_size) / step) + 1)

    x_count = axis_count(width)
    y_count = axis_count(height)
    source_total = x_count * y_count

    if max_chips <= 0 or source_total <= max_chips:
        x_indices = list(range(x_count))
        y_indices = list(range(y_count))
        sampled = False
    else:
        target_y = max(1, min(y_count, int(math.sqrt(max_chips * y_count / max(1, x_count)))))
        target_x = max(1, min(x_count, max_chips // target_y))
        while target_x * target_y > max_chips and target_y > 1:
            target_y -= 1
            target_x = max(1, min(x_count, max_chips // target_y))

        x_indices = sample_axis_indices(x_count, target_x)
        y_indices = sample_axis_indices(y_count, target_y)
        sampled = True

    return {
        "step": step,
        "x_indices": x_indices,
        "y_indices": y_indices,
        "source_total": source_total,
        "planned_total": max(1, len(x_indices) * len(y_indices)),
        "sampled": sampled,
        "max_chips": max_chips,
    }


def detection_ontology(det_class: str) -> dict:
    return conservative_detection_ontology(det_class)


def classify_detection_ontologies(detections: list, progress_callback=None) -> dict[str, dict]:
    grouped: dict[str, list[float]] = {}
    for det in detections:
        det_class = det.get("class", "Unknown")
        grouped.setdefault(det_class, []).append(float(det.get("confidence") or 0))

    ontology_by_class: dict[str, dict] = {}
    if not grouped:
        if progress_callback:
            progress_callback("classification", 94, "No detections found; skipping class labeling.")
        return ontology_by_class

    for det_class in grouped:
        ontology_by_class[det_class] = {
            **detection_ontology(det_class),
            "status": "deterministic",
        }
    if progress_callback:
        progress_callback("classification", 94, "Detection classes labeled with deterministic ontology rules.")
    return ontology_by_class


def _post_chip_to_sam3(
    session: requests.Session,
    chip_file,
    chip_meta_payload: str,
    chip_label: str,
) -> dict | None:
    """POST a single chip to SAM3 /detect. Returns response JSON or None on failure."""
    try:
        try:
            meta = json.loads(chip_meta_payload) if chip_meta_payload else {}
        except (TypeError, json.JSONDecodeError):
            meta = {}
        filename = meta.get("filename") or "chip.png"
        content_type = meta.get("content_type") or "image/png"
        chip_file.seek(0)
        resp = session.post(
            f"{INFERENCE_SAM3_URL}/detect",
            files={"image": (filename, chip_file, content_type)},
            data={"metadata": chip_meta_payload},
            timeout=INFERENCE_CHIP_TIMEOUT_S,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("[WORKER] sam3 inference failed on chip %s: %s", chip_label, exc)
        return None
    finally:
        chip_file.close()


def _png_file(rgb: np.ndarray):
    chip_file = tempfile.SpooledTemporaryFile(max_size=INFERENCE_CHIP_SPOOL_MAX_BYTES)
    Image.fromarray(rgb, mode="RGB").save(chip_file, format="PNG")
    chip_file.seek(0)
    return chip_file


def _geotiff_window_file(src: rasterio.io.DatasetReader, window: Window, indexes: list[int]):
    data = src.read(indexes=indexes, window=window).astype("float32", copy=False)
    transform = src.window_transform(window)
    profile = {
        "driver": "GTiff",
        "height": data.shape[1],
        "width": data.shape[2],
        "count": data.shape[0],
        "dtype": "float32",
        "transform": transform,
        "crs": src.crs,
    }
    with MemoryFile() as memfile:
        with memfile.open(**profile) as dst:
            dst.write(data)
            descriptions = src.descriptions or ()
            for out_index, src_index in enumerate(indexes, start=1):
                if src_index - 1 < len(descriptions) and descriptions[src_index - 1]:
                    dst.set_band_description(out_index, descriptions[src_index - 1])
        payload = memfile.read()
    chip_file = tempfile.SpooledTemporaryFile(max_size=INFERENCE_CHIP_SPOOL_MAX_BYTES)
    chip_file.write(payload)
    chip_file.seek(0)
    return chip_file


def _encode_bool_mask(mask: np.ndarray) -> dict:
    """Compact bool mask transport for inference services that need chip validity."""
    mask_bool = np.asarray(mask, dtype=bool)
    packed = np.packbits(mask_bool.reshape(-1).astype(np.uint8), bitorder="little")
    return {
        "shape": [int(mask_bool.shape[0]), int(mask_bool.shape[1])],
        "bitorder": "little",
        "data_b64": base64.b64encode(packed.tobytes()).decode("ascii"),
    }


def _emit_chip_payload(window: Window, src: rasterio.io.DatasetReader, *, valid_mask=None):
    """Return (fileobj, metadata) for a SAM3 chip upload.

    Multispectral (≥6-band) and SAR (2-band VV/VH) rasters go out as GeoTIFFs;
    everything else is encoded to a uint8 RGB PNG.
    """
    window_transform = src.window_transform(window)
    geo_meta = {
        "source_crs": src.crs.to_string() if src.crs else None,
        "chip_transform": list(window_transform.to_gdal()),
        "chip_transform_order": "gdal",
        "source_window": [int(window.col_off), int(window.row_off), int(window.width), int(window.height)],
        "source_bounds": list(src.window_bounds(window)),
    }
    valid_mask_meta = _encode_bool_mask(valid_mask) if valid_mask is not None else None
    descriptions = tuple((desc or "").strip().lower() for desc in (src.descriptions or ()))
    has_vv_vh = {"vv", "vh"}.issubset(set(descriptions))

    if src.count == 2 and has_vv_vh:
        meta = {
            "modality": "sar",
            "filename": "chip.tif",
            "content_type": "image/tiff",
            "geo": geo_meta,
            "sar_polarizations": ["VV", "VH"],
        }
        if valid_mask_meta:
            meta["valid_mask"] = valid_mask_meta
        return _geotiff_window_file(src, window, [1, 2]), meta

    if src.count >= 6:
        hls_timesteps = 3 if src.count >= 18 else None
        meta = {
            "modality": "multispectral",
            "filename": "chip.tif",
            "content_type": "image/tiff",
            "geo": geo_meta,
        }
        if hls_timesteps:
            meta["hls_timesteps"] = hls_timesteps
        if valid_mask_meta:
            meta["valid_mask"] = valid_mask_meta
        indexes = list(range(1, 19 if hls_timesteps else 7))
        return _geotiff_window_file(src, window, indexes), meta

    chip = src.read(window=window)
    chip_rgb = chip_to_uint8_rgb(chip)
    if valid_mask is not None:
        chip_rgb = chip_rgb.copy()
        chip_rgb[~valid_mask] = 0
    meta = {
        "modality": "rgb",
        "filename": "chip.png",
        "content_type": "image/png",
        "geo": geo_meta,
    }
    if valid_mask_meta:
        meta["valid_mask"] = valid_mask_meta
    return _png_file(chip_rgb), meta


def _parse_prompt_override(raw: object) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        prompts = [str(item).strip() for item in raw if str(item).strip()]
        return prompts or None
    text = str(raw).strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, list):
        prompts = [str(item).strip() for item in payload if str(item).strip()]
    else:
        prompts = [item.strip() for item in text.split(",") if item.strip()]
    return prompts or None


def slice_and_infer(
    cog_path: str,
    pass_id: int,
    chip_size: int = DEFAULT_INFERENCE_CHIP_SIZE,
    overlap: int = DEFAULT_INFERENCE_OVERLAP,
    max_chips: int = MAX_INFERENCE_CHIPS,
    progress_callback=None,
    inference_metadata: dict | None = None,
):
    """Slice COG into chips, send each to SAM3 /detect, dedupe and return detections."""
    inference_metadata = inference_metadata or {}
    with rasterio.open(cog_path) as src:
        width = src.width
        height = src.height
        transform = src.transform
        crs = src.crs
        
        detections = []
        
        grid = plan_inference_grid(width, height, chip_size, overlap, max_chips)
        step = grid["step"]
        total_windows = grid["planned_total"]
        processed_windows = 0
        failed_windows = 0
        last_reported_percent = None
        coverage_fraction = round(total_windows / max(1, grid["source_total"]), 4)
        inference_summary = {
            "chip_size": chip_size,
            "overlap": overlap,
            "step": step,
            "planned_chips": total_windows,
            "source_total_chips": grid["source_total"],
            "processed_chips": 0,
            "inference_speed_profile": INFERENCE_SPEED_PROFILE,
            "coverage_fraction": coverage_fraction,
            "sampling_enabled": grid["sampled"],
            "max_inference_chips": grid["max_chips"],
            "dedupe_method": "obb_nms",
            "threshold_profile": DETECTION_POLICY["threshold_profile"],
            "taxonomy_version": DETECTION_POLICY["taxonomy_version"],
            "model_version": DETECTION_POLICY["model_version"],
            "max_pending_chips": INFERENCE_MAX_PENDING_CHIPS,
            "chip_spool_max_bytes": INFERENCE_CHIP_SPOOL_MAX_BYTES,
        }

        if progress_callback:
            if grid["sampled"]:
                message = f"Large raster detected; sampling {total_windows} of {grid['source_total']} chips for inference."
            else:
                message = f"Prepared {total_windows} raster chips for inference."
            progress_callback(
                "inference",
                56,
                message,
                {
                    "planned_chips": total_windows,
                    "total_chips": total_windows,
                    "source_total_chips": grid["source_total"],
                    "processed_chips": 0,
                    "failed_chips": 0,
                    "inference_speed_profile": INFERENCE_SPEED_PROFILE,
                    "max_inference_chips": grid["max_chips"],
                    "sampling_enabled": grid["sampled"],
                    "coverage_fraction": coverage_fraction,
                },
            )

        # HTTP session shared across the chip ThreadPoolExecutor so connection
        # pooling actually engages (default requests.post opens a fresh TCP per
        # call). pool_maxsize must be >= concurrency or requests will warn and
        # silently drop connections.
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=INFERENCE_CHIP_CONCURRENCY * 2,
            pool_maxsize=INFERENCE_CHIP_CONCURRENCY * 2,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=INFERENCE_CHIP_CONCURRENCY,
            thread_name_prefix="chip-post",
        )
        pending: dict[concurrent.futures.Future, dict] = {}

        def _apply_chip_response(ctx: dict, inference_response: dict) -> None:
            x = ctx["x"]; y = ctx["y"]
            win_width = ctx["win_width"]; win_height = ctx["win_height"]
            valid_mask = ctx.get("valid_mask")
            chip_detections = []
            for det in inference_response.get("detections", []):
                det["model_version"] = (
                    inference_response.get("model_version")
                    or det.get("model_version")
                )
                det["taxonomy_version"] = (
                    inference_response.get("taxonomy_version")
                    or det.get("taxonomy_version")
                )
                det["model_versions"] = (
                    inference_response.get("model_versions")
                    or det.get("model_versions")
                )
                det["threshold_profile"] = (
                    inference_response.get("threshold_profile")
                    or det.get("threshold_profile")
                )
                chip_detections.append(det)

            for det in chip_detections:
                try:
                    cx, cy, w, h = [float(value) for value in det["bbox"][:4]]
                except (KeyError, TypeError, ValueError):
                    continue

                chip_px_cx = cx * win_width
                chip_px_cy = cy * win_height
                chip_px_w = max(0.0, w * win_width)
                chip_px_h = max(0.0, h * win_height)

                local_box = clip_box_to_valid_mask(
                    valid_mask,
                    chip_px_cx - chip_px_w / 2,
                    chip_px_cy - chip_px_h / 2,
                    chip_px_cx + chip_px_w / 2,
                    chip_px_cy + chip_px_h / 2,
                )
                if local_box is None:
                    continue
                local_x1, local_y1, local_x2, local_y2 = local_box

                abs_px_x1 = clamp_float(x + local_x1, 0, width)
                abs_px_y1 = clamp_float(y + local_y1, 0, height)
                abs_px_x2 = clamp_float(x + local_x2, 0, width)
                abs_px_y2 = clamp_float(y + local_y2, 0, height)
                if abs_px_x2 <= abs_px_x1 or abs_px_y2 <= abs_px_y1:
                    continue

                pixel_obb = []
                if det.get("obb") and len(det["obb"]) == 8:
                    for index, value in enumerate(det["obb"]):
                        if index % 2 == 0:
                            pixel_obb.append(clamp_float(x + float(value) * win_width, 0, width))
                        else:
                            pixel_obb.append(clamp_float(y + float(value) * win_height, 0, height))
                else:
                    pixel_obb = [
                        abs_px_x1, abs_px_y1,
                        abs_px_x2, abs_px_y1,
                        abs_px_x2, abs_px_y2,
                        abs_px_x1, abs_px_y2,
                    ]

                pixel_points = list(zip(pixel_obb[0::2], pixel_obb[1::2]))
                lons, lats = [], []
                for px, py in pixel_points:
                    lon, lat = transform * (px, py)
                    lons.append(lon)
                    lats.append(lat)

                if crs and crs.to_string() != "EPSG:4326":
                    from rasterio.warp import transform as rasterio_transform
                    lons, lats = rasterio_transform(crs, "EPSG:4326", lons, lats)

                geo_polygon = [coord for point in zip(lons, lats) for coord in point]
                lon1, lat1, lon2, lat2 = min(lons), min(lats), max(lons), max(lats)

                original_class = det.get("original_class") or det.get("class", "unknown")
                confidence = float(det.get("confidence") or det.get("calibrated_confidence") or 0.0)
                decision = detection_decision(original_class, confidence, DETECTION_POLICY)
                policy_review_status = decision["review_status"]
                # Open-vocab policy: drop only when the operator explicitly raised
                # GLOBAL_CONFIDENCE_FLOOR / PER_CLASS_CONFIDENCE_OVERRIDES above
                # this detection's confidence. Otherwise everything passes through.
                if decision["review_status"] == "below_class_threshold":
                    continue

                # Preserve the original SAM3 prompt as the canonical class. The
                # decision["parent_class"] is a coarse legacy bucket from naive
                # substring matching (e.g. "disturbed earth" → "bed" → "furniture")
                # and overwriting class with it discards information the
                # frontend defence-ontology classifier needs.
                det["class"] = decision["original_class"]
                det_model_version = det.get("model_version") or decision["model_version"]
                det_taxonomy_version = det.get("taxonomy_version") or decision["taxonomy_version"]
                det_threshold_profile = det.get("threshold_profile") or decision["threshold_profile"]
                det.update({**decision, **{
                    "model_version": det_model_version,
                    "taxonomy_version": det_taxonomy_version,
                    "threshold_profile": det_threshold_profile,
                    "policy_review_status": det.get("policy_review_status") or policy_review_status,
                }})
                det["pixel_bbox"] = [abs_px_x1, abs_px_y1, abs_px_x2, abs_px_y2]
                det["pixel_obb"] = pixel_obb
                det["geo_bbox"] = [lon1, lat1, lon2, lat2]
                det["geo_polygon"] = geo_polygon
                det["chip_id"] = f"{pass_id}:{x}:{y}:{win_width}:{win_height}"
                det["chip_window"] = [x, y, win_width, win_height]
                det["chip_valid_fraction"] = ctx.get("valid_fraction")
                det["coverage_fraction"] = coverage_fraction
                det["planned_chips"] = total_windows
                det["source_total_chips"] = grid["source_total"]
                det["sampling_enabled"] = grid["sampled"]
                det["dedupe_method"] = "obb_nms"
                detections.append(det)

        def _report_inference_progress() -> None:
            nonlocal last_reported_percent
            if not progress_callback:
                return
            inferred_percent = int(processed_windows / total_windows * 100)
            if (
                processed_windows == 1
                or processed_windows == total_windows
                or last_reported_percent is None
                or inferred_percent > last_reported_percent
            ):
                last_reported_percent = inferred_percent
                progress_callback(
                    "inference",
                    55 + int(inferred_percent * 0.35),
                    f"Running inference on raster chips ({processed_windows}/{total_windows}).",
                    {
                        "processed_chips": processed_windows,
                        "failed_chips": failed_windows,
                        "total_chips": total_windows,
                        "planned_chips": total_windows,
                        "source_total_chips": grid["source_total"],
                        "sampling_enabled": grid["sampled"],
                        "inference_speed_profile": INFERENCE_SPEED_PROFILE,
                        "coverage_fraction": coverage_fraction,
                    },
                )

        def _consume_one(fut: concurrent.futures.Future) -> None:
            nonlocal processed_windows, failed_windows
            ctx = pending.pop(fut)
            try:
                response = fut.result()
            except Exception as exc:
                failed_windows += 1
                processed_windows += 1
                logger.warning(
                    "[WORKER] Inference failed for chip pass=%s x=%s y=%s: %s",
                    pass_id, ctx["x"], ctx["y"], exc,
                )
                _report_inference_progress()
                return
            if not response:
                failed_windows += 1
                processed_windows += 1
                logger.warning(
                    "[WORKER] sam3 inference returned no response for chip pass=%s x=%s y=%s",
                    pass_id, ctx["x"], ctx["y"],
                )
                _report_inference_progress()
                return
            _apply_chip_response(ctx, response)
            processed_windows += 1
            _report_inference_progress()

        # Cap in-flight chips and spool oversized PNGs to disk so large rasters
        # cannot accumulate unbounded encoded chip buffers in memory.
        pending_limit = INFERENCE_MAX_PENDING_CHIPS

        try:
            for y_index in grid["y_indices"]:
                y = y_index * step
                for x_index in grid["x_indices"]:
                    x = x_index * step
                    win_width = min(chip_size, width - x)
                    win_height = min(chip_size, height - y)
                    window = Window(x, y, win_width, win_height)

                    valid_mask = valid_data_mask(src, window)
                    valid_fraction = (
                        float(np.count_nonzero(valid_mask)) / max(1, valid_mask.size)
                        if valid_mask is not None
                        else 1.0
                    )
                    if valid_fraction < INFERENCE_MIN_VALID_CHIP_FRACTION:
                        continue
                    chip = src.read(window=window)
                    if np.all(chip == 0) or (src.nodata is not None and np.all(chip == src.nodata)):
                        continue

                    chip_file, chip_meta = _emit_chip_payload(
                        window,
                        src,
                        valid_mask=valid_mask,
                    )
                    chip_meta_payload = json.dumps({
                        "pass_id": pass_id,
                        "window": [x, y, win_width, win_height],
                        **inference_metadata,
                        **chip_meta,
                    })
                    chip_label = f"pass={pass_id} x={x} y={y}"

                    future = executor.submit(
                        _post_chip_to_sam3,
                        session, chip_file, chip_meta_payload, chip_label,
                    )
                    del chip
                    pending[future] = {
                        "x": x, "y": y, "win_width": win_width, "win_height": win_height,
                        "valid_mask": valid_mask,
                        "valid_fraction": round(valid_fraction, 4),
                    }

                    while len(pending) >= pending_limit:
                        done, _ = concurrent.futures.wait(
                            list(pending.keys()),
                            return_when=concurrent.futures.FIRST_COMPLETED,
                        )
                        for fut in done:
                            _consume_one(fut)

            while pending:
                done, _ = concurrent.futures.wait(
                    list(pending.keys()),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for fut in done:
                    _consume_one(fut)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            session.close()
    
    deduped = deduplicate_detections(detections)
    inference_summary["processed_chips"] = processed_windows
    inference_summary["failed_chips"] = failed_windows
    inference_summary["raw_detections"] = len(detections)
    inference_summary["deduped_detections"] = len(deduped)
    inference_summary["suppressed_detections"] = max(0, len(detections) - len(deduped))
    return {"detections": deduped, "summary": inference_summary}


def store_detections(detections: list, pass_id: int, ontology_by_class: dict[str, dict] = None):
    """Store detections in PostGIS and create Neo4j nodes."""
    if not detections:
        return 0

    # Step 3 of /home/avinash/.claude/plans/the-inference-system-has-piped-nest.md:
    # Each detection now carries the new defence-ontology classification
    # (branch_id / icon_key / canonical_label / was_unknown / ontology_object_id)
    # in its metadata JSON. The `class` column itself is FROZEN as the raw
    # lowercase_underscore label from inference and the existing
    # `parent_class` field stays for backward compat (Step 7 makes
    # parent_class_for_label() a wrapper). The authoritative classification
    # going forward is `branch_id`. See backend/ontology.py::normalize().
    unknown_count = 0
    total_normalized = 0
    with postgis_db.get_cursor(commit=True) as cursor, db.get_session() as neo_session:
        for det in detections:
            lon1, lat1, lon2, lat2 = det["geo_bbox"]
            confidence = det.get("confidence", 0.0)
            det_class = det.get("class", "Unknown")
            original_class = det.get("original_class") or det_class
            parent_class = det.get("parent_class") or parent_class_for_label(original_class)
            decision = detection_decision(original_class, confidence, DETECTION_POLICY)
            # Defence-ontology normalization (Step 3). Falls back gracefully
            # when source_layer is missing — empty string is the documented default.
            ont = ontology_normalize(original_class, layer=det.get("source_layer", ""))
            total_normalized += 1
            if ont.was_unknown:
                unknown_count += 1
            ontology = (ontology_by_class or {}).get(det_class) or detection_ontology(det_class)
            assessment = assess_detection_threat(det_class, confidence=confidence, allegiance=det.get("allegiance", "unknown"))
            ontology = {
                **ontology,
                "original_class": original_class,
                "parent_class": parent_class,
                "threat_level": assessment["threat_level"],
                "threat_confidence": assessment["threat_confidence"],
                "assessment_status": assessment["assessment_status"],
                "evidence": assessment["evidence"],
                "category": assessment["category"],
            }
            pixel_bbox = det.get("pixel_bbox", [])
            geo_polygon = det.get("geo_polygon") or [lon1, lat1, lon1, lat2, lon2, lat2, lon2, lat1]
            
            # Create WKT polygons
            pairs = list(zip(geo_polygon[0::2], geo_polygon[1::2]))
            if pairs[0] != pairs[-1]:
                pairs.append(pairs[0])
            geom_wkt = "POLYGON((" + ", ".join(f"{lon} {lat}" for lon, lat in pairs) + "))"
            centroid_wkt = f"POINT({sum(lon for lon, _lat in pairs[:-1]) / max(1, len(pairs) - 1)} {sum(lat for _lon, lat in pairs[:-1]) / max(1, len(pairs) - 1)})"
            
            cursor.execute("""
                INSERT INTO detections (pass_id, class, confidence, geom, centroid, pixel_bbox, metadata)
                VALUES (%s, %s, %s, ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326), %s, %s)
                RETURNING id
            """, (
                pass_id,
                det_class,
                confidence,
                geom_wkt,
                centroid_wkt,
                json.dumps({"bbox": pixel_bbox, "obb": det.get("pixel_obb", [])}),
                json.dumps({
                    "source": "inference",
                    "chip_size": det.get("chip_window", [None, None, None, None])[2] or DEFAULT_INFERENCE_CHIP_SIZE,
                    "geo_polygon": geo_polygon,
                    "confidence": confidence,
                    "calibrated_confidence": det.get("calibrated_confidence", confidence),
                    "original_class": original_class,
                    "parent_class": parent_class,
                    # Step 3: defence-ontology fields. Step 5 surfaces these
                    # through the API; nothing reads them yet so this is
                    # backwards-compatible. branch_id is the authoritative
                    # classification going forward.
                    "branch_id": ont.branch_id,
                    "icon_key": ont.icon_key,
                    "canonical_label": ont.canonical_label,
                    "was_unknown": ont.was_unknown,
                    "ontology_object_id": ont.ontology_object_id,
                    "review_status": det.get("review_status") or decision["review_status"],
                    "policy_review_status": det.get("policy_review_status") or decision["review_status"],
                    "threshold_profile": det.get("threshold_profile") or DETECTION_POLICY["threshold_profile"],
                    "class_threshold": det.get("class_threshold") or decision["class_threshold"],
                    "model_version": det.get("model_version") or DETECTION_POLICY["model_version"],
                    "taxonomy_version": det.get("taxonomy_version") or DETECTION_POLICY["taxonomy_version"],
                    "chip_id": det.get("chip_id"),
                    "chip_window": det.get("chip_window"),
                    "chip_valid_fraction": det.get("chip_valid_fraction"),
                    "coverage_fraction": det.get("coverage_fraction"),
                    "planned_chips": det.get("planned_chips"),
                    "source_total_chips": det.get("source_total_chips"),
                    "sampling_enabled": det.get("sampling_enabled"),
                    "dedupe_method": det.get("dedupe_method", "obb_nms"),
                    "ontology": ontology,
                    "threat_level": assessment["threat_level"],
                    "threat_confidence": assessment["threat_confidence"],
                    "assessment_status": assessment["assessment_status"],
                    "evidence": assessment["evidence"],
                    "allegiance": det.get("allegiance", "unknown"),
                    "prompt_profile": det.get("prompt_profile"),
                    "prompt_chunk_index": det.get("prompt_chunk_index"),
                    "prompt_total_chunks": det.get("prompt_total_chunks"),
                    "prompt_text": det.get("prompt_text"),
                    "mask_rle": det.get("mask_rle"),
                    "obb": det.get("obb"),
                    "pixel_obb": det.get("pixel_obb"),
                    "obb_format": det.get("obb_format"),
                    "obb_source": det.get("obb_source"),
                    "obb_angle_deg": det.get("obb_angle_deg"),
                    "obb_area_px": det.get("obb_area_px"),
                    "edge_truncated": det.get("edge_truncated"),
                    "embedding": det.get("embedding"),
                    "prithvi_labels": det.get("prithvi_labels"),
                    "sar_proxy": det.get("sar_proxy"),
                    "terramind_embedding": det.get("terramind_embedding"),
                    "modality": det.get("modality"),
                    "task": det.get("task"),
                    "geo": det.get("geo"),
                    "area": det.get("area"),
                    "model_versions": det.get("model_versions"),
                })
            ))
            
            det_id = cursor.fetchone()["id"]
            
            neo_session.run("""
                MATCH (sp:SatellitePass {postgis_id: $pass_id})
                CREATE (d:Detection {
                    postgis_id: $det_id,
                    class: $det_class,
                    label: $label,
                    original_class: $original_class,
                    parent_class: $parent_class,
                    confidence: $confidence,
                    review_status: $review_status,
                    threshold_profile: $threshold_profile,
                    model_version: $model_version,
                    taxonomy_version: $taxonomy_version,
                    threat_level: $threat_level,
                    threat_confidence: $threat_confidence,
                    assessment_status: $assessment_status,
                    ontology_category: $ontology_category,
                    allegiance: $allegiance,
                    latitude: $lat,
                    longitude: $lon,
                    created_at: datetime()
                })
                CREATE (sp)-[:CONTAINS_DETECTION]->(d)
            """, {
                "pass_id": pass_id,
                "det_id": det_id,
                "det_class": det_class,
                "label": ontology["label"],
                "original_class": original_class,
                "parent_class": parent_class,
                "confidence": confidence,
                "review_status": det.get("review_status") or decision["review_status"],
                "threshold_profile": det.get("threshold_profile") or DETECTION_POLICY["threshold_profile"],
                "model_version": det.get("model_version") or DETECTION_POLICY["model_version"],
                "taxonomy_version": det.get("taxonomy_version") or DETECTION_POLICY["taxonomy_version"],
                "threat_level": assessment["threat_level"],
                "threat_confidence": assessment["threat_confidence"],
                "assessment_status": assessment["assessment_status"],
                "ontology_category": ontology["category"],
                "allegiance": det.get("allegiance", "unknown"),
                "lat": (lat1 + lat2) / 2,
                "lon": (lon1 + lon2) / 2
            })

    # Step 3: per-batch summary of how many labels could not be resolved by
    # the defence-ontology normalizer (branch_id == "Other" / icon == circle_help).
    if total_normalized:
        logger.info(
            "ontology.normalize: pass_id=%s normalized=%d unknown=%d",
            pass_id,
            total_normalized,
            unknown_count,
        )

    return len(detections)


def clear_existing_detections(pass_id: int) -> None:
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("SELECT id FROM detections WHERE pass_id = %s", (pass_id,))
        det_ids = [row["id"] for row in cursor.fetchall()]
        cursor.execute("DELETE FROM detections WHERE pass_id = %s", (pass_id,))

    if det_ids:
        with db.get_session() as neo_session:
            neo_session.run("""
                MATCH (d:Detection)
                WHERE d.postgis_id IN $det_ids
                DETACH DELETE d
            """, {"det_ids": det_ids})


def _xyxy_to_normalized_cxcywh(box: list[float], width: float | None = None, height: float | None = None) -> list[float]:
    x1, y1, x2, y2 = [float(v) for v in box[:4]]
    if width and height and width > 0 and height > 0:
        return [
            max(0.0, min(1.0, ((x1 + x2) / 2.0) / width)),
            max(0.0, min(1.0, ((y1 + y2) / 2.0) / height)),
            max(0.0, min(1.0, (x2 - x1) / width)),
            max(0.0, min(1.0, (y2 - y1) / height)),
        ]
    return [x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1)]


@celery_app.task(name="worker.process_fmv", queue="imagery")
def process_fmv(clip_id: int, video_path: str, text_prompts: list[str] | None = None, frame_stride: int = 1, max_frames: int | None = None) -> int:
    """Run SAM3 FMV tracking and persist one row per frame/track detection."""
    provider_lifecycle.ensure_running()
    payload = json.dumps({
        "video_path": video_path,
        "text_prompts": text_prompts or None,
        "frame_stride": frame_stride,
        "max_frames": max_frames,
        "modality": "fmv",
    })
    session = requests.Session()
    try:
        resp = session.post(
            f"{INFERENCE_SAM3_URL}/detect_video",
            data={"metadata": payload},
            stream=True,
            timeout=INFERENCE_CHIP_TIMEOUT_S * 60,
        )
        resp.raise_for_status()
        inserted = 0
        with postgis_db.get_cursor(commit=True) as cur:
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                entry = json.loads(line)
                cur.execute(
                    """
                    INSERT INTO fmv_detections (clip_id, frame_index, class, confidence, bbox, metadata)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        clip_id,
                        int(entry["frame_index"]),
                        entry.get("original_class") or entry.get("class") or "track",
                        float(entry.get("score") or 0.0),
                        json.dumps(_xyxy_to_normalized_cxcywh(entry.get("bbox_xyxy") or [0, 0, 0, 0])),
                        json.dumps({
                            "track_id": entry.get("track_id"),
                            "mask_rle": entry.get("mask_rle"),
                            "obb": entry.get("obb"),
                            "obb_format": entry.get("obb_format"),
                            "obb_source": entry.get("obb_source"),
                            "obb_angle_deg": entry.get("obb_angle_deg"),
                            "edge_truncated": entry.get("edge_truncated"),
                            "embedding": entry.get("embedding"),
                            "provider": "sam3",
                        }),
                    ),
                )
                inserted += 1
        provider_lifecycle.mark_active()
        return inserted
    finally:
        session.close()


def target_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    d_phi = np.radians(lat2 - lat1)
    d_lambda = np.radians(lon2 - lon1)
    a = np.sin(d_phi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(d_lambda / 2) ** 2
    return float(radius * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a)))


def target_class_compatibility(det_class: str, target_props: dict) -> tuple[float, str]:
    det_text = clean_detection_class(det_class).lower()
    target_text = " ".join(str(target_props.get(key, "")) for key in ("name", "type", "category", "description")).lower()
    if any(token in target_text for token in det_text.split() if len(token) >= 4):
        return 0.35, "class/name text overlap"
    category = conservative_detection_ontology(det_class).get("category")
    if category and category in target_text:
        return 0.3, "category overlap"
    return 0.15, "generic proximity match"


def generate_candidate_links_for_pass(pass_id: int, distance_threshold_meters: float = 1500.0) -> int:
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT d.id, d.class, d.confidence, ST_X(d.centroid) AS lon, ST_Y(d.centroid) AS lat
            FROM detections d
            WHERE d.pass_id = %s
        """, (pass_id,))
        rows = cursor.fetchall()

    try:
        with db.get_session() as session:
            result = session.run("""
                MATCH (t)
                WHERE 'Target' IN labels(t)
                WITH t, properties(t) AS props
                WHERE props.latitude IS NOT NULL
                  AND props.longitude IS NOT NULL
                RETURN elementId(t) AS element_id, props.id AS stable_id, props.name AS name,
                       props.latitude AS lat, props.longitude AS lon, props
            """)
            targets = [dict(record) for record in result]
    except Exception as exc:
        logger.warning("Unable to read targets for candidate links: %s", exc)
        targets = []

    created = 0
    with postgis_db.get_cursor(commit=True) as cursor:
        for det in rows:
            for target in targets:
                distance_m = target_distance_m(float(det["lat"]), float(det["lon"]), float(target["lat"]), float(target["lon"]))
                if distance_m > distance_threshold_meters:
                    continue
                compatibility, compatibility_reason = target_class_compatibility(det["class"], target.get("props") or {})
                distance_score = max(0.0, 1.0 - (distance_m / distance_threshold_meters)) * 0.45
                confidence_score = max(0.0, min(1.0, float(det["confidence"] or 0))) * 0.2
                score = round(distance_score + compatibility + confidence_score, 3)
                target_id = target.get("stable_id") or target["element_id"]
                evidence = {
                    "distance_m": round(distance_m, 2),
                    "compatibility_reason": compatibility_reason,
                    "detection_class": det["class"],
                    "detection_confidence": float(det["confidence"] or 0),
                }
                cursor.execute("""
                    INSERT INTO detection_target_candidates (detection_id, target_id, target_name, score, reason, status, evidence)
                    VALUES (%s, %s, %s, %s, %s, 'pending', %s)
                    ON CONFLICT (detection_id, target_id) DO UPDATE SET
                        target_name = EXCLUDED.target_name,
                        score = EXCLUDED.score,
                        reason = EXCLUDED.reason,
                        evidence = EXCLUDED.evidence,
                        updated_at = NOW()
                    RETURNING id
                """, (
                    det["id"],
                    target_id,
                    target.get("name") or target_id,
                    score,
                    f"{round(distance_m)}m from target; {compatibility_reason}; confidence {float(det['confidence'] or 0):.2f}",
                    json.dumps(evidence, default=str),
                ))
                if cursor.fetchone():
                    created += 1
    return created


def detection_class_summary(pass_id: int) -> list[dict]:
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT class,
                   COUNT(*)::int AS count,
                   COALESCE(AVG(confidence), 0)::float AS avg_confidence
            FROM detections
            WHERE pass_id = %s
            GROUP BY class
            ORDER BY COUNT(*) DESC, class ASC
        """, (pass_id,))
        return [dict(row) for row in cursor.fetchall()]


@celery_app.task(queue="imagery", bind=True)
def process_satellite_imagery(
    self,
    image_url: str,
    sensor_type: str = "Optical",
    acquisition_time: str = None,
    upload_id: str = None,
    enabled_layers: Optional[list[str]] = None,
):
    """
    Full pipeline: download/validate -> COG conversion -> catalog -> inference -> store.

    enabled_layers: optional list of inference layer names to forward to
        /detect (e.g. ["sam3", "dota_obb", "grounding_dino", "dinov3_sat"]).
        When None the inference service runs all loaded layers.
    """
    try:
        logger.info("[WORKER] Processing satellite image: %s", image_url)
        publish_event("imagery", {"type": "ingest_started", "image_url": image_url, "upload_id": upload_id})
        publish_event("ops", {"type": "imagery_ingest_started", "image_url": image_url, "upload_id": upload_id})

        # 1. Determine local path
        ensure_worker_imagery_schema()
        input_path = resolve_input_path(image_url)
        filename = os.path.basename(input_path)
        upload_job = get_upload_job(upload_id)
        original_filename = upload_job.get("filename") or filename
        upload_meta = upload_job.get("metadata") or {}
        if isinstance(upload_meta, str):
            try:
                upload_meta = json.loads(upload_meta)
            except (TypeError, ValueError):
                upload_meta = {}
        try:
            provider_lifecycle.ensure_running()
            provider_lifecycle.mark_active()
        except Exception as exc:
            logger.warning("[WORKER] provider_lifecycle.ensure_running failed: %s", exc)
        report_progress(self, upload_id, input_path, "metadata", 8, "Reading raster metadata and computing file hash.")
        raster_metadata = extract_raster_metadata(input_path)
        source_hash = raster_metadata.get("source_hash")
        source_filename = original_filename
        update_upload_job(
            upload_id=upload_id,
            file_path=input_path,
            status="processing",
            metadata={"task_id": self.request.id, "raster_metadata": raster_metadata, "source_hash": source_hash},
        )
        report_progress(self, upload_id, input_path, "processing", 10, "Resolved imagery input and metadata.")

        # 2. Convert to COG
        cog_name = f"{os.path.splitext(filename)[0]}_cog.tif"
        cog_path = os.path.join(IMAGERY_PATH, "processed", cog_name)
        os.makedirs(os.path.dirname(cog_path), exist_ok=True)

        report_progress(self, upload_id, input_path, "conversion", 20, "Converting raster to Cloud Optimized GeoTIFF.")
        ensure_cog(input_path, cog_path)
        logger.info("[WORKER] COG created: %s", cog_path)
        report_progress(self, upload_id, input_path, "conversion", 35, "COG conversion complete.", {"cog_path": cog_path})

        # 3. Extract footprint and catalog in PostGIS
        report_progress(self, upload_id, input_path, "cataloging", 45, "Extracting footprint and cataloging imagery.")
        footprint, min_lon, min_lat, max_lon, max_lat = get_raster_footprint(cog_path)
        footprint_wkt = footprint.wkt

        acq_time = acquisition_time or raster_metadata.get("acquisition_time") or datetime.now(timezone.utc).isoformat()

        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute("""
                SELECT id
                FROM satellite_passes
                WHERE acquisition_time = %s::timestamptz
                  AND (
                    (%s IS NOT NULL AND source_hash = %s)
                    OR (source_filename = %s AND ST_Equals(footprint, ST_GeomFromText(%s, 4326)))
                    OR (source_filename IS NULL AND (name = %s OR name LIKE %s) AND ST_Equals(footprint, ST_GeomFromText(%s, 4326)))
                  )
                ORDER BY updated_at DESC NULLS LAST, created_at DESC
                LIMIT 1
            """, (acq_time, source_hash, source_hash, source_filename, footprint_wkt, source_filename, f"%{source_filename}", footprint_wkt))
            existing = cursor.fetchone()
            if existing:
                pass_id = existing["id"]
                cursor.execute("""
                    UPDATE satellite_passes
                    SET name = %s,
                        file_path = %s,
                        sensor_type = %s,
                        acquisition_time = %s,
                        footprint = ST_GeomFromText(%s, 4326),
                        crs = %s,
                        metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb,
                        source_hash = %s,
                        source_filename = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id
                """, (
                    original_filename,
                    cog_path,
                    sensor_type,
                    acq_time,
                    footprint_wkt,
                    "EPSG:4326",
                    json.dumps({**raster_metadata, "upload_id": upload_id, "replacement": True}, default=str),
                    source_hash,
                    source_filename,
                    pass_id,
                ))
                cursor.fetchone()
                replacement = True
            else:
                cursor.execute("""
                    INSERT INTO satellite_passes (
                        name, file_path, sensor_type, acquisition_time, footprint, crs,
                        metadata, source_hash, source_filename, updated_at
                    )
                    VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326), %s, %s, %s, %s, NOW())
                    RETURNING id
                """, (
                    original_filename,
                    cog_path,
                    sensor_type,
                    acq_time,
                    footprint_wkt,
                    "EPSG:4326",
                    json.dumps({**raster_metadata, "upload_id": upload_id, "replacement": False}, default=str),
                    source_hash,
                    source_filename,
                ))
                pass_id = cursor.fetchone()["id"]
                replacement = False

        logger.info("[WORKER] Cataloged in PostGIS with id=%s replacement=%s", pass_id, replacement)
        report_progress(
            self,
            upload_id,
            input_path,
            "classification",
            50,
            "Imagery cataloged; replacing matching timestamp detections." if replacement else "Imagery cataloged; preparing classification graph.",
            {"pass_id": pass_id, "acquisition_time": acq_time, "replacement": replacement},
        )
        record_timeline_event(
            "GEOINT",
            "imagery_replaced" if replacement else "imagery_cataloged",
            original_filename,
            {"pass_id": pass_id, "upload_id": upload_id, "source_hash": source_hash, "replacement": replacement},
            occurred_at=acq_time,
        )

        # 4. Create SatellitePass node in Neo4j
        with db.get_session() as session:
            session.run("""
                MERGE (sp:SatellitePass {postgis_id: $pass_id})
                ON CREATE SET sp.created_at = datetime()
                SET sp.name = $name,
                    sp.sensor_type = $sensor_type,
                    sp.acquisition_time = $acq_time,
                    sp.file_path = $file_path,
                    sp.min_lon = $min_lon,
                    sp.min_lat = $min_lat,
                    sp.max_lon = $max_lon,
                    sp.max_lat = $max_lat,
                    sp.updated_at = datetime()
            """, {
                "pass_id": pass_id,
                "name": original_filename,
                "sensor_type": sensor_type,
                "acq_time": acq_time,
                "file_path": cog_path,
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat
            })

        # 5. Tiling inference
        report_progress(self, upload_id, input_path, "inference", 55, "Starting chip inference.", {"pass_id": pass_id})
        clear_existing_detections(pass_id)
        logger.info("[WORKER] Starting tiling inference...")
        inference_metadata = {}
        prompt_override = _parse_prompt_override(upload_meta.get("text_prompts"))
        if prompt_override:
            inference_metadata["text_prompts"] = prompt_override
        # Honor enabled_layers from the upload form. Two channels: explicit
        # task arg (already parsed) takes precedence; otherwise read from the
        # stored upload_meta which may carry a JSON-encoded list.
        layers_to_use = enabled_layers
        if not layers_to_use:
            raw_layers = upload_meta.get("enabled_layers")
            if isinstance(raw_layers, str):
                try:
                    layers_to_use = json.loads(raw_layers)
                except json.JSONDecodeError:
                    layers_to_use = None
            elif isinstance(raw_layers, list):
                layers_to_use = raw_layers
        if layers_to_use:
            inference_metadata["enabled_layers"] = list(layers_to_use)
            logger.info("[WORKER] Forwarding enabled_layers=%s to /detect", layers_to_use)
        inference_result = slice_and_infer(
            cog_path,
            pass_id,
            inference_metadata=inference_metadata,
            progress_callback=lambda stage, progress, message, extra=None: report_progress(
                self,
                upload_id,
                input_path,
                stage,
                progress,
                message,
                {"pass_id": pass_id, **(extra or {})},
            ),
        )
        detections = inference_result["detections"]
        inference_summary = inference_result["summary"]
        try:
            provider_lifecycle.mark_active()
        except Exception as exc:
            logger.warning("[WORKER] provider_lifecycle.mark_active(post-infer) failed: %s", exc)
        logger.info("[WORKER] Total detections after dedupe: %s", len(detections))
        report_progress(
            self,
            upload_id,
            input_path,
            "classification",
            90,
            "Inference complete; labeling detection classes.",
            {"pass_id": pass_id, "detections_count": len(detections), "inference_summary": inference_summary},
        )
        ontology_by_class = classify_detection_ontologies(
            detections,
            progress_callback=lambda stage, progress, message, extra=None: report_progress(
                self,
                upload_id,
                input_path,
                stage,
                progress,
                message,
                {"pass_id": pass_id, "detections_count": len(detections), **(extra or {})},
            ),
        )

        # 6. Store detections
        report_progress(self, upload_id, input_path, "storage", 95, "Storing all detections and generating candidate links.", {"pass_id": pass_id, "detections_count": len(detections)})
        stored_count = store_detections(detections, pass_id, ontology_by_class)
        candidate_count = generate_candidate_links_for_pass(pass_id)
        logger.info("[WORKER] Stored %s detections and generated %s candidate links.", stored_count, candidate_count)

        # Invoke detection tracker — failure must not poison detection ingest
        try:
            try:
                from tracker import update_tracks_for_pass
            except ImportError:
                from .tracker import update_tracks_for_pass
            tracker_stats = update_tracks_for_pass(pass_id, postgis_db=postgis_db)
            logger.info("[WORKER] Tracker updated for pass %s: %s", pass_id, tracker_stats)
        except Exception as exc:
            logger.exception("[WORKER] Tracker update failed for pass %s: %s", pass_id, exc)

        payload = {
            "pass_id": pass_id,
            "cog_path": cog_path,
            "upload_id": upload_id,
            "detections_count": stored_count,
            "candidate_links_count": candidate_count,
            "acquisition_time": acq_time,
            "replacement": replacement,
            "inference_summary": inference_summary,
            "processed_chips": inference_summary.get("processed_chips"),
            "total_chips": inference_summary.get("planned_chips"),
            "planned_chips": inference_summary.get("planned_chips"),
            "source_total_chips": inference_summary.get("source_total_chips"),
        }
        update_upload_job(
            upload_id=upload_id,
            file_path=input_path,
            status="ready",
            metadata={
                **payload,
                "stage": "ready",
                "progress": 100,
                "message": "Imagery processing complete.",
            },
            clear_metadata_keys=("error",),
        )
        publish_event("detections", {"type": "detections_updated", **payload})
        publish_event("imagery", {"type": "ingest_succeeded", "stage": "ready", "progress": 100, **payload})
        publish_event("ops", {"type": "imagery_ready", "stage": "ready", "progress": 100, **payload})

        return payload
    except Exception as e:
        logger.exception("[WORKER] Imagery ingest failed: %s", e)
        failed_path = locals().get("input_path") or image_url
        update_upload_job(
            upload_id=upload_id,
            file_path=failed_path,
            status="failed",
            metadata={
                "error": str(e),
                "task_id": self.request.id,
                "stage": "failed",
                "message": f"Imagery processing failed: {e}",
            },
        )
        publish_event("imagery", {"type": "ingest_failed", "image_url": image_url, "upload_id": upload_id, "error": str(e)})
        publish_event("ops", {"type": "imagery_failed", "image_url": image_url, "upload_id": upload_id, "error": str(e)})
        raise

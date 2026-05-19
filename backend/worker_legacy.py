import os
import time
import sys
import json
import requests
import subprocess
import uuid
import logging
import math
import threading
import concurrent.futures
import tempfile
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
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
from calibration import calibrate_confidence
from detection_policy import active_detection_policy, detection_decision, parent_class_for_label
from candidate_linking import rank_candidate_links
from threat_assessment import (
    assess_detection_threat,
    clean_detection_class,
    conservative_detection_ontology,
    detection_ontology,
)
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
# Phase 1.3: optional second-scale chip pass at a smaller window so the model
# gets a higher pixel-per-object budget on small targets (TELs, fuel bowsers,
# light armour, etc.). When > 0 and != DEFAULT_INFERENCE_CHIP_SIZE,
# slice_and_infer runs the second pass after the main pass; both passes share
# the dedupe index so duplicates across scales are suppressed by NMS.
INFERENCE_SMALL_OBJECT_CHIP_SIZE = env_int("INFERENCE_SMALL_OBJECT_CHIP_SIZE", 0)
INFERENCE_SMALL_OBJECT_OVERLAP = env_int("INFERENCE_SMALL_OBJECT_OVERLAP", 128)
INFERENCE_SMALL_OBJECT_MAX_CHIPS = env_int(
    "INFERENCE_SMALL_OBJECT_MAX_CHIPS", _INFERENCE_PROFILE_DEFAULTS["max_chips"] or 0
)
INFERENCE_CHIP_CONCURRENCY = max(1, env_int("INFERENCE_CHIP_CONCURRENCY", _INFERENCE_PROFILE_DEFAULTS["concurrency"]))
INFERENCE_CHIP_TIMEOUT_S = env_int("INFERENCE_CHIP_TIMEOUT_S", 120)
INFERENCE_MIN_VALID_CHIP_FRACTION = max(0.0, min(1.0, env_float("INFERENCE_MIN_VALID_CHIP_FRACTION", 0.01)))
INFERENCE_MIN_VALID_DETECTION_FRACTION = max(0.0, min(1.0, env_float("INFERENCE_MIN_VALID_DETECTION_FRACTION", 0.20)))


def _load_per_class_valid_fractions() -> dict[str, float]:
    """Phase 3.10: per-class minimum-valid-pixel fractions.

    The global 0.20 floor drops legitimate detections where >80% of the bbox
    sits on cloud/water/nodata pixels — fine for dense ground vehicles, but
    over-conservative for ships at water edges or aircraft partially obscured
    by cloud. Operators set per-class overrides via ``PER_CLASS_VALID_FRACTION_OVERRIDES``
    JSON; unrecognised classes fall back to ``INFERENCE_MIN_VALID_DETECTION_FRACTION``.

    Suggested defaults for an analyst tuning this in production::

        {"ship": 0.05, "naval": 0.05, "aircraft": 0.10,
         "vehicle": 0.25, "building": 0.30, "infrastructure": 0.30}
    """
    raw_env = (os.getenv("PER_CLASS_VALID_FRACTION_OVERRIDES") or "").strip()
    out: dict[str, float] = {}
    if raw_env:
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    try:
                        out[str(key).strip().lower()] = max(0.0, min(1.0, float(value)))
                    except (TypeError, ValueError):
                        continue
        except json.JSONDecodeError:
            logger.warning("PER_CLASS_VALID_FRACTION_OVERRIDES is not valid JSON; ignoring")
    return out


_PER_CLASS_VALID_FRACTION_OVERRIDES: dict[str, float] = _load_per_class_valid_fractions()


def _valid_fraction_threshold_for(det_class: str | None) -> float:
    if not det_class or not _PER_CLASS_VALID_FRACTION_OVERRIDES:
        return INFERENCE_MIN_VALID_DETECTION_FRACTION
    return _PER_CLASS_VALID_FRACTION_OVERRIDES.get(
        str(det_class).strip().lower(),
        INFERENCE_MIN_VALID_DETECTION_FRACTION,
    )
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
celery_app.conf.beat_schedule = {
    "tick-collection-scheduler": {
        "task": "worker.tick_collection_scheduler",
        "schedule": float(env_int("COLLECTION_SCHEDULER_INTERVAL_S", 300)),
    },
    "tick-feed-poll": {
        "task": "worker.tick_feed_poll",
        "schedule": float(env_int("FEED_POLL_INTERVAL_S", 60)),
    },
}
celery_app.conf.timezone = "UTC"


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


from events import get_redis_client, publish_event, record_timeline_event


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
    min_valid_fraction: float | None = None,
) -> tuple[float, float, float, float] | None:
    """Clip a bbox to its valid-pixel envelope.

    Phase 3.10: ``min_valid_fraction`` is overridable per call so callers
    (which know the detection's parent_class) can apply a class-specific
    floor — water-edge ships keep at 0.05, large infrastructure at 0.30.
    Falls back to the global ``INFERENCE_MIN_VALID_DETECTION_FRACTION``
    when no override is passed.
    """
    threshold = (
        INFERENCE_MIN_VALID_DETECTION_FRACTION if min_valid_fraction is None
        else max(0.0, min(1.0, float(min_valid_fraction)))
    )
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
    if valid_fraction < threshold:
        return None

    valid_y, valid_x = np.nonzero(box_mask)
    clipped_x1 = float(ix1 + int(valid_x.min()))
    clipped_y1 = float(iy1 + int(valid_y.min()))
    clipped_x2 = float(ix1 + int(valid_x.max()) + 1)
    clipped_y2 = float(iy1 + int(valid_y.max()) + 1)
    if clipped_x2 <= clipped_x1 or clipped_y2 <= clipped_y1:
        return None
    return clipped_x1, clipped_y1, clipped_x2, clipped_y2


from geometry import iou_cxcywh, iou_xyxy as bbox_iou


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


def _load_per_class_iou_thresholds() -> dict[str, float]:
    """Phase 2.7: per-class NMS IoU floors.

    A single global 0.45 threshold over-suppresses dense small objects (dense
    truck convoys) and under-suppresses overlapping large structures
    (hangars, terminals). This map lets the operator set tighter / looser
    thresholds per parent_class via env (``PER_CLASS_NMS_IOU_OVERRIDES``,
    JSON dict) or via the DB ``inference_config`` row. Falls back to the
    global default when no class-specific value exists.
    """
    raw_env = (os.getenv("PER_CLASS_NMS_IOU_OVERRIDES") or "").strip()
    out: dict[str, float] = {}
    if raw_env:
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    try:
                        out[str(key).strip().lower()] = max(0.0, min(1.0, float(value)))
                    except (TypeError, ValueError):
                        continue
        except json.JSONDecodeError:
            logger.warning("PER_CLASS_NMS_IOU_OVERRIDES is not valid JSON; ignoring")
    return out


_PER_CLASS_IOU_THRESHOLDS: dict[str, float] = _load_per_class_iou_thresholds()


def _load_per_model_trust_weights() -> dict[str, float]:
    """Phase 2.8: per-model trust weights.

    Multiplies the detection's confidence at NMS-comparison time so a tuned
    DOTA-OBB output isn't drowned out by an over-confident SAM3 mask score.
    Env: ``PER_MODEL_TRUST_WEIGHTS`` JSON dict keyed by ``source_layer`` /
    ``model_version`` substring (case-insensitive). Unrecognised models keep
    weight 1.0.
    """
    raw_env = (os.getenv("PER_MODEL_TRUST_WEIGHTS") or "").strip()
    out: dict[str, float] = {}
    if raw_env:
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    try:
                        out[str(key).strip().lower()] = max(0.0, float(value))
                    except (TypeError, ValueError):
                        continue
        except json.JSONDecodeError:
            logger.warning("PER_MODEL_TRUST_WEIGHTS is not valid JSON; ignoring")
    return out


_PER_MODEL_TRUST_WEIGHTS: dict[str, float] = _load_per_model_trust_weights()


def _trust_weight_for(det: dict) -> float:
    if not _PER_MODEL_TRUST_WEIGHTS:
        return 1.0
    for tag in (det.get("source_layer"), det.get("model_version"), det.get("parent_class"), det.get("class")):
        if not tag:
            continue
        key = str(tag).strip().lower()
        if key in _PER_MODEL_TRUST_WEIGHTS:
            return _PER_MODEL_TRUST_WEIGHTS[key]
        # Allow substring match (e.g. "dota_obb" in "dota_obb:v1.2")
        for src_key, weight in _PER_MODEL_TRUST_WEIGHTS.items():
            if src_key and src_key in key:
                return weight
    return 1.0


class _DetectionDedupeIndex:
    """Incremental NMS with the same IoU+bucket algorithm as the old
    deduplicate_detections, but with state that persists across chip
    boundaries — so slice_and_infer can dedupe and store survivors as each
    chip completes (instead of one giant batch at the very end of inference).

    Phase 2.7/2.8: IoU thresholds are now per-class, and the sort key
    incorporates per-model trust weights so a tuned specialist isn't
    drowned out by a loud generalist."""

    BUCKET_SIZE = 512

    def __init__(self, iou_threshold: float = 0.45) -> None:
        self.iou_threshold = iou_threshold
        self.buckets: dict[tuple[str, int, int], list[dict]] = {}
        self.raw_seen = 0
        self.kept_count = 0

    def _iou_for_class(self, det_class: str | None, modality: str | None = None) -> float:
        """Per-class IoU floor. Phase 5.22: SAR detections are point-like and
        speckle-driven, so a tighter default (0.25 vs 0.45 optical) suppresses
        the long tail of weak overlapping detections that flood the SAR
        output. The per-class override map still wins when a class is listed.
        """
        if det_class:
            override = _PER_CLASS_IOU_THRESHOLDS.get(str(det_class).strip().lower())
            if override is not None:
                return override
        if (modality or "").strip().lower() == "sar":
            try:
                return float(os.getenv("SAR_NMS_IOU_DEFAULT", "0.25"))
            except ValueError:
                return 0.25
        return self.iou_threshold

    def add(self, detections: list) -> list:
        """Run the new batch through NMS against the running index.

        Returns the list of survivors (mutated state). The batch is sorted by
        ``trust_weight * confidence`` so a high-trust specialist suppresses a
        lower-trust generalist when they overlap — matching the principle
        WBF will eventually replace this with, while preserving the simple
        NMS contract for now."""
        if not detections:
            return []

        def _sort_key(item: dict) -> float:
            try:
                conf = float(item.get("confidence") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            return _trust_weight_for(item) * conf

        survivors: list[dict] = []
        for det in sorted(detections, key=_sort_key, reverse=True):
            self.raw_seen += 1
            if not det.get("pixel_bbox"):
                det.setdefault("dedupe_method", "obb_nms")
                survivors.append(det)
                self.kept_count += 1
                continue

            x1, y1, x2, y2 = det["pixel_bbox"]
            cx = int(((x1 + x2) / 2) // self.BUCKET_SIZE)
            cy = int(((y1 + y2) / 2) // self.BUCKET_SIZE)
            det_class = det.get("parent_class") or det.get("class")
            iou_for_class = self._iou_for_class(det_class, det.get("modality"))
            suppressed = False
            for dx in (-1, 0, 1):
                if suppressed:
                    break
                for dy in (-1, 0, 1):
                    for existing in self.buckets.get((det_class, cx + dx, cy + dy), ()):
                        if detection_overlap(det, existing) >= iou_for_class:
                            suppressed = True
                            break
                    if suppressed:
                        break
            if suppressed:
                continue

            det.setdefault("dedupe_method", "obb_nms")
            self.buckets.setdefault((det_class, cx, cy), []).append(det)
            survivors.append(det)
            self.kept_count += 1
        return survivors

    def reconcile_edge_truncated(self, survivors: list[dict]) -> tuple[list[dict], int]:
        """Phase 3.12: cross-chip edge reconciliation.

        After every chip has finished and the global NMS has run, some
        ``edge_truncated`` detections still survive because their per-chip
        bbox didn't IoU-overlap the matching detection from the adjacent
        chip — each saw a different half of the object. This second pass
        scans each edge_truncated survivor against neighbours in the same
        class within 1 spatial bucket (so cross-chip pairs land in the same
        comparison window). When a pair is found whose pixel-bbox union
        forms a meaningful continuation, we keep the higher-confidence
        survivor as a ``reconciled`` detection with the union bbox and
        drop the lower-confidence half. The merged detection is flagged
        ``dedupe_method="edge_reconciled"`` so provenance can show that
        an edge stitching happened.

        Returns ``(reconciled_survivors, merge_count)``.
        """
        if not survivors:
            return survivors, 0
        truncated = [det for det in survivors if det.get("edge_truncated")]
        if len(truncated) < 2:
            return survivors, 0
        # Pre-bucket by (class, bucket_cx, bucket_cy) for cheap neighbour lookup.
        buckets: dict[tuple[str, int, int], list[dict]] = {}
        for det in truncated:
            bb = det.get("pixel_bbox") or []
            if len(bb) < 4:
                continue
            x1, y1, x2, y2 = bb[:4]
            cx = int(((x1 + x2) / 2) // self.BUCKET_SIZE)
            cy = int(((y1 + y2) / 2) // self.BUCKET_SIZE)
            det_class = det.get("parent_class") or det.get("class")
            buckets.setdefault((det_class, cx, cy), []).append(det)
        suppressed_ids: set[int] = set()
        merges = 0
        for det in truncated:
            if id(det) in suppressed_ids:
                continue
            bb = det.get("pixel_bbox") or []
            if len(bb) < 4:
                continue
            x1, y1, x2, y2 = bb[:4]
            cx = int(((x1 + x2) / 2) // self.BUCKET_SIZE)
            cy = int(((y1 + y2) / 2) // self.BUCKET_SIZE)
            det_class = det.get("parent_class") or det.get("class")
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for other in buckets.get((det_class, cx + dx, cy + dy), ()):
                        if other is det or id(other) in suppressed_ids:
                            continue
                        obb = other.get("pixel_bbox") or []
                        if len(obb) < 4:
                            continue
                        ox1, oy1, ox2, oy2 = obb[:4]
                        # Centroids close OR bboxes adjacent / overlapping.
                        det_cx = (x1 + x2) / 2
                        det_cy = (y1 + y2) / 2
                        other_cx = (ox1 + ox2) / 2
                        other_cy = (oy1 + oy2) / 2
                        d = math.hypot(det_cx - other_cx, det_cy - other_cy)
                        if d > max((x2 - x1), (y2 - y1)) + max((ox2 - ox1), (oy2 - oy1)):
                            continue  # too far apart
                        # Pick the higher-confidence detection as the
                        # survivor; expand its bbox to the union of both.
                        det_conf = float(det.get("confidence") or 0.0)
                        other_conf = float(other.get("confidence") or 0.0)
                        winner, loser = (det, other) if det_conf >= other_conf else (other, det)
                        winner["pixel_bbox"] = [
                            min(x1, ox1), min(y1, oy1),
                            max(x2, ox2), max(y2, oy2),
                        ]
                        winner["dedupe_method"] = "edge_reconciled"
                        winner["edge_truncated"] = False  # union is no longer partial
                        suppressed_ids.add(id(loser))
                        merges += 1
                        break
                    if id(det) in suppressed_ids:
                        break
                if id(det) in suppressed_ids:
                    break
        if not suppressed_ids:
            return survivors, 0
        reconciled = [det for det in survivors if id(det) not in suppressed_ids]
        self.kept_count = max(0, self.kept_count - len(suppressed_ids))
        return reconciled, merges


def deduplicate_detections(
    detections: list,
    iou_threshold: float = 0.45,
) -> list:
    """Stateless dedup wrapper preserved for callers that batch up detections
    themselves (tests, FMV pipeline)."""
    if not detections:
        return []
    return _DetectionDedupeIndex(iou_threshold=iou_threshold).add(detections)


# ---------------------------------------------------------------------------
# Phase 2.6: Weighted Boxes Fusion (Solovyev et al. 2019).
#
# Where NMS picks one survivor per overlapping cluster and drops the rest,
# WBF averages every box in the cluster, weighted by (trust_weight × calibrated
# confidence), to produce a single fused box whose confidence is the cluster's
# average rather than the max. This rewards multi-detector agreement instead
# of letting the loudest single model dominate, and is the recommended
# post-calibration ensembling step in the modern aerial object-detection
# literature.
#
# Implemented here as a stateful index with the same ``.add(batch) -> list``
# contract as ``_DetectionDedupeIndex`` so it can be swapped in by env flag
# (``DEDUPE_METHOD=wbf``). Default remains the existing NMS path; WBF is
# opt-in until we have the larger evaluation harness from Phase 9 in place
# to validate it doesn't regress per-class recall.
# ---------------------------------------------------------------------------


class _WeightedBoxFusionIndex:
    """Stateful WBF clusterer. Same contract as ``_DetectionDedupeIndex``.

    Maintains per-class clusters in spatial buckets. When a new detection
    overlaps an existing cluster, the cluster's fused bbox is updated to
    the (weight-weighted) average of every member box, and the new
    detection is added to the cluster's member list. When no cluster
    overlaps, a new single-member cluster is started.

    ``.add(batch)`` returns only newly created or changed cluster heads. That
    makes the streaming path safe: callers do not re-store every historical
    cluster after each chip. ``heads()`` exposes the final full set when a
    deferred flush is needed.
    """

    BUCKET_SIZE = 512

    def __init__(
        self,
        iou_threshold: float = 0.55,
        expected_models: int = 2,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.expected_models = max(1, int(expected_models))
        # bucket → list[cluster]; cluster is a dict with the fused detection
        # plus a parallel ``_members`` list of contributing weights/boxes.
        self.buckets: dict[tuple[str, int, int], list[dict]] = {}
        # Order-preserving list of cluster heads, in insertion order.
        self.clusters: list[dict] = []
        self.raw_seen = 0
        self.kept_count = 0

    def _iou_for_class(self, det_class: str | None) -> float:
        if det_class:
            override = _PER_CLASS_IOU_THRESHOLDS.get(str(det_class).strip().lower())
            if override is not None:
                return override
        return self.iou_threshold

    @staticmethod
    def _bucket_of(bbox: list[float]) -> tuple[int, int]:
        cx = int(((bbox[0] + bbox[2]) / 2) // _WeightedBoxFusionIndex.BUCKET_SIZE)
        cy = int(((bbox[1] + bbox[3]) / 2) // _WeightedBoxFusionIndex.BUCKET_SIZE)
        return cx, cy

    @staticmethod
    def _weighted_average(members: list[dict]) -> tuple[list[float], float]:
        """Return (fused_bbox_xyxy, weight_sum) for the cluster's members."""
        total = sum(m["weight"] for m in members)
        if total <= 0:
            return members[0]["bbox"], 0.0
        fused = [0.0, 0.0, 0.0, 0.0]
        for m in members:
            w = m["weight"] / total
            bb = m["bbox"]
            for i in range(4):
                fused[i] += w * bb[i]
        return fused, total

    def add(self, detections: list) -> list:
        if not detections:
            return []
        changed_heads: list[dict] = []
        for det in sorted(detections, key=lambda d: _trust_weight_for(d) * float(d.get("confidence") or 0.0), reverse=True):
            self.raw_seen += 1
            bbox = det.get("pixel_bbox")
            if not bbox or len(bbox) < 4:
                # Pass-through for detections without a bbox; treat as its
                # own cluster so it survives.
                det.setdefault("dedupe_method", "wbf")
                cluster = {"head": det, "_members": [], "_class": det.get("parent_class") or det.get("class")}
                self.clusters.append(cluster)
                self.kept_count += 1
                changed_heads.append(det)
                continue
            try:
                conf = float(det.get("confidence") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            weight = _trust_weight_for(det) * conf
            det_class = det.get("parent_class") or det.get("class")
            iou_for_class = self._iou_for_class(det_class)
            cx, cy = self._bucket_of(bbox)
            best_cluster: dict | None = None
            best_iou = 0.0
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for cand in self.buckets.get((det_class, cx + dx, cy + dy), ()):
                        head = cand["head"]
                        overlap = detection_overlap(det, head)
                        if overlap >= iou_for_class and overlap > best_iou:
                            best_iou = overlap
                            best_cluster = cand
            if best_cluster is not None:
                # Append to existing cluster, recompute fused bbox.
                best_cluster["_members"].append({"bbox": list(bbox[:4]), "weight": weight, "raw_conf": conf, "source": det.get("source_layer")})
                fused_bbox, _ = self._weighted_average(best_cluster["_members"])
                head = best_cluster["head"]
                head["pixel_bbox"] = fused_bbox
                # Cluster confidence = mean(member raw_conf) × min(N, expected) / expected
                # — the second factor rewards multi-detector agreement.
                n = len(best_cluster["_members"])
                mean_conf = sum(m["raw_conf"] for m in best_cluster["_members"]) / n
                agreement_factor = min(n, self.expected_models) / self.expected_models
                head["confidence"] = max(0.0, min(1.0, mean_conf * (0.5 + 0.5 * agreement_factor)))
                head["dedupe_method"] = "wbf"
                head["wbf_member_count"] = n
                head["wbf_member_sources"] = sorted({
                    m["source"] or "unknown" for m in best_cluster["_members"]
                })
                changed_heads.append(head)
            else:
                det.setdefault("dedupe_method", "wbf")
                det["wbf_member_count"] = 1
                det["wbf_member_sources"] = [det.get("source_layer") or "unknown"]
                cluster = {
                    "head": det,
                    "_class": det_class,
                    "_members": [{"bbox": list(bbox[:4]), "weight": weight, "raw_conf": conf, "source": det.get("source_layer")}],
                }
                self.buckets.setdefault((det_class, cx, cy), []).append(cluster)
                self.clusters.append(cluster)
                self.kept_count += 1
                changed_heads.append(det)
        return changed_heads

    def heads(self) -> list[dict]:
        return [c["head"] for c in self.clusters]

    def reconcile_edge_truncated(self, survivors: list[dict]) -> tuple[list[dict], int]:
        """No-op for WBF. The fusion step already handles cross-chip
        contributions to the same object — adding a second pass of
        edge-truncated reconciliation would double-merge. Returns the
        input survivor list unchanged + zero merges so the
        non-streaming caller's contract still works.
        """
        return list(survivors), 0


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
        meta = {
            "modality": "multispectral",
            "filename": "chip.tif",
            "content_type": "image/tiff",
            "geo": geo_meta,
        }
        if valid_mask_meta:
            meta["valid_mask"] = valid_mask_meta
        return _geotiff_window_file(src, window, list(range(1, 7))), meta

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
    on_chip_store=None,
):
    """Slice COG into chips, send each to SAM3 /detect, dedupe and return detections.

    When `on_chip_store` is provided, surviving detections from each chip are
    handed off to that callback (which inserts them into the DB and fires a
    `detections_partial` WS event) instead of being accumulated for a single
    bulk store at the end. The returned `summary` reflects the same totals
    either way; the returned `detections` list is empty in the streaming path
    because every survivor has already been persisted."""
    inference_metadata = inference_metadata or {}
    streaming = on_chip_store is not None
    with rasterio.open(cog_path) as src:
        width = src.width
        height = src.height
        transform = src.transform
        crs = src.crs

        # Phase 2.6: opt-in WBF dedup. ``DEDUPE_METHOD=wbf`` swaps the
        # confidence-greedy NMS for confidence-averaged Weighted Boxes
        # Fusion so multi-detector agreement boosts the fused score
        # rather than the loudest single model winning. Default remains
        # NMS until the larger eval harness validates WBF doesn't
        # regress per-class recall.
        if (os.getenv("DEDUPE_METHOD", "nms") or "nms").strip().lower() == "wbf":
            dedupe_idx: _DetectionDedupeIndex | _WeightedBoxFusionIndex = _WeightedBoxFusionIndex(
                iou_threshold=float(os.getenv("WBF_IOU_THRESHOLD", "0.55")),
                expected_models=int(os.getenv("WBF_EXPECTED_MODELS", "2")),
            )
        else:
            dedupe_idx = _DetectionDedupeIndex()
        # A fused head can continue changing when later chips arrive. Persisting
        # every intermediate WBF head in streaming mode creates duplicate DB
        # rows and stores stale geometry, so WBF is flushed once at the end.
        defer_streaming_store = streaming and isinstance(dedupe_idx, _WeightedBoxFusionIndex)
        all_kept: list[dict] = []  # only populated when not streaming
        completed_chip_count = 0

        # Phase 1.3: build the list of (chip_size, overlap, max_chips) passes.
        # The first entry is the main pass at the caller's configured size;
        # an optional second entry runs at INFERENCE_SMALL_OBJECT_CHIP_SIZE so
        # small-class targets get a higher pixel-per-object budget. Both
        # passes share the same dedupe_idx, so NMS suppresses cross-scale
        # duplicates of the same object.
        chip_passes: list[tuple[int, int, int]] = [(chip_size, overlap, max_chips)]
        if (
            INFERENCE_SMALL_OBJECT_CHIP_SIZE > 0
            and INFERENCE_SMALL_OBJECT_CHIP_SIZE != chip_size
        ):
            chip_passes.append((
                INFERENCE_SMALL_OBJECT_CHIP_SIZE,
                INFERENCE_SMALL_OBJECT_OVERLAP,
                INFERENCE_SMALL_OBJECT_MAX_CHIPS,
            ))

        # Pre-plan every pass so total_windows = sum across passes — keeps the
        # progress callback's percentage monotonic 0-100% across multi-scale.
        pass_plans: list[dict] = []
        for pass_chip_size, pass_overlap, pass_max_chips in chip_passes:
            g = plan_inference_grid(width, height, pass_chip_size, pass_overlap, pass_max_chips)
            pass_plans.append({
                "chip_size": pass_chip_size,
                "overlap": pass_overlap,
                "max_chips": pass_max_chips,
                "grid": g,
                "step": g["step"],
                "planned_total": g["planned_total"],
            })
        total_windows = sum(p["planned_total"] for p in pass_plans)
        processed_windows = 0
        failed_windows = 0
        last_reported_percent = None

        # The first pass is the primary one for summary fields; per-pass
        # breakdown lives under `passes`.
        main_plan = pass_plans[0]
        grid = main_plan["grid"]
        step = main_plan["step"]
        coverage_fraction = round(total_windows / max(1, sum(p["grid"]["source_total"] for p in pass_plans)), 4)
        inference_summary = {
            "chip_size": chip_size,
            "overlap": overlap,
            "step": step,
            "planned_chips": total_windows,
            "source_total_chips": sum(p["grid"]["source_total"] for p in pass_plans),
            "processed_chips": 0,
            "inference_speed_profile": INFERENCE_SPEED_PROFILE,
            "coverage_fraction": coverage_fraction,
            "sampling_enabled": any(p["grid"]["sampled"] for p in pass_plans),
            "max_inference_chips": grid["max_chips"],
            "dedupe_method": "wbf" if isinstance(dedupe_idx, _WeightedBoxFusionIndex) else "obb_nms",
            "threshold_profile": DETECTION_POLICY["threshold_profile"],
            "taxonomy_version": DETECTION_POLICY["taxonomy_version"],
            "model_version": DETECTION_POLICY["model_version"],
            "max_pending_chips": INFERENCE_MAX_PENDING_CHIPS,
            "chip_spool_max_bytes": INFERENCE_CHIP_SPOOL_MAX_BYTES,
            "multi_scale": len(pass_plans) > 1,
            "passes": [
                {
                    "chip_size": p["chip_size"],
                    "overlap": p["overlap"],
                    "planned_chips": p["planned_total"],
                    "source_total_chips": p["grid"]["source_total"],
                    "sampling_enabled": p["grid"]["sampled"],
                }
                for p in pass_plans
            ],
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

        def _apply_chip_response(ctx: dict, inference_response: dict) -> list[dict]:
            """Convert the chip's inference response into pass-frame detections.

            Returns the per-chip detection list (one entry per surviving
            inference output). The caller is responsible for running NMS and
            either streaming-store or accumulating these."""
            x = ctx["x"]; y = ctx["y"]
            win_width = ctx["win_width"]; win_height = ctx["win_height"]
            valid_mask = ctx.get("valid_mask")
            chip_detections = []
            chip_results: list[dict] = []
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

                # Phase 3.10: apply per-class valid-fraction threshold so
                # water-edge ships aren't dropped at the same 0.20 floor as
                # ground vehicles. parent_class is derived from the
                # ontology normalizer in _apply_chip_response.
                _det_class_for_clip = det.get("parent_class") or det.get("class")
                local_box = clip_box_to_valid_mask(
                    valid_mask,
                    chip_px_cx - chip_px_w / 2,
                    chip_px_cy - chip_px_h / 2,
                    chip_px_cx + chip_px_w / 2,
                    chip_px_cy + chip_px_h / 2,
                    min_valid_fraction=_valid_fraction_threshold_for(_det_class_for_clip),
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
                raw_confidence = float(det.get("confidence") or 0.0)
                # Phase 2.5: apply per-model temperature scaling so different
                # detectors' confidence distributions become comparable before
                # NMS and the per-class threshold gate consume them. T defaults
                # to 1.0 (identity) when no calibration is configured for this
                # model — the call is safe and cheap.
                model_tag = (
                    det.get("source_layer")
                    or det.get("model_version")
                    or DETECTION_POLICY.get("model_version")
                )
                confidence = calibrate_confidence(raw_confidence, model_tag)
                from calibration import temperature_for as _t_for
                det["raw_confidence"] = raw_confidence
                det["calibrated_confidence"] = confidence
                det["model_temperature"] = _t_for(model_tag)
                det["confidence"] = confidence
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
                # Phase 3.11: position-uncertainty ellipse — replaces the
                # Phase 7.35 scalar with semi-major / semi-minor axes in
                # metres and a bearing in degrees (clockwise from north,
                # WGS-84 convention). The ellipse is anisotropic when the
                # raster pixel is non-square or the CRS is geographic
                # (where 1° lon shrinks with cos(latitude)), which is the
                # common case for Sentinel-1 / Landsat tiles. The
                # ``position_uncertainty_m`` scalar is preserved as the
                # 95%-CEP equivalent (semi-major × 1) so downstream code
                # that already consumes it keeps working.
                try:
                    px_w = abs(float(transform.a))
                    px_h = abs(float(transform.e))
                    if crs and crs.is_geographic:
                        mid_lat_rad = math.radians((lat1 + lat2) / 2.0)
                        meters_per_deg_lat = 111_320.0
                        meters_per_deg_lon = 111_320.0 * max(math.cos(mid_lat_rad), 0.01)
                        sigma_x_m = 2.0 * px_w * meters_per_deg_lon  # easting
                        sigma_y_m = 2.0 * px_h * meters_per_deg_lat  # northing
                    else:
                        sigma_x_m = 2.0 * px_w
                        sigma_y_m = 2.0 * px_h
                    # Map (sigma_x, sigma_y) to (semi-major, semi-minor) +
                    # bearing. With axis-aligned pixel uncertainty the
                    # bearing is 0° when sigma_y dominates (north-south) or
                    # 90° when sigma_x dominates (east-west).
                    semi_major = max(sigma_x_m, sigma_y_m)
                    semi_minor = min(sigma_x_m, sigma_y_m)
                    bearing_deg = 0.0 if sigma_y_m >= sigma_x_m else 90.0
                    det["position_uncertainty_m"] = round(semi_major, 3)
                    det["position_uncertainty_ellipse"] = {
                        "semi_major_m": round(semi_major, 3),
                        "semi_minor_m": round(semi_minor, 3),
                        "bearing_deg": bearing_deg,
                        "confidence": 0.95,  # 2-sigma ≈ 95%
                        "source": "gsd_propagation",
                    }
                except Exception:
                    pass
                det["chip_id"] = f"{pass_id}:{x}:{y}:{win_width}:{win_height}"
                det["chip_window"] = [x, y, win_width, win_height]
                det["chip_valid_fraction"] = ctx.get("valid_fraction")
                det["coverage_fraction"] = coverage_fraction
                det["planned_chips"] = total_windows
                det["source_total_chips"] = grid["source_total"]
                det["sampling_enabled"] = grid["sampled"]
                det["dedupe_method"] = "obb_nms"
                chip_results.append(det)
            return chip_results

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
            nonlocal processed_windows, failed_windows, completed_chip_count
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
            chip_dets = _apply_chip_response(ctx, response)
            kept = dedupe_idx.add(chip_dets)
            processed_windows += 1
            completed_chip_count += 1
            if streaming and not defer_streaming_store:
                if kept:
                    try:
                        on_chip_store(kept, completed_chip_count)
                    except Exception as exc:
                        logger.exception(
                            "[WORKER] on_chip_store callback failed for pass=%s chip=%s: %s",
                            pass_id, completed_chip_count, exc,
                        )
            else:
                all_kept.extend(kept)
            _report_inference_progress()

        # Cap in-flight chips and spool oversized PNGs to disk so large rasters
        # cannot accumulate unbounded encoded chip buffers in memory.
        pending_limit = INFERENCE_MAX_PENDING_CHIPS

        try:
            for pass_index, plan in enumerate(pass_plans):
                # Rebind per-pass closure variables. _apply_chip_response,
                # _report_inference_progress, _consume_one read `grid`,
                # `chip_size`, `step`, `coverage_fraction`, `total_windows`
                # by closure, so the rebind here takes effect for them too.
                chip_size = plan["chip_size"]
                step = plan["step"]
                grid = plan["grid"]
                # `coverage_fraction` is now an across-pass average; keep the
                # single rebound value for chip metadata. _apply_chip_response
                # records this on each detection.
                if pass_index == 0 and progress_callback:
                    if grid["sampled"]:
                        msg = f"Large raster detected; sampling {plan['planned_total']} of {grid['source_total']} chips for inference."
                    else:
                        msg = f"Prepared {total_windows} raster chips for inference."
                    progress_callback(
                        "inference", 56, msg,
                        {
                            "planned_chips": total_windows,
                            "total_chips": total_windows,
                            "source_total_chips": grid["source_total"],
                            "processed_chips": 0,
                            "failed_chips": 0,
                            "inference_speed_profile": INFERENCE_SPEED_PROFILE,
                            "max_inference_chips": grid["max_chips"],
                            "sampling_enabled": inference_summary["sampling_enabled"],
                            "coverage_fraction": coverage_fraction,
                            "multi_scale": inference_summary["multi_scale"],
                        },
                    )
                elif pass_index > 0:
                    logger.info(
                        "[WORKER] Starting small-object pass %s: chip_size=%s overlap=%s planned_chips=%s",
                        pass_index, chip_size, plan["overlap"], plan["planned_total"],
                    )

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
                            "scale_pass": pass_index,
                            **inference_metadata,
                            **chip_meta,
                        })
                        chip_label = f"pass={pass_id} scale={pass_index} x={x} y={y}"

                        future = executor.submit(
                            _post_chip_to_sam3,
                            session, chip_file, chip_meta_payload, chip_label,
                        )
                        del chip
                        pending[future] = {
                            "x": x, "y": y, "win_width": win_width, "win_height": win_height,
                            "valid_mask": valid_mask,
                            "valid_fraction": round(valid_fraction, 4),
                            "scale_pass": pass_index,
                        }

                        while len(pending) >= pending_limit:
                            done, _ = concurrent.futures.wait(
                                list(pending.keys()),
                                return_when=concurrent.futures.FIRST_COMPLETED,
                            )
                            for fut in done:
                                _consume_one(fut)

                # Drain this pass before starting the next so per-pass detections
                # are stored before the smaller-scale chips run against them.
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
    
    inference_summary["processed_chips"] = processed_windows
    inference_summary["failed_chips"] = failed_windows
    inference_summary["raw_detections"] = dedupe_idx.raw_seen
    inference_summary["deduped_detections"] = dedupe_idx.kept_count
    inference_summary["suppressed_detections"] = max(0, dedupe_idx.raw_seen - dedupe_idx.kept_count)
    # Phase 3.12: cross-chip edge reconciliation runs only on the non-streaming
    # path (where the full survivor list is in memory). Streaming mode pushes
    # each chip's survivors to ``on_chip_store`` immediately and cannot wait
    # for a hypothetical complementary detection from a future chip; a
    # follow-up will add a small buffer of edge_truncated survivors with a
    # bounded flush window for the streaming case.
    if not streaming and all_kept:
        reconciled, merge_count = dedupe_idx.reconcile_edge_truncated(all_kept)
        all_kept = reconciled
        inference_summary["edge_reconciled_pairs"] = merge_count
        inference_summary["deduped_detections"] = dedupe_idx.kept_count
    elif defer_streaming_store:
        final_heads = dedupe_idx.heads()
        if final_heads:
            on_chip_store(final_heads, completed_chip_count)
        all_kept = []
    return {"detections": all_kept, "summary": inference_summary}


def run_sar_cfar_for_pass(
    cog_path: str,
    pass_id: int,
    *,
    threshold_sigma: float = 2.5,
    guard_px: int = 4,
    background_px: int = 20,
    min_pixels: int = 4,
    on_chip_store=None,
) -> dict:
    """Phase 5.20b: run the SAR CFAR detector across a Sentinel-1 (or
    similar) GRD COG and ingest the resulting ship detections.

    Companion to Phase 5.20's "skip SAM3 on SAR by default" gate — once SAM3
    is muted, this is what produces detections for SAR rasters. The
    detector lives in :mod:`backend.sar_cfar` and runs entirely on the CPU
    worker; no GPU / inference-service round trip needed.

    Reuses :func:`plan_inference_grid` for chip planning + the same
    pixel→geo transform that ``slice_and_infer`` uses, so the resulting
    detections share the exact same provenance shape as the SAM3 path
    (chip_id, chip_window, pixel_bbox, geo_bbox, geo_polygon, sampling_*
    metadata, …). Stored via ``on_chip_store`` when streaming, or
    accumulated and stored at the end otherwise.

    Args follow ``detect_ships_cfar`` plus an ``on_chip_store`` callback
    that matches ``slice_and_infer``'s contract: ``(survivor_dets, chip_index) -> None``.
    Returns the same shape as ``slice_and_infer``::

        {"detections": [..], "summary": {..}}
    """
    from sar_cfar import detect_ships_cfar  # local import: keep worker startup cheap

    streaming = on_chip_store is not None
    summary: dict = {
        "method": "sar_cfar",
        "modality": "sar",
        "threshold_sigma": threshold_sigma,
        "guard_px": guard_px,
        "background_px": background_px,
        "min_pixels": min_pixels,
    }
    all_kept: list[dict] = []
    dedupe_idx = _DetectionDedupeIndex(
        iou_threshold=float(os.getenv("SAR_NMS_IOU_DEFAULT", "0.25"))
    )

    with rasterio.open(cog_path) as src:
        width = src.width
        height = src.height
        transform = src.transform
        crs = src.crs

        # Pick VV (band 1) + optional VH (band 2) — Sentinel-1 IW GRD is
        # always (VV, VH) in that order. Other 2-band SAR formats follow
        # the same convention. Single-band rasters fall back to VV-only.
        try:
            vv = src.read(1).astype(np.float32)
        except Exception as exc:
            logger.warning("[CFAR] failed to read band 1 from %s: %s", cog_path, exc)
            return {"detections": [], "summary": {**summary, "error": str(exc)}}
        vh: np.ndarray | None = None
        if src.count >= 2:
            try:
                vh = src.read(2).astype(np.float32)
            except Exception as exc:
                logger.warning("[CFAR] failed to read band 2: %s", exc)
                vh = None

        # The CFAR runs on dB-scaled backscatter. Heuristic: if the input
        # values span > 50 they're already in dB; otherwise treat as linear
        # amplitude and convert.
        def _to_db(arr: np.ndarray) -> np.ndarray:
            if arr.size == 0:
                return arr
            span = float(arr.max() - arr.min())
            if span > 50.0 or arr.min() < -1.0:
                return arr
            with np.errstate(divide="ignore", invalid="ignore"):
                return 10.0 * np.log10(np.maximum(arr, 1e-6))
        vv = _to_db(vv)
        if vh is not None:
            vh = _to_db(vh)

        # Plan a coarse chip grid so very large COGs stay bounded. CFAR is
        # cheap so the chip size can be much larger than the SAM3 inference
        # chip — 4096 px gives plenty of context for the background window.
        chip_size = int(os.getenv("SAR_CFAR_CHIP_SIZE", "4096"))
        overlap = int(os.getenv("SAR_CFAR_OVERLAP", "256"))
        grid = plan_inference_grid(width, height, chip_size, overlap, max_chips=0)
        step = grid["step"]
        summary["planned_chips"] = grid["planned_total"]
        summary["source_total_chips"] = grid["source_total"]
        summary["sampling_enabled"] = grid["sampled"]
        coverage_fraction = round(grid["planned_total"] / max(1, grid["source_total"]), 4)

        chip_index = 0
        for y_idx in grid["y_indices"]:
            y = y_idx * step
            for x_idx in grid["x_indices"]:
                x = x_idx * step
                win_w = min(chip_size, width - x)
                win_h = min(chip_size, height - y)
                if win_w <= 2 * background_px + 1 or win_h <= 2 * background_px + 1:
                    continue  # too small for the CFAR window
                tile_vv = vv[y : y + win_h, x : x + win_w]
                tile_vh = vh[y : y + win_h, x : x + win_w] if vh is not None else None
                try:
                    cfar_dets = detect_ships_cfar(
                        tile_vv, tile_vh,
                        threshold_sigma=threshold_sigma,
                        guard_px=guard_px,
                        background_px=background_px,
                        min_pixels=min_pixels,
                    )
                except Exception as exc:
                    logger.warning("[CFAR] chip x=%s y=%s failed: %s", x, y, exc)
                    cfar_dets = []
                if not cfar_dets:
                    continue
                chip_index += 1

                survivors: list[dict] = []
                for det in cfar_dets:
                    # CFAR pixel_bbox is in tile-local coords; lift to COG-global.
                    lx1, ly1, lx2, ly2 = det["pixel_bbox"]
                    abs_px = [
                        float(x + lx1), float(y + ly1),
                        float(x + lx2), float(y + ly2),
                    ]
                    pixel_obb = [
                        abs_px[0], abs_px[1],
                        abs_px[2], abs_px[1],
                        abs_px[2], abs_px[3],
                        abs_px[0], abs_px[3],
                    ]
                    # Pixel → geo via the COG transform; reproject to WGS84
                    # when CRS isn't already lat/lon, matching slice_and_infer.
                    pts = [
                        (pixel_obb[i], pixel_obb[i + 1])
                        for i in range(0, 8, 2)
                    ]
                    lons, lats = [], []
                    for px, py in pts:
                        lon_v, lat_v = transform * (px, py)
                        lons.append(lon_v)
                        lats.append(lat_v)
                    if crs and crs.to_string() != "EPSG:4326":
                        from rasterio.warp import transform as rasterio_transform
                        lons, lats = rasterio_transform(crs, "EPSG:4326", lons, lats)
                    geo_polygon = [c for pt in zip(lons, lats) for c in pt]
                    lon1, lat1, lon2, lat2 = min(lons), min(lats), max(lons), max(lats)

                    det.update({
                        "pixel_bbox": abs_px,
                        "pixel_obb": pixel_obb,
                        "geo_bbox": [lon1, lat1, lon2, lat2],
                        "geo_polygon": geo_polygon,
                        "chip_id": f"{pass_id}:{x}:{y}:{win_w}:{win_h}:cfar",
                        "chip_window": [x, y, win_w, win_h],
                        "coverage_fraction": coverage_fraction,
                        "planned_chips": grid["planned_total"],
                        "source_total_chips": grid["source_total"],
                        "sampling_enabled": grid["sampled"],
                        "dedupe_method": "sar_cfar",
                        "source_layer": "sar_cfar",
                        "modality": "sar",
                        "scale_pass": 0,
                    })
                    survivors.append(det)

                survivors = dedupe_idx.add(survivors)
                if streaming and survivors:
                    try:
                        on_chip_store(survivors, chip_index)
                    except Exception as exc:
                        logger.exception("[CFAR] on_chip_store failed: %s", exc)
                elif survivors:
                    all_kept.extend(survivors)

        summary["processed_chips"] = chip_index
        summary["coverage_fraction"] = coverage_fraction
        summary["raw_detections"] = dedupe_idx.raw_seen
        summary["deduped_detections"] = dedupe_idx.kept_count
        summary["suppressed_detections"] = max(0, dedupe_idx.raw_seen - dedupe_idx.kept_count)
    return {"detections": all_kept, "summary": summary}


def _aoi_default_allegiance_at(cursor, lon: float, lat: float) -> str:
    """Phase 6.26: return the ``default_allegiance`` of the AOI containing
    ``(lon, lat)`` — first match wins by smallest area (so nested AOIs work).
    Falls back to ``"unknown"`` when no AOI matches or the column is missing
    on an old install.
    """
    try:
        cursor.execute(
            "SELECT default_allegiance FROM aois "
            "WHERE geom IS NOT NULL AND ST_Intersects(geom, ST_SetSRID(ST_Point(%s, %s), 4326)) "
            "ORDER BY ST_Area(geom) ASC LIMIT 1",
            (lon, lat),
        )
        row = cursor.fetchone()
    except Exception:
        return "unknown"
    if not row:
        return "unknown"
    raw = row[0] if not isinstance(row, dict) else row.get("default_allegiance")
    value = (str(raw or "unknown")).strip().lower()
    return value if value in {"friendly", "hostile", "neutral", "unknown"} else "unknown"


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
            # Phase 6.26: per-AOI default allegiance. When the detection's
            # centroid falls inside an AOI with a non-"unknown" default, use
            # that as the starting allegiance instead of the global "unknown".
            # An explicit per-detection allegiance (set upstream by the
            # operator or another worker stage) still wins.
            allegiance = det.get("allegiance") or _aoi_default_allegiance_at(
                cursor, (lon1 + lon2) / 2.0, (lat1 + lat2) / 2.0,
            )
            assessment = assess_detection_threat(det_class, confidence=confidence, allegiance=allegiance)
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
                    # Phase 2.5: keep the pre-calibration score visible for
                    # audit. ``calibrated_confidence`` is what NMS and the
                    # threshold gate use; the analyst sees both in provenance.
                    "raw_confidence": det.get("raw_confidence"),
                    "model_temperature": det.get("model_temperature"),
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
                    "source_layer": det.get("source_layer"),
                    "wbf_member_count": det.get("wbf_member_count"),
                    "wbf_member_sources": det.get("wbf_member_sources"),
                    # Phase 7.35: surface the per-detection position uncertainty
                    # (in metres) so the UI can render an uncertainty halo.
                    "position_uncertainty_m": det.get("position_uncertainty_m"),
                    "position_uncertainty_ellipse": det.get("position_uncertainty_ellipse"),
                    "scale_pass": det.get("scale_pass"),
                    "ontology": ontology,
                    "threat_level": assessment["threat_level"],
                    "threat_confidence": assessment["threat_confidence"],
                    "assessment_status": assessment["assessment_status"],
                    "evidence": assessment["evidence"],
                    "allegiance": allegiance,
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
                "allegiance": allegiance,
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


FMV_TRACK_FPS = float(os.getenv("FMV_TRACK_FPS", "4"))
# 540p prep clip: at 10 km slant range a person ≈22 px on the prep clip (vs
# ~6 px at 270p), which puts the target inside SAM3's small-object band.
FMV_TRACK_HEIGHT = int(os.getenv("FMV_TRACK_HEIGHT", "540"))
# SAM3's video predictor pins every decoded frame as a single CUDA tensor at
# session start (~80 MiB/frame at 540p once letterboxed to 1024² on a 16 GiB
# GPU also holding the SAM3 image+video weights). 48 frames keeps peak VRAM
# under ~5 GiB and lets a 12 s window run at 4 fps in a single session.
FMV_TRACK_FRAMES_PER_WINDOW = int(os.getenv("FMV_TRACK_FRAMES_PER_WINDOW", "48"))
# Window slicing of the source clip. Each window is its own SAM3 video
# session, so the tracker gets a fresh re-detection every WINDOW_SECONDS of
# source content, avoiding the "tracker loses target after 7 s" behaviour
# observed on Day Flight.mpg.
FMV_TRACK_WINDOW_SECONDS = float(os.getenv("FMV_TRACK_WINDOW_SECONDS", "12"))
FMV_TRACK_WINDOW_OVERLAP_SECONDS = float(os.getenv("FMV_TRACK_WINDOW_OVERLAP_SECONDS", "2"))
# In-flight cap for /detect_video fan-out. Defaults to the inference-sam3
# pool size discovered at /health (i.e. one slot per GPU), so each parallel
# task lands on a distinct multiplex predictor replica. The env override
# is a hard ceiling — if /health reports a smaller pool we use that.
FMV_INFLIGHT_REQUESTS = max(1, int(os.getenv("FMV_INFLIGHT_REQUESTS", "4")))


def _probe_source(src_path: str) -> tuple[float, float]:
    """Return `(source_fps, duration_s)` via ffprobe (best-effort)."""
    fps = 30.0
    duration = 0.0
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate,duration:format=duration",
             "-of", "default=nw=1", src_path],
            capture_output=True, text=True, check=False, timeout=10,
        )
        for line in (proc.stdout or "").splitlines():
            key, _, val = line.partition("=")
            val = val.strip()
            if key == "r_frame_rate" and val:
                if "/" in val:
                    num, den = val.split("/", 1)
                    den_f = float(den)
                    if den_f > 0:
                        fps = float(num) / den_f
                else:
                    fps = float(val)
            elif key == "duration" and val and val != "N/A":
                try:
                    duration = float(val)
                except ValueError:
                    pass
    except Exception:
        pass
    return fps or 30.0, duration


# Smallest plausible 540p libx264 single-frame mp4 is several KiB. A 261-byte
# ftyp-only stub (the failure mode we're guarding against — see the
# Truck.win01 incident 2026-05-12) is well below this. The threshold is
# conservative: well above any legitimate output, well below the smallest
# real clip we'd produce.
_FMV_WINDOW_MIN_BYTES = 4 * 1024


def _window_output_is_valid(path: Path) -> bool:
    """True iff `path` is a non-empty mp4 with at least one video stream.

    Guards against ffmpeg's "exit 0 with no streams" failure mode where
    `-ss` lands past the last keyframe or the filter graph emits zero
    frames. Cheap size check first, then ffprobe stream-type check."""
    try:
        if not path.is_file() or path.stat().st_size < _FMV_WINDOW_MIN_BYTES:
            return False
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=False, timeout=10,
        )
        return bool((proc.stdout or "").strip())
    except Exception:
        return False


def _prepare_tracking_window(src_path: str, window_idx: int, start_s: float, duration_s: float) -> Path | None:
    """Extract a single sliding-window track clip from the source.

    Returns the output Path, or None on ffmpeg failure or zero-stream
    output. Each window is a short low-fps low-res mp4 sized so the
    entire decoded frame stack fits in GPU memory at SAM3 video
    session-init time."""
    src = Path(src_path)
    out = src.with_name(f"{src.stem}.win{window_idx:02d}.track.mp4")
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        if _window_output_is_valid(out):
            return out
        # Cached file exists but is a stub (e.g. produced by an older
        # build that didn't validate). Unlink so we re-extract below.
        logger.warning("FMV window %d cached output at %s is invalid; re-extracting", window_idx, out)
        out.unlink(missing_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start_s:.3f}",
        "-i", str(src),
        "-t", f"{duration_s:.3f}",
        "-an",
        "-vf", f"fps={FMV_TRACK_FPS},scale=-2:{FMV_TRACK_HEIGHT}",
        "-frames:v", str(FMV_TRACK_FRAMES_PER_WINDOW),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        # Force mp4 container explicitly: the tmp filename ends in
        # `.mp4.tmp`, which defeats ffmpeg's extension-based format
        # auto-detection. The final `os.replace(tmp, out)` lands at
        # `.mp4`, so the container we write here must be mp4.
        "-f", "mp4",
        str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.warning("FMV window %d prep failed for %s: %s", window_idx, src, proc.stderr[:300])
        tmp.unlink(missing_ok=True)
        return None
    if not _window_output_is_valid(tmp):
        logger.warning(
            "FMV window %d produced zero-stream output for %s (start=%.3fs len=%.3fs) — deleting and skipping",
            window_idx, src, start_s, duration_s,
        )
        tmp.unlink(missing_ok=True)
        return None
    os.replace(tmp, out)
    return out


def _slice_windows(duration_s: float) -> list[tuple[float, float]]:
    """Compute `[(start_s, length_s)]` slices that overlap by
    FMV_TRACK_WINDOW_OVERLAP_SECONDS. The final window may be shorter if
    the duration doesn't divide evenly. Returns at least one window."""
    if duration_s <= 0:
        return [(0.0, FMV_TRACK_WINDOW_SECONDS)]
    step = max(0.5, FMV_TRACK_WINDOW_SECONDS - FMV_TRACK_WINDOW_OVERLAP_SECONDS)
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_s:
        length = min(FMV_TRACK_WINDOW_SECONDS, duration_s - start)
        windows.append((start, length))
        if start + length >= duration_s:
            break
        start += step
    return windows or [(0.0, duration_s)]


def _ensure_fmv_profile(session: requests.Session, clip_id: int, max_wait_s: float = 600.0) -> dict:
    """Load the FMV inference profile, retrying on 409 (other request in flight)
    so two consecutive FMV tasks don't fight over the same swap. Returns the
    /health JSON so the caller knows which video backend is active."""
    deadline = time.time() + max_wait_s
    last_err: str | None = None
    while time.time() < deadline:
        try:
            resp = session.post(
                f"{INFERENCE_SAM3_URL}/load",
                params={"profile": "fmv"},
                timeout=600,
            )
            if resp.status_code == 200:
                break
            if resp.status_code == 409:
                publish_event(
                    f"fmv:{clip_id}",
                    {"type": "fmv_detections_progress", "clip_id": clip_id,
                     "stage": "waiting_for_inference", "detail": "another request in flight"},
                )
                last_err = "inference busy (409)"
                time.sleep(2)
                continue
            last_err = f"{resp.status_code}: {resp.text[:200]}"
            time.sleep(2)
        except requests.RequestException as exc:
            last_err = f"connection error: {exc}"
            time.sleep(2)
    else:
        raise RuntimeError(f"could not load FMV inference profile: {last_err or 'timeout'}")
    # Read /health to learn whether the multiplex or base predictor came up.
    health = session.get(f"{INFERENCE_SAM3_URL}/health", timeout=10).json()
    return health


def _update_clip_tracking(clip_id: int, **fields) -> None:
    """Merge tracking_* fields into fmv_clips.metadata jsonb. Best-effort —
    failures here shouldn't kill the tracking task."""
    if not fields:
        return
    try:
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE fmv_clips
                   SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb,
                       updated_at = NOW()
                 WHERE id = %s
                """,
                (json.dumps(fields), clip_id),
            )
    except Exception:
        logger.exception("failed to update fmv_clips.metadata for clip %s", clip_id)


_bbox_iou = iou_cxcywh


def _drain_response_entries(resp) -> list[dict]:
    """Drain one /detect_video streaming response to a list of parsed
    JSON dicts. Pure I/O — safe to run outside any DB / dedup lock so
    parallel fan-out tasks don't serialize on each other while their
    GPU sessions are still streaming. The caller passes the resulting
    list to `_insert_detection_rows` under a lock to do the DB inserts
    and dedup updates."""
    entries: list[dict] = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        entries.append(json.loads(line))
    return entries


# Session prompts that are bookkeeping sentinels, not real concept labels.
# YOLOE mode fans out one session per window with a placeholder prompt;
# the runner emits the per-detection class inside the NDJSON.
_SENTINEL_PROMPTS = frozenset({"_yoloe"})


def _insert_detection_rows(cur, clip_id: int, source_fps: float, window_idx: int, window_start_frame: int,
                            session_prompt: str, entries: list[dict], next_track_id: int,
                            overlap_index: dict[tuple[int, str], list[list[float]]] | None = None,
                            overlap_iou: float = 0.5) -> tuple[int, int]:
    """Insert one (window, prompt) session's parsed entries into fmv_detections.

    Each call corresponds to exactly one (window, prompt) session — the
    upstream SAM3 API (`sam3_video_inference.py:656` / `sam3_multiplex_tracking.py:1934`)
    resets state on every text `add_prompt`, so one session can only track
    one concept. `session_prompt` is the prompt this session was launched
    with; we trust it over any prompt_text the runner emits.

    `entries` is the result of `_drain_response_entries(resp)` — split so
    the HTTP drain can happen unlocked while this function runs under the
    worker's shared lock to mutate `next_track_id` and `overlap_index`
    without races.

    `overlap_index`, if provided, maps `(source_frame, class)` to the list
    of cxcywh-normalised bboxes already inserted in *earlier* windows. Any
    incoming detection with IoU >= `overlap_iou` against an existing entry
    in that key is skipped to suppress the window-overlap duplicates that
    sliding-window tracking produces by construction.

    Returns (rows_inserted, new_next_track_id).
    """
    multiplier = source_fps / FMV_TRACK_FPS if FMV_TRACK_FPS > 0 else 1.0
    inserted = 0
    local_to_global: dict[tuple, int] = {}
    fallback_prompt = session_prompt or "track"
    for entry in entries:
        prep_idx = int(entry["frame_index"])
        source_frame = window_start_frame + int(round(prep_idx * multiplier))
        # SAM3 now emits ``bbox_xyxy_norm`` (already in [0,1] relative to
        # the prep clip). Convert directly to cxcywh-normalised without
        # re-normalising — the previous _xyxy_to_normalized_cxcywh path
        # treated normalised values as pixel xywh and produced offset
        # boxes. Empty/heartbeat frames carry None — store as [] so the
        # frontend's normalizeBbox falls through to the OBB (if any) or
        # skips drawing for that frame.
        bbox_norm = entry.get("bbox_xyxy_norm")
        if bbox_norm and len(bbox_norm) == 4:
            x1n, y1n, x2n, y2n = (float(v) for v in bbox_norm)
            wn = max(0.0, x2n - x1n)
            hn = max(0.0, y2n - y1n)
            cxcywh_norm = [(x1n + x2n) / 2.0, (y1n + y2n) / 2.0, wn, hn]
            bbox_json = json.dumps(cxcywh_norm)
        else:
            # Heartbeat / lost-track frame.
            bbox_json = json.dumps([])
        # Class resolution: PCS mode runs one session per concept, so the
        # session prompt IS the class — trust it over runner output. AMG
        # and YOLOE modes use a single sentinel session prompt
        # ("_amg" / "_yoloe") and the runner assigns a real class per
        # detection (AMG via Grounding-DINO labels; YOLOE from its
        # built-in vocab or text-prompt set_classes). For those modes,
        # honour entry["class"] whenever it's set and not itself a
        # sentinel; fall back only when the runner couldn't label it.
        entry_class = entry.get("class")
        if fallback_prompt in _SENTINEL_PROMPTS:
            if entry_class and entry_class not in _SENTINEL_PROMPTS:
                cls = str(entry_class)
            else:
                cls = fallback_prompt
        else:
            cls = fallback_prompt
        prompt_text = fallback_prompt
        local_tid = entry.get("track_id")
        if local_tid is not None:
            try:
                ltid = int(local_tid)
                key = (window_idx, prompt_text, ltid)
                if key not in local_to_global:
                    local_to_global[key] = next_track_id
                    next_track_id += 1
                global_tid = local_to_global[key]
            except (TypeError, ValueError):
                global_tid = local_tid
        else:
            global_tid = None

        # Cross-window overlap dedup: windows overlap by 2 s by design so
        # the tracker has continuity across the seam. Without dedup every
        # overlap frame produces two rows for the same object.
        if overlap_index is not None and bbox_norm and len(bbox_norm) == 4:
            cxcywh_for_check = json.loads(bbox_json)
            existing_boxes = overlap_index.get((source_frame, cls), [])
            if any(_bbox_iou(cxcywh_for_check, prev) >= overlap_iou for prev in existing_boxes):
                continue

        meta_json = json.dumps({
            "track_id": global_tid,
            "mask_rle": entry.get("mask_rle"),
            "obb": entry.get("obb"),
            "obb_format": entry.get("obb_format"),
            "obb_source": entry.get("obb_source"),
            "obb_angle_deg": entry.get("obb_angle_deg"),
            "edge_truncated": entry.get("edge_truncated"),
            "embedding": entry.get("embedding"),
            "prompt_text": prompt_text,
            "window_index": window_idx,
            "provider": "sam3",
        })
        conf = float(entry.get("score") or 0.0)
        cur.execute(
            """
            INSERT INTO fmv_detections (clip_id, frame_index, class, confidence, bbox, metadata)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
            """,
            (clip_id, source_frame, cls, conf, bbox_json, meta_json),
        )
        inserted += 1
        if overlap_index is not None and bbox_norm and len(bbox_norm) == 4:
            overlap_index.setdefault((source_frame, cls), []).append(json.loads(bbox_json))
    return inserted, next_track_id


@celery_app.task(name="worker.process_fmv", queue="imagery")
def process_fmv(clip_id: int, video_path: str, text_prompts: list[str] | None = None,
                frame_stride: int | None = None, max_frames: int | None = None,
                prompt_mode: str = "pcs") -> int:
    """Run FMV tracking over the full clip via sliding-window sessions.

    ``prompt_mode``:
      * ``"pcs"`` (default) — SAM 3.1 Promptable Concept Segmentation.
        ``text_prompts`` defaults to ``["object"]``. One inference session
        per (window, prompt).
      * ``"yoloe"`` — YOLOE-26x-seg standalone tracker. ``text_prompts``
        non-empty → ``-seg`` checkpoint with those classes;
        ``text_prompts`` empty → ``-pf`` prompt-free checkpoint. Single
        inference session per window.

    Per-window flow:
      1. Slice source into overlapping windows (so SAM3's tracker is
         re-seeded every WINDOW_SECONDS; gives full-clip coverage on top
         of a predictor that loses targets within ~30 frames).
      2. For each window, extract a low-fps/low-res working clip with
         ffmpeg (caps VRAM at SAM3 session-init time).
      3. Call inference. For PCS, iterate prompts one-per-session
         (multiplex resets state on each text add_prompt). For YOLOE, one
         call per window covering all classes.
      4. Commit detections to PostGIS *per window*, then publish progress
         so the FmvPlayer sees boxes appear within seconds of the first
         window finishing — not 4 minutes after the whole clip processes.
    """
    provider_lifecycle.ensure_running()
    mode = (prompt_mode or "pcs").strip().lower()
    if mode not in {"pcs", "yoloe"}:
        raise ValueError(f"unknown prompt_mode {prompt_mode!r}")
    source_fps, duration_s = _probe_source(video_path)
    if duration_s <= 0:
        duration_s = FMV_TRACK_WINDOW_SECONDS
    windows = _slice_windows(duration_s)
    # `prompts` drives the per-window task fan-out. PCS fans out one
    # /detect_video session per prompt because SAM 3.1 multiplex resets
    # state on every text prompt. YOLOE handles all classes in one forward
    # pass per frame, so it collapses to a single sentinel-prompt task per
    # window and the real prompt list is forwarded via closure below.
    yoloe_prompts: list[str] = list(text_prompts or [])
    if mode == "yoloe":
        prompts = ["_yoloe"]
    else:
        prompts = list(text_prompts or [])
        if not prompts:
            prompts = ["object"]

    _update_clip_tracking(
        clip_id,
        tracking_status="running",
        tracking_started_at=datetime.now(timezone.utc).isoformat(),
        tracking_windows=len(windows),
        tracking_prompts=prompts,
        tracking_count=0,
        tracking_error=None,
    )
    publish_event(
        f"fmv:{clip_id}",
        {"type": "fmv_detections_progress", "clip_id": clip_id,
         "window": 0, "windows": len(windows), "stage": "starting"},
    )

    session = requests.Session()
    try:
        health = _ensure_fmv_profile(session, clip_id)
        video_backend = (health.get("model_versions") or {}).get("sam3_video", "")
        multiplex = "multiplex" in str(video_backend).lower()
        # Bound concurrency by the inference-sam3 pool size. Each multiplex
        # replica accepts one in-flight session at a time (enforced by its
        # per-bundle lock on the server); going beyond pool_size just
        # bounces with 503 and forces us to wait, so right-size it here.
        pool_size = int(health.get("pool_size") or 1)
        inflight_cap = max(1, min(FMV_INFLIGHT_REQUESTS, pool_size))
        logger.info(
            "FMV tracking clip=%s windows=%d backend=%s multiplex=%s mode=%s "
            "pool_size=%d inflight_cap=%d",
            clip_id, len(windows), video_backend, multiplex, mode, pool_size, inflight_cap,
        )

        # Build the (window, prompt) task list. ffmpeg slicing runs
        # sequentially up front because it's cheap (~500 ms/window) and
        # parallel ffmpeg processes would just contend for disk anyway.
        # Each window appears in the task list as its (win_path,
        # window_start_frame); inference fan-out happens across the
        # cartesian product with prompts.
        sliced: list[tuple[int, int, Any]] = []  # (window_idx, window_start_frame, win_path)
        for window_idx, (start_s, length_s) in enumerate(windows):
            win_path = _prepare_tracking_window(video_path, window_idx, start_s, length_s)
            if win_path is None:
                continue
            sliced.append((window_idx, int(round(start_s * source_fps)), win_path))

        tasks = [(win_idx, win_start_frame, win_path, prompt)
                 for (win_idx, win_start_frame, win_path) in sliced
                 for prompt in prompts]
        total_tasks = len(tasks)

        # Shared state guarded by `shared_lock`. `next_track_id` is the
        # rolling allocator for global track IDs; `overlap_index` is the
        # cross-window dedup table from the FP-suppression round. Both
        # are touched per-row in `_insert_detection_rows`, so the lock
        # also wraps the row insertion itself (otherwise two threads can
        # race the same (source_frame, class) key).
        shared_lock = threading.Lock()
        state = {"next_track_id": 0, "inserted": 0, "completed": 0}
        overlap_index: dict[tuple[int, str], list[list[float]]] = {}

        def _run_one(args: tuple[int, int, Any, str]) -> int:
            win_idx, win_start_frame, win_path, prompt = args
            if mode == "yoloe":
                # YOLOE runs one inference per window covering all classes.
                # Empty text_prompts → service uses yoloe-26x-seg-pf
                # (prompt-free); non-empty → yoloe-26x-seg with prompts.
                payload = json.dumps({
                    "video_path": str(win_path),
                    "prompt_mode": "yoloe",
                    "text_prompts": list(yoloe_prompts),
                    "frame_stride": 1,
                    "max_frames": FMV_TRACK_FRAMES_PER_WINDOW,
                    "modality": "fmv",
                })
            else:
                payload = json.dumps({
                    "video_path": str(win_path),
                    "text_prompts": [prompt],
                    "frame_stride": 1,
                    "max_frames": FMV_TRACK_FRAMES_PER_WINDOW,
                    "modality": "fmv",
                })
            # Retry transient 503s — the server returns 503 when every
            # GPU bundle is busy, so this is the natural backpressure
            # signal under fan-out. Linear backoff capped at 30 s; the
            # full session timeout still bounds the wait.
            attempt = 0
            while True:
                attempt += 1
                try:
                    resp = session.post(
                        f"{INFERENCE_SAM3_URL}/detect_video",
                        data={"metadata": payload},
                        stream=True,
                        timeout=INFERENCE_CHIP_TIMEOUT_S * 60,
                    )
                    if resp.status_code == 503 and attempt < 20:
                        resp.close()
                        time.sleep(min(0.5 + 0.5 * attempt, 5.0))
                        continue
                    resp.raise_for_status()
                    break
                except requests.RequestException:
                    if attempt >= 5:
                        raise
                    time.sleep(min(1.0 * attempt, 5.0))
            try:
                # Drain the HTTP stream OUTSIDE the lock — this is the
                # long-running part (mirrors the GPU's per-frame emit
                # cadence). Holding the lock here would serialize every
                # task on the slowest GPU, killing the fan-out.
                entries = _drain_response_entries(resp)
            finally:
                resp.close()
            with shared_lock:
                with postgis_db.get_cursor(commit=True) as cur:
                    n, new_next = _insert_detection_rows(
                        cur, clip_id, source_fps, win_idx, win_start_frame,
                        prompt, entries, state["next_track_id"],
                        overlap_index=overlap_index,
                    )
                    state["next_track_id"] = new_next
                    state["inserted"] += n
                    state["completed"] += 1
                    completed = state["completed"]
                    running_total = state["inserted"]
            publish_event(
                f"fmv:{clip_id}",
                {"type": "fmv_detections_progress", "clip_id": clip_id,
                 "window": win_idx + 1, "windows": len(windows),
                 "inserted": n, "total_inserted": running_total,
                 "completed_tasks": completed, "total_tasks": total_tasks,
                 "prompt": prompt},
            )
            _update_clip_tracking(clip_id, tracking_count=running_total)
            return n

        with concurrent.futures.ThreadPoolExecutor(max_workers=inflight_cap) as pool:
            futures = [pool.submit(_run_one, t) for t in tasks]
            for fut in concurrent.futures.as_completed(futures):
                fut.result()  # re-raise any task exception

        inserted = state["inserted"]

        _update_clip_tracking(
            clip_id,
            tracking_status="complete",
            tracking_completed_at=datetime.now(timezone.utc).isoformat(),
            tracking_count=inserted,
        )
        publish_event(
            f"fmv:{clip_id}",
            {"type": "fmv_detections_complete", "clip_id": clip_id, "count": inserted},
        )
        publish_event(
            "ops",
            {"type": "fmv_detections_complete", "clip_id": clip_id, "count": inserted},
        )
        return inserted
    except Exception as exc:
        logger.exception("FMV processing failed for clip %s", clip_id)
        message = str(exc)[:500] or exc.__class__.__name__
        _update_clip_tracking(
            clip_id,
            tracking_status="failed",
            tracking_completed_at=datetime.now(timezone.utc).isoformat(),
            tracking_error=message,
        )
        publish_event(
            f"fmv:{clip_id}",
            {"type": "fmv_detections_failed", "clip_id": clip_id, "error": message},
        )
        publish_event(
            "ops",
            {"type": "fmv_detections_failed", "clip_id": clip_id, "error": message},
        )
        raise
    finally:
        session.close()


def _target_history_anchor(cursor, target_id: str) -> float:
    cursor.execute(
        "SELECT count(*) AS c FROM detection_target_candidates "
        "WHERE target_id = %s AND status IN ('accepted', 'confirmed')",
        (target_id,),
    )
    row = cursor.fetchone()
    if not row:
        return 0.0
    accepted = int(row["c"] if isinstance(row, dict) else row[0])
    return min(1.0, accepted / 5.0)


def generate_candidate_links_for_pass(
    pass_id: int,
    distance_threshold_meters: float = 1500.0,
    max_candidates_per_detection: int = 5,
) -> int:
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
            ranked = rank_candidate_links(
                dict(det),
                targets,
                max_distance_m=distance_threshold_meters,
                max_candidates_per_detection=max_candidates_per_detection,
                history_lookup=lambda target_id: _target_history_anchor(cursor, target_id),
            )
            for item in ranked:
                target_id = item["target_id"]
                evidence = {
                    "distance_m": round(item["distance_m"], 2),
                    "compatibility_reason": item["compatibility_reason"],
                    "compatibility_score": round(item["compatibility_score"], 3),
                    "history_anchor": round(item["history_anchor"], 3),
                    "score_weights": item["score_weights"],
                    "detection_class": det["class"],
                    "detection_confidence": item["detection_confidence"],
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
                    item["target_name"],
                    item["score"],
                    item["reason"],
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

        # Phase 5.20: SAM3 is optical-pretrained; running it on TerraMind's
        # SAR pseudo-RGB injects optical-domain priors into a synthetic
        # 3-channel view of a SAR scene, which generates spurious detections.
        # By default we skip SAM3 grounding on SAR rasters and rely on the
        # TerraMind embedding pass only. Operators can opt back in via the
        # ``SAM3_ALLOW_ON_SAR=1`` env or the upload form's
        # ``allow_sam3_on_sar=true`` metadata key.
        sensor_lower = (sensor_type or "").strip().lower()
        if sensor_lower == "sar":
            allow_sam3_on_sar = (
                upload_meta.get("allow_sam3_on_sar") in {True, "true", "1", 1}
                or (os.getenv("SAM3_ALLOW_ON_SAR", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
            )
            inference_metadata["modality"] = "sar"
            inference_metadata["sensor_type"] = "sar"
            if not allow_sam3_on_sar:
                # Express to the inference service: skip SAM3 image grounding;
                # rely on the SAR-specific layers (TerraMind embedding + any
                # future CFAR detector). The inference container interprets
                # an empty layer list with explicit ``sam3=false`` as
                # "embedding-only".
                inference_metadata["skip_sam3_image"] = True
                logger.info(
                    "[WORKER] SAR pass %s: SAM3-on-SAR disabled by default; "
                    "set allow_sam3_on_sar=true or SAM3_ALLOW_ON_SAR=1 to re-enable.",
                    pass_id,
                )
            else:
                logger.info(
                    "[WORKER] SAR pass %s: SAM3-on-SAR enabled by operator opt-in.",
                    pass_id,
                )

        # Streaming detection storage: each chip's surviving detections are
        # written to PostGIS as soon as the chip finishes inference, and a
        # `detections_partial` WS event lets the frontend pick them up. The
        # ontology cache memoises the deterministic ontology so every new
        # class hits detection_ontology() once and reuses the result.
        ontology_cache: dict[str, dict] = {}
        streaming_total = {"stored": 0}

        def _store_chip(kept_dets: list[dict], chip_index: int) -> None:
            for det in kept_dets:
                cls = det.get("class", "Unknown")
                if cls not in ontology_cache:
                    ontology_cache[cls] = {
                        **detection_ontology(cls),
                        "status": "deterministic",
                    }
            stored = store_detections(kept_dets, pass_id, ontology_cache)
            streaming_total["stored"] += stored
            publish_event("detections", {
                "type": "detections_partial",
                "pass_id": pass_id,
                "chip_index": chip_index,
                "stored": stored,
                "stored_total": streaming_total["stored"],
            })

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
            on_chip_store=_store_chip,
        )
        inference_summary = inference_result["summary"]
        # Phase 5.20b: for SAR rasters, run the local CFAR detector after
        # the SAM3 / TerraMind chip pass. Always-on for SAR — operators who
        # want CFAR off explicitly can set ``SAR_CFAR_ENABLED=0``. Routes
        # detections through the same ``_store_chip`` callback so they go
        # into PostGIS + fire ``detections_partial`` WS events just like
        # the SAM3 path.
        if sensor_lower == "sar" and (
            os.getenv("SAR_CFAR_ENABLED", "1") or "1"
        ).strip().lower() in {"1", "true", "yes", "on"}:
            try:
                report_progress(
                    self, upload_id, input_path, "inference", 88,
                    "Running SAR CFAR ship detector.", {"pass_id": pass_id},
                )
                cfar_result = run_sar_cfar_for_pass(
                    cog_path, pass_id,
                    threshold_sigma=float(os.getenv("SAR_CFAR_THRESHOLD_SIGMA", "2.5")),
                    guard_px=int(os.getenv("SAR_CFAR_GUARD_PX", "4")),
                    background_px=int(os.getenv("SAR_CFAR_BACKGROUND_PX", "20")),
                    min_pixels=int(os.getenv("SAR_CFAR_MIN_PIXELS", "4")),
                    on_chip_store=_store_chip,
                )
                inference_summary["sar_cfar"] = cfar_result.get("summary") or {}
                logger.info(
                    "[WORKER] SAR CFAR pass %s: %s",
                    pass_id, inference_summary["sar_cfar"],
                )
            except Exception as exc:
                logger.exception("[WORKER] SAR CFAR pass failed for pass %s: %s", pass_id, exc)
                inference_summary["sar_cfar_error"] = str(exc)

        stored_count = streaming_total["stored"]
        logger.info("[WORKER] Total detections after dedupe: %s", stored_count)

        # 6. Finalise: detections were stored progressively per chip, so only
        # candidate links + tracker need the post-inference pass.
        report_progress(
            self,
            upload_id,
            input_path,
            "storage",
            95,
            "Generating candidate links.",
            {"pass_id": pass_id, "detections_count": stored_count, "inference_summary": inference_summary},
        )
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

        if (sensor_type or "").lower() in {"multispectral", "hyperspectral"}:
            try:
                run_prithvi_multitemporal.delay(pass_id)
            except Exception as exc:
                logger.warning("[WORKER] Failed to queue prithvi multitemporal for pass %s: %s", pass_id, exc)

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


# ============================================================================
# Prithvi multi-temporal consistency — runs after a multispectral pass lands.
# Looks for prior overlapping passes within
# PRITHVI_MULTI_TEMPORAL_WINDOW_DAYS and tags each Prithvi-labeled detection
# in the current pass with a per-label `pass_count` indicating how often the
# same label appears within
# PRITHVI_MULTI_TEMPORAL_MATCH_RADIUS_M of the same point in priors.
# ============================================================================


PRITHVI_MULTI_TEMPORAL_WINDOW_DAYS = env_int("PRITHVI_MULTI_TEMPORAL_WINDOW_DAYS", 30)
PRITHVI_MULTI_TEMPORAL_MIN_PRIORS = env_int("PRITHVI_MULTI_TEMPORAL_MIN_PRIORS", 2)
PRITHVI_MULTI_TEMPORAL_MATCH_RADIUS_M = env_float("PRITHVI_MULTI_TEMPORAL_MATCH_RADIUS_M", 200.0)


@celery_app.task(name="worker.run_prithvi_multitemporal", queue="imagery")
def run_prithvi_multitemporal(pass_id: int) -> dict:
    with postgis_db.get_cursor() as cur:
        cur.execute(
            """
            SELECT id, acquisition_time, sensor_type
            FROM satellite_passes
            WHERE id = %s AND footprint IS NOT NULL
            """,
            (pass_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"status": "skipped", "reason": "pass_not_found", "pass_id": pass_id}

    with postgis_db.get_cursor() as cur:
        cur.execute(
            """
            SELECT id, acquisition_time
            FROM satellite_passes
            WHERE id <> %s
              AND footprint IS NOT NULL
              AND ST_Intersects(footprint, (SELECT footprint FROM satellite_passes WHERE id = %s))
              AND acquisition_time >= NOW() - (%s || ' days')::interval
            ORDER BY acquisition_time DESC
            LIMIT 5
            """,
            (pass_id, pass_id, PRITHVI_MULTI_TEMPORAL_WINDOW_DAYS),
        )
        priors = [dict(r) for r in cur.fetchall()]

    if len(priors) < PRITHVI_MULTI_TEMPORAL_MIN_PRIORS:
        return {"status": "skipped", "reason": "insufficient_history", "found": len(priors), "pass_id": pass_id}

    prior_ids = [int(p["id"]) for p in priors]

    with postgis_db.get_cursor() as cur:
        cur.execute(
            """
            SELECT id, ST_X(centroid) AS lon, ST_Y(centroid) AS lat, metadata
            FROM detections
            WHERE pass_id = %s
              AND deleted_at IS NULL
              AND metadata ? 'prithvi_labels'
            """,
            (pass_id,),
        )
        detections = [dict(r) for r in cur.fetchall()]

    if not detections:
        return {"status": "skipped", "reason": "no_prithvi_detections", "pass_id": pass_id}

    updated = 0
    for det in detections:
        metadata = det.get("metadata") or {}
        labels = metadata.get("prithvi_labels") or []
        if not isinstance(labels, list) or not labels:
            continue
        consistency: dict[str, dict] = {}
        with postgis_db.get_cursor() as cur:
            for label in labels:
                if not isinstance(label, str):
                    continue
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT pass_id) AS pass_count
                    FROM detections
                    WHERE pass_id = ANY(%s)
                      AND deleted_at IS NULL
                      AND metadata->'prithvi_labels' ? %s
                      AND ST_DWithin(
                        centroid::geography,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                        %s
                      )
                    """,
                    (prior_ids, label, det["lon"], det["lat"], PRITHVI_MULTI_TEMPORAL_MATCH_RADIUS_M),
                )
                pc = int((cur.fetchone() or {}).get("pass_count") or 0)
                consistency[label] = {"pass_count": pc, "consistent": pc >= 1}
        if not consistency:
            continue
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE detections
                SET metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb
                WHERE id = %s
                """,
                (
                    json.dumps({
                        "prithvi_temporal_consistency": consistency,
                        "prithvi_temporal_priors": prior_ids,
                    }),
                    det["id"],
                ),
            )
            updated += 1

    record_timeline_event(
        "GEOINT",
        "prithvi_multitemporal_complete",
        f"Prithvi multi-temporal pass {pass_id}: tagged {updated} detections across {len(priors) + 1} passes",
        {"pass_id": pass_id, "prior_pass_ids": prior_ids, "updated": updated},
    )
    publish_event("imagery", {
        "type": "prithvi_multitemporal_complete",
        "pass_id": pass_id,
        "prior_pass_ids": prior_ids,
        "updated": updated,
    })
    return {"status": "ok", "pass_id": pass_id, "prior_pass_ids": prior_ids, "updated": updated}


# ============================================================================
# Audio transcription — runs faster-whisper on a worker host. Opt-in via
# WHISPER_ENABLED=1; on hosts without faster-whisper installed the task marks
# the transcript row as "failed" with a clear error instead of pretending.
# ============================================================================


@celery_app.task(name="worker.transcribe_audio", queue="default")
def transcribe_audio(document_id: int, audio_path: str) -> dict:
    if os.getenv("WHISPER_ENABLED", "0") != "1":
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE transcripts SET status='skipped', text=%s WHERE document_id=%s",
                ("Transcription disabled: set WHISPER_ENABLED=1.", document_id),
            )
        return {"status": "skipped"}
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE transcripts SET status='failed', text=%s WHERE document_id=%s",
                (f"faster-whisper not installed: {exc}", document_id),
            )
        return {"status": "failed", "error": str(exc)}

    model_size = os.getenv("WHISPER_MODEL", "base")
    device = os.getenv("WHISPER_DEVICE", "auto")
    try:
        model = WhisperModel(model_size, device=device, compute_type="int8")
        segments_iter, info = model.transcribe(audio_path)
        segments = []
        full_text_parts = []
        for seg in segments_iter:
            segments.append({"start": seg.start, "end": seg.end, "text": seg.text})
            full_text_parts.append(seg.text)
        full_text = "".join(full_text_parts).strip() or "(empty audio)"
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE transcripts SET
                  text=%s, status='ready', confidence=%s, language=%s, segments=%s
                WHERE document_id=%s
                """,
                (full_text, 1.0, info.language or "unknown", json.dumps(segments), document_id),
            )
        publish_event(
            "ops",
            {"type": "transcript_ready", "document_id": document_id, "language": info.language},
        )
        return {"status": "ready", "language": info.language, "segments": len(segments)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("transcription failed for document %s", document_id)
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE transcripts SET status='failed', text=%s WHERE document_id=%s",
                (f"Transcription failed: {exc}", document_id),
            )
        return {"status": "failed", "error": str(exc)}


# ============================================================================
# Training — invokes a real training entrypoint at backend/scripts/train.py.
# If no GPU/profile is detected, the task fails the job rather than silently
# pretending it succeeded.
# ============================================================================


@celery_app.task(name="worker.train_model", queue="default")
def train_model(job_id: int) -> dict:
    with postgis_db.get_cursor() as cur:
        cur.execute(
            "SELECT id, name, dataset_path, epochs, status, metrics FROM training_jobs WHERE id=%s",
            (job_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"status": "missing"}
    job = dict(row)
    gpu = os.getenv("SAM3_GPU_PROFILE") or os.getenv("CUDA_VISIBLE_DEVICES")
    if not gpu:
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE training_jobs SET status='failed', metrics = metrics || %s::jsonb WHERE id=%s",
                (json.dumps({"error": "no GPU profile"}), job_id),
            )
        return {"status": "failed", "error": "no GPU profile"}

    train_script = Path(__file__).resolve().parent / "scripts" / "train.py"
    if not train_script.exists():
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE training_jobs SET status='failed', metrics = metrics || %s::jsonb WHERE id=%s",
                (json.dumps({"error": "scripts/train.py not present"}), job_id),
            )
        return {"status": "failed", "error": "scripts/train.py missing"}

    cmd = [
        "python", str(train_script),
        "--job", str(job_id),
        "--dataset", str(job.get("dataset_path") or ""),
        "--epochs", str(int(job.get("epochs") or 1)),
        "--out", str(Path(os.getenv("MODEL_OUT_DIR", "/data/models")) / f"job-{job_id}"),
    ]
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("UPDATE training_jobs SET status='running' WHERE id=%s", (job_id,))
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        ok = completed.returncode == 0
        metrics = {
            "stdout_tail": (completed.stdout or "")[-2000:],
            "stderr_tail": (completed.stderr or "")[-2000:],
            "return_code": completed.returncode,
        }
        status = "done" if ok else "failed"
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE training_jobs SET status=%s, metrics = metrics || %s::jsonb WHERE id=%s",
                (status, json.dumps(metrics), job_id),
            )
        publish_event("ops", {"type": "training_finished", "job_id": job_id, "status": status})
        return {"status": status, **metrics}
    except Exception as exc:  # noqa: BLE001
        logger.exception("train_model failed for job %s", job_id)
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE training_jobs SET status='failed', metrics = metrics || %s::jsonb WHERE id=%s",
                (json.dumps({"error": str(exc)}), job_id),
            )
        return {"status": "failed", "error": str(exc)}


# ============================================================================
# Beat-driven housekeeping: collection-task scheduler and feed pollers.
# ============================================================================


COLLECTION_TASK_TTL_HOURS = env_int("COLLECTION_TASK_TTL_HOURS", 72)


@celery_app.task(name="worker.tick_collection_scheduler", queue="default")
def tick_collection_scheduler() -> dict:
    """Transition proposed→scheduled and scheduled→expired based on age + priority."""
    from platform_schema import ensure_collection_tables
    ensure_collection_tables()
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE collection_tasks
            SET status = 'scheduled',
                scheduled_for = NOW() + (
                    CASE lower(coalesce(priority, ''))
                        WHEN 'high'   THEN INTERVAL '1 hour'
                        WHEN 'medium' THEN INTERVAL '6 hours'
                        WHEN 'low'    THEN INTERVAL '24 hours'
                        ELSE               INTERVAL '6 hours'
                    END
                ),
                updated_at = NOW()
            WHERE status = 'proposed'
            RETURNING id
            """
        )
        scheduled_ids = [int(r["id"]) for r in cur.fetchall()]

        cur.execute(
            """
            UPDATE collection_tasks
            SET status = 'expired', updated_at = NOW()
            WHERE status = 'scheduled'
              AND created_at < NOW() - (%s || ' hours')::interval
            RETURNING id
            """,
            (COLLECTION_TASK_TTL_HOURS,),
        )
        expired_ids = [int(r["id"]) for r in cur.fetchall()]

    if scheduled_ids or expired_ids:
        publish_event("ops", {
            "type": "collection_tasks_ticked",
            "scheduled": scheduled_ids,
            "expired": expired_ids,
        })
    return {"scheduled": len(scheduled_ids), "expired": len(expired_ids)}


@celery_app.task(name="worker.tick_feed_poll", queue="default")
def tick_feed_poll() -> dict:
    """Poll all enabled HTTP/HTTPS feed_sources whose poll interval has elapsed."""
    from feed_collectors import poll_http_feed
    from platform_schema import ensure_feed_tables
    ensure_feed_tables()
    with postgis_db.get_cursor() as cur:
        cur.execute(
            """
            SELECT id, name, feed_type, protocol, endpoint, parser, metadata,
                   poll_interval_seconds, last_seen
            FROM feed_sources
            WHERE enabled = TRUE
              AND lower(protocol) IN ('http', 'https')
              AND (
                last_seen IS NULL
                OR last_seen < NOW() - (coalesce(poll_interval_seconds, 60) || ' seconds')::interval
              )
            ORDER BY coalesce(last_seen, '1970-01-01'::timestamptz) ASC
            LIMIT 20
            """
        )
        due = [dict(r) for r in cur.fetchall()]

    polled = 0
    total_events = 0
    for source in due:
        try:
            events = poll_http_feed(source)
        except Exception as exc:  # noqa: BLE001
            logger.warning("feed poll failed for %s: %s", source.get("name"), exc)
            with postgis_db.get_cursor(commit=True) as cur:
                cur.execute(
                    """
                    UPDATE feed_sources
                    SET status = 'error', last_error = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (str(exc)[:1000], source["id"]),
                )
            continue
        if events:
            with postgis_db.get_cursor(commit=True) as cur:
                for evt in events:
                    lat = evt.get("latitude")
                    lon = evt.get("longitude")
                    if lat is not None and lon is not None:
                        cur.execute(
                            """
                            INSERT INTO feed_events (source_id, event_type, payload, geom, observed_at)
                            VALUES (%s, %s, %s::jsonb, ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                                    COALESCE(%s::timestamptz, NOW()))
                            """,
                            (
                                source["id"],
                                evt.get("event_type", "observation"),
                                json.dumps(evt.get("payload") or {}, default=str),
                                lon, lat,
                                evt.get("observed_at"),
                            ),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO feed_events (source_id, event_type, payload, observed_at)
                            VALUES (%s, %s, %s::jsonb, COALESCE(%s::timestamptz, NOW()))
                            """,
                            (
                                source["id"],
                                evt.get("event_type", "observation"),
                                json.dumps(evt.get("payload") or {}, default=str),
                                evt.get("observed_at"),
                            ),
                        )
                total_events += len(events)
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE feed_sources
                SET status = 'connected', last_seen = NOW(), last_error = NULL, updated_at = NOW()
                WHERE id = %s
                """,
                (source["id"],),
            )
        polled += 1

    if total_events:
        publish_event("feeds", {"type": "feed_events_collected", "polled": polled, "events": total_events})
    return {"polled": polled, "events": total_events, "due": len(due)}

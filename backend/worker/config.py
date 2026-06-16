"""Worker foundation: imports, env constants, loaders, shared read-only helpers.

Sliced verbatim from worker_legacy.py. Every worker.* module does
`from worker.config import *` to inherit this namespace.
"""

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
import queue
import tempfile
import base64
import ipaddress
import socket
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from celery import Celery

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from cascade_delete import affected_track_ids, purge_empty_tracks, purge_object_details
from database import db, postgis_db
import rasterio
from rasterio.io import MemoryFile
from rasterio.windows import Window
from shapely.geometry import Polygon, MultiPolygon
import numpy as np
from PIL import Image
from imagery_metadata import extract_raster_metadata
from calibration import calibrate_confidence
from detection_evidence import apply_evidence_ranking
from detection_policy import (
    active_detection_policy,
    detection_decision,
    display_label_for,
    parent_class_for_label,
)
from size_estimation import estimate_size
from candidate_linking import rank_candidate_links
from threat_assessment import (
    assess_detection_threat,
    clean_detection_class,
    conservative_detection_ontology,
    detection_ontology,
)
from ontology import normalize as ontology_normalize
from reference_platform_db import attach_identification_candidates
import provider_lifecycle
from chip_prep_profiler import (
    stage_timer as _chip_stage_timer,
    record as _chip_record,
    snapshot as _chip_snapshot,
    is_enabled as _chip_profile_enabled,
)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
INFERENCE_SAM3_URL = os.getenv("INFERENCE_SAM3_URL", "http://inference-sam3:8001")
IMAGERY_PATH = os.getenv("IMAGERY_PATH", "/data/imagery")
REFERENCE_ID_AUTO_THRESHOLD = float(os.getenv("REFERENCE_ID_AUTO_THRESHOLD", "0.85"))

# Defined before any module-scope caller — _load_per_class_valid_fractions()
# runs at import time and logs on malformed env JSON; a later definition made
# that branch a NameError that prevented the worker from booting.
logger = logging.getLogger(__name__)


def _setdefault_gdal_env() -> None:
    """Apply tuned GDAL defaults before any rasterio.open() runs.

    `setdefault` so operators can override via .env / docker-compose. Effects:
    * `GDAL_CACHEMAX` — larger block cache so adjacent windowed reads hit
      the cache instead of re-decompressing.
    * `GDAL_DISABLE_READDIR_ON_OPEN` — skip sidecar lookups (no .aux.xml,
      .ovr probing) when we know we only want the raster itself.
    * `GDAL_HTTP_MERGE_CONSECUTIVE_RANGES` + `GDAL_HTTP_MULTIPLEX` +
      `GDAL_HTTP_VERSION=2` — coalesce + HTTP/2-multiplex range GETs for
      remote COGs (vsicurl path).
    * `VSI_CACHE` / `VSI_CACHE_SIZE` / `CPL_VSIL_CURL_CACHE_SIZE` — per-file
      and global LRU cache for vsicurl reads.
    * `CPL_VSIL_CURL_ALLOWED_EXTENSIONS` — restrict speculative HEADs.
    * `GTIFF_VIRTUAL_MEM_IO` — mmap path for uncompressed local TIFFs;
      silently no-ops on compressed inputs.

    GPU env (`SAM3_*`, `CUDA_VISIBLE_DEVICES`, etc.) is owned by
    scripts/configure_host.py; none of those are touched here.
    """
    defaults = {
        "GDAL_CACHEMAX": "1024",  # MB; explicit unit avoided pre-GDAL-3.11
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "GDAL_HTTP_MULTIPLEX": "YES",
        "GDAL_HTTP_VERSION": "2",
        "VSI_CACHE": "TRUE",
        "VSI_CACHE_SIZE": "5000000",
        "CPL_VSIL_CURL_CACHE_SIZE": "200000000",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff,.jp2",
        "GTIFF_VIRTUAL_MEM_IO": "YES",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


_setdefault_gdal_env()


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
# Phase 1.3: second-scale chip pass at a smaller window so the model gets a
# higher pixel-per-object budget on small targets (TELs, fuel bowsers, light
# armour, etc.). When > 0 and != DEFAULT_INFERENCE_CHIP_SIZE, slice_and_infer
# runs the second pass after the main pass; both passes share the dedupe index
# so duplicates across scales are suppressed by NMS. Default 504 (= half the
# 1008 main chip): ON for dense-scene small-object recall — this is the
# highest-cost recall knob (~+1 inference pass per scene). Set 0 to opt out on
# throughput-bound hosts. See docs/decisions/dense-scene-recall-defaults.md.
INFERENCE_SMALL_OBJECT_CHIP_SIZE = env_int("INFERENCE_SMALL_OBJECT_CHIP_SIZE", 504)
INFERENCE_SMALL_OBJECT_OVERLAP = env_int("INFERENCE_SMALL_OBJECT_OVERLAP", 128)
INFERENCE_SMALL_OBJECT_MAX_CHIPS = env_int(
    "INFERENCE_SMALL_OBJECT_MAX_CHIPS", _INFERENCE_PROFILE_DEFAULTS["max_chips"] or 0
)
# Optional coarse full-scene pass: when enabled, slice_and_infer runs ONE extra
# inference over the WHOLE image downsampled to ~chip_size (read from COG
# overviews). This catches objects larger than a single chip (runways, piers,
# large facilities) that the sliding-window grid only ever sees fragmented.
# Shares the dedupe index with the grid passes, so a large object detected both
# whole (full-scene) and fragmented (main pass) is fused/suppressed. Default OFF
# so the standard grid behaviour is unchanged.
INFERENCE_FULL_SCENE_PASS = env_bool("INFERENCE_FULL_SCENE_PASS", False)
# Pad edge RGB chips up to the full chip_size square before sending to SAM3.
# torch.compile (SAM3_COMPILE_IMAGE) specialises on input shape, so variable-size
# edge chips (the last row/col of the grid) would miss the compiled graph (fall
# back to eager / recompile). Padding them to a fixed chip_size x chip_size makes
# EVERY chip hit the same compiled graph (~6x faster). The padded region is black
# and is marked invalid in the chip's valid_mask, so any detection landing there
# is clipped — georef is unaffected (the window origin/transform are unchanged;
# only the normalization basis grows to chip_size). RGB/optical only; default ON
# to pair with SAM3_COMPILE_IMAGE=1 (the compiled graph specialises on shape, so
# unpadded edge chips would recompile / fall back to eager). Set 0 if compile is
# off. See docs/decisions/sam3-compile-and-chip-padding-2026-06-14.md.
INFERENCE_PAD_CHIPS_TO_SIZE = env_bool("INFERENCE_PAD_CHIPS_TO_SIZE", True)
def _default_reader_pool_size() -> int:
    """Default for the Phase 3 reader thread pool.

    Reader threads do GDAL windowed reads + numpy encode; GDAL releases
    the GIL during `GDALRasterIO()` so threads scale. Bounded to 4 by
    default to limit open dataset handles and avoid OS file-descriptor
    pressure on large rasters.
    """
    return min(4, max(1, os.cpu_count() or 1))


INFERENCE_READER_POOL_SIZE = max(1, env_int("INFERENCE_READER_POOL_SIZE", _default_reader_pool_size()))


def _default_chip_concurrency() -> int:
    """Pick a sensible default chip-POST concurrency.

    Per-profile baseline (recall=2, fast=1) was set when the producer
    serialised one chip read at a time. With Phase 3's reader pool the
    poster pool can absorb more in-flight chips before saturating the
    inference service, so we bump the lower bound to ``min(8, cpu_count)``
    while still respecting an explicit profile override greater than that
    bound. Operators set ``INFERENCE_CHIP_CONCURRENCY`` to pin a value.
    """
    profile_value = int(_INFERENCE_PROFILE_DEFAULTS["concurrency"])
    floor = min(8, max(1, (os.cpu_count() or 1)))
    return max(profile_value, floor)


INFERENCE_CHIP_CONCURRENCY = max(1, env_int("INFERENCE_CHIP_CONCURRENCY", _default_chip_concurrency()))
INFERENCE_CHIP_TIMEOUT_S = env_int("INFERENCE_CHIP_TIMEOUT_S", 120)
# Live (streaming) detections: the per-chip `detections_partial` WS event carries
# map-ready GeoJSON features so the frontend renders detections AS each chip
# completes, instead of waiting ~90s for the whole pass. ON by default; set 0 to
# fall back to count-only events + an end-of-pass load. A chip that exceeds the
# feature cap streams counts only (the final load still reconciles), bounding the
# WS message size. See docs/decisions/why-live-streaming-detections.md.
LIVE_DETECTIONS_STREAM = os.getenv("LIVE_DETECTIONS_STREAM", "1").strip().lower() in {"1", "true", "yes", "on"}
LIVE_DETECTIONS_MAX_FEATURES = env_int("LIVE_DETECTIONS_MAX_FEATURES", 400)


def _det_to_live_feature(det: dict) -> dict | None:
    """Build a compact, map-ready GeoJSON Feature from a stored detection dict.

    Used only for the live preview embedded in `detections_partial`; the
    authoritative feature set is loaded from
    /api/detections/geojson-lite when the pass completes (reconciliation).
    Returns None if the detection has no usable geometry / id yet.
    """
    det_id = det.get("id")
    geo_polygon = det.get("geo_polygon")
    if det_id is None or not geo_polygon or len(geo_polygon) < 6:
        return None
    pts = list(zip(geo_polygon[0::2], geo_polygon[1::2]))
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    conf = det.get("confidence")
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[lon, lat] for lon, lat in pts]]},
        "properties": {
            "id": det_id,
            "class": det.get("class", "Unknown"),
            "confidence": conf,
            "calibrated_confidence": det.get("calibrated_confidence", conf),
            "pass_id": det.get("pass_id"),
            "review_status": det.get("review_status", "review_candidate"),
            "live_preview": True,
        },
    }


# A poisoned CUDA context makes inference-sam3 self-heal by exiting and letting
# `restart: unless-stopped` respawn it with a clean context (~100 s). See
# docs/decisions/why-exit-on-poisoned-cuda-context.md. During that window chip
# POSTs fail at the connection level (refused / DNS / remote-disconnected, all
# `requests.ConnectionError`). Rather than silently scoring those chips as
# zero-detection, the chip POST waits for /health to come back and retries — so
# a self-heal restart costs ~one restart of wall-clock instead of dropping the
# rest of the scene. Bounded so a *persistent* crash loop still terminates.
INFERENCE_RESTART_RETRY_MAX = max(0, env_int("INFERENCE_RESTART_RETRY_MAX", 3))
INFERENCE_RESTART_WAIT_S = max(1, env_int("INFERENCE_RESTART_WAIT_S", 180))
# After retries are exhausted, fail the pass loudly if more than this fraction
# of attempted chips still failed — a near-empty result from a half-inferenced
# scene is worse than an honest failure for an analyst. 0 disables the fraction
# gate (the all-chips-failed gate still applies).
INFERENCE_MAX_FAILED_CHIP_FRACTION = max(
    0.0, min(1.0, env_float("INFERENCE_MAX_FAILED_CHIP_FRACTION", 0.05))
)
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
INFERENCE_MAX_PENDING_CHIPS = max(
    1,
    env_int("INFERENCE_MAX_PENDING_CHIPS", INFERENCE_CHIP_CONCURRENCY * 4),
)
# Floor for the adaptive concurrency back-off. The inference service runs a
# replica per GPU and serves /detect_raw lock-free, so the back-off must never
# starve that pool — keep at least this many chips in flight. Set to your
# inference GPU/replica count (default 4). Clamped to the pending ceiling.
INFERENCE_MIN_PENDING_CHIPS = max(
    1,
    min(env_int("INFERENCE_MIN_PENDING_CHIPS", 4), INFERENCE_MAX_PENDING_CHIPS),
)


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw:
        return default
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or default


FMV_DEFAULT_PROMPTS = _csv_env("FMV_DEFAULT_PROMPTS", ("vehicle", "person", "building"))
INFERENCE_CHIP_SPOOL_MAX_BYTES = max(
    64 * 1024,
    env_int("INFERENCE_CHIP_SPOOL_MAX_BYTES", 4 * 1024 * 1024),
)


__all__ = [n for n in dir() if not n.startswith("__")]

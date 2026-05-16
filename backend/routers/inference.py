"""Inference-service proxy routes + DB-stored confidence overrides + admin dashboard."""

from __future__ import annotations

import json
import logging
import os
import time

import requests
from fastapi import APIRouter, Depends, HTTPException, Query

from auth import SessionUser, get_current_user, require_admin
from database import postgis_db
from detection_policy import active_detection_policy
from platform_schema import ensure_platform_tables
from schemas import ConfidenceConfig

logger = logging.getLogger(__name__)

router = APIRouter()

_INFERENCE_SAM3_URL = os.getenv("INFERENCE_SAM3_URL", "http://inference-sam3:8001")


@router.post("/api/inference/load")
def inference_load(profile: str = Query(...)):
    """Proxy: ask the inference service to load a named model profile."""
    try:
        resp = requests.post(
            f"{_INFERENCE_SAM3_URL}/load",
            params={"profile": profile},
            timeout=600,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"inference unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@router.post("/api/inference/unload")
def inference_unload():
    """Proxy: ask the inference service to free GPU memory.

    The inference container exits on /unload so docker compose can respawn
    it with a clean CUDA context (SAM3 model refs cannot be released
    in-process). We block until /health responds again so the next
    /load call from the frontend doesn't race against startup."""
    try:
        resp = requests.post(f"{_INFERENCE_SAM3_URL}/unload", timeout=120)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"inference unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    body = resp.json()
    if body.get("restarting"):
        deadline = time.time() + 30
        while time.time() < deadline:
            time.sleep(0.5)
            try:
                h = requests.get(f"{_INFERENCE_SAM3_URL}/health", timeout=2)
                if h.status_code == 200:
                    return body
            except requests.RequestException:
                continue
    return body


@router.get("/api/inference/health")
def inference_health():
    """Proxy: return inference service health."""
    try:
        resp = requests.get(f"{_INFERENCE_SAM3_URL}/health", timeout=10)
        return resp.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"inference unreachable: {exc}") from exc


def _read_inference_config() -> dict:
    ensure_platform_tables()
    with postgis_db.get_cursor() as cur:
        cur.execute("SELECT config FROM inference_config WHERE id = 1")
        row = cur.fetchone()
    cfg = (row[0] if row and not isinstance(row, dict) else (row or {}).get("config")) or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except json.JSONDecodeError:
            cfg = {}
    return cfg or {}


@router.get("/api/inference/confidence-overrides")
def get_confidence_overrides(user: SessionUser = Depends(get_current_user)):
    policy = active_detection_policy()
    cfg = _read_inference_config()
    db_overrides = cfg.get("per_class_confidence_overrides") or {}
    return {
        "per_class_confidence_overrides": db_overrides,
        "env_per_class_confidence_overrides": policy.get("class_thresholds", {}),
        "global_floor": cfg.get("global_floor"),
        "env_global_floor": policy.get("global_confidence_floor"),
        "high_confidence_threshold": cfg.get("high_confidence_threshold"),
        "env_high_confidence_threshold": policy.get("high_confidence_threshold"),
    }


@router.put("/api/inference/confidence-overrides")
def put_confidence_overrides(body: ConfidenceConfig, user: SessionUser = Depends(require_admin)):
    """Replace the DB-stored confidence overrides and invalidate the policy cache.

    Empty dict clears all DB overrides (env values take over)."""
    payload = {
        "per_class_confidence_overrides": {k: float(v) for k, v in (body.per_class_confidence_overrides or {}).items()},
        "global_floor": body.global_floor,
        "high_confidence_threshold": body.high_confidence_threshold,
    }
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO inference_config (id, config, updated_at, updated_by) "
            "VALUES (1, %s::jsonb, NOW(), %s) "
            "ON CONFLICT (id) DO UPDATE "
            "SET config = EXCLUDED.config, updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by",
            (json.dumps(payload), user.username),
        )
    try:
        from detection_policy import invalidate_policy_cache
        invalidate_policy_cache()
    except Exception:
        pass
    return {"saved": True, **payload}


# Component → dashboard-row mapping. Single source of truth used by the
# dashboard endpoint to build the models table from real inference-sam3
# health data instead of falling back to a hardcoded list.
#
# `flag = None` means always-loaded with whatever profile is active (sam3
# image/video are part of every profile). `version_from` is either the
# top-level field name (e.g. "model_version") or "model_versions.<key>"
# from inference-sam3 /health. `subslugs` collapses multi-component models
# (YOLOE has -pf and -seg variants) into a single dashboard row.
_COMPONENT_ROWS: tuple[dict, ...] = (
    {"slug": "sam3_image",     "name": "SAM 3 image",      "flag": None,             "version_from": "model_version"},
    {"slug": "sam3_video",     "name": "SAM 3.1 video",    "flag": None,             "version_from": "model_version"},
    {"slug": "dinov3_sat",     "name": "DINOv3-SAT",       "flag": "dinov3_sat",     "version_from": "model_versions.dinov3_sat"},
    {"slug": "prithvi",        "name": "Prithvi-EO",       "flag": "prithvi",        "version_from": "model_versions.prithvi_backbone"},
    {"slug": "terramind",      "name": "TerraMind",        "flag": "terramind",      "version_from": "model_versions.terramind"},
    {"slug": "dota_obb",       "name": "DOTA-OBB",         "flag": "dota_obb",       "version_from": None},
    {"slug": "grounding_dino", "name": "Grounding-DINO",   "flag": "grounding_dino", "version_from": None},
    {"slug": "yoloe",          "name": "YOLOE-26x",        "flag": "yoloe",          "version_from": None,
     "subslugs": ("yoloe_pf", "yoloe_seg")},
)


def _resolve_version(health: dict, version_from: str | None) -> str | None:
    if not version_from:
        return None
    if "." in version_from:
        section, key = version_from.split(".", 1)
        return (health.get(section) or {}).get(key)
    return health.get(version_from)


def _build_models(health: dict, reachable: bool) -> list[dict]:
    """Build the dashboard model rows from an inference-sam3 /health payload.

    `reachable=False` short-circuits every row to `status="offline"` so the
    UI shows an honest "service down" picture instead of stale data."""
    replicas = health.get("replicas") or []
    load_flags = health.get("load_flags") or {}
    metrics = health.get("metrics") or {}
    rows: list[dict] = []
    for row in _COMPONENT_ROWS:
        slug = row["slug"]
        subslugs: tuple[str, ...] = row.get("subslugs") or (slug,)

        # Loaded if any replica has any sub-slug loaded.
        loaded = False
        for r in replicas:
            comp = r.get("components") or {}
            if any(bool(comp.get(s)) for s in subslugs):
                loaded = True
                break

        if not reachable:
            status = "offline"
        elif loaded:
            status = "online"
        elif row["flag"] is not None and not load_flags.get(row["flag"]):
            status = "disabled"
        else:
            status = "configured"

        version = _resolve_version(health, row.get("version_from"))
        sub_versions: dict[str, str] | None = None
        if row.get("subslugs"):
            mv = health.get("model_versions") or {}
            sub_versions = {s: mv.get(s) for s in subslugs if mv.get(s)}

        # Metrics: for combined rows pick the higher-traffic sub-slug for
        # top-level numbers, but expose every sub-slug under submetrics.
        submetrics: dict[str, dict] | None = None
        primary_metrics: dict = {}
        if row.get("subslugs"):
            submetrics = {s: metrics.get(s) or {} for s in subslugs}
            primary = max(subslugs, key=lambda s: (metrics.get(s) or {}).get("requests") or 0)
            primary_metrics = metrics.get(primary) or {}
        else:
            primary_metrics = metrics.get(slug) or {}

        rows.append({
            "id": slug,
            "name": row["name"],
            "version": version,
            "sub_versions": sub_versions,
            "status": status,
            "requests": primary_metrics.get("requests") or 0,
            "errors": primary_metrics.get("errors") or 0,
            "last_request_ts": primary_metrics.get("last_request_ts"),
            "p50_ms": primary_metrics.get("p50_ms"),
            "p95_ms": primary_metrics.get("p95_ms"),
            "submetrics": submetrics,
        })
    return rows


@router.get("/api/inference/dashboard")
def inference_dashboard(user: SessionUser = Depends(get_current_user)):
    """Aggregate health + model status for the Admin · Health view."""
    base: dict = {
        "gpu": {
            "model": os.getenv("GPU_MODEL") or "unknown",
            "profile": os.getenv("SAM3_GPU_PROFILE") or os.getenv("CUDA_VISIBLE_DEVICES") or "cpu",
            "cuda_version": os.getenv("SAM3_CUDA_VERSION") or "n/a",
        },
        "mode": "online" if os.getenv("OPENAI_API_BASE") else "offline_safe",
        "vram_total_gib": None,
        "vram_used_gib": None,
        "device": None,
        "profile_loaded": None,
        "available_profiles": [],
        "pool_size": 0,
        "replicas": [],
        "active_requests": 0,
        "uptime_s": None,
        "system": {},
        "request_rate_60s": None,
        "models": [],
    }
    health: dict = {}
    reachable = False
    try:
        resp = requests.get(f"{_INFERENCE_SAM3_URL}/health", timeout=2.5)
        if resp.status_code == 200:
            health = resp.json() if resp.text else {}
            reachable = True
        else:
            base["inference_error"] = f"sidecar returned HTTP {resp.status_code}"
    except Exception as exc:
        base["inference_error"] = str(exc)

    base["vram_total_gib"] = health.get("vram_total_gib") or health.get("vram_total_gb")
    base["vram_used_gib"] = health.get("vram_used_gib") or health.get("vram_used_gb")
    base["device"] = health.get("device")
    base["profile_loaded"] = health.get("current_profile") or health.get("profile_loaded") or health.get("profile")
    base["available_profiles"] = health.get("available_profiles") or []
    base["pool_size"] = health.get("pool_size") or 0
    base["replicas"] = health.get("replicas") or []
    base["active_requests"] = health.get("active_requests") or 0
    base["uptime_s"] = health.get("uptime_s")
    base["system"] = health.get("system") or {}
    base["request_rate_60s"] = health.get("request_rate_60s")
    base["models"] = _build_models(health, reachable)
    return base

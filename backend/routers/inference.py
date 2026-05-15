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


@router.get("/api/inference/dashboard")
def inference_dashboard(user: SessionUser = Depends(get_current_user)):
    """Aggregate health + model status for the Admin · Health view."""
    base = {
        "gpu": {
            "model": os.getenv("GPU_MODEL") or "unknown",
            "profile": os.getenv("SAM3_GPU_PROFILE") or os.getenv("CUDA_VISIBLE_DEVICES") or "cpu",
            "cuda_version": os.getenv("SAM3_CUDA_VERSION") or "n/a",
        },
        "vram_total_gib": None,
        "vram_used_gib": None,
        "models": [],
        "mode": "online" if os.getenv("OPENAI_API_BASE") else "offline_safe",
    }
    try:
        resp = requests.get(f"{_INFERENCE_SAM3_URL}/health", timeout=2.5)
        if resp.status_code == 200:
            data = resp.json() if resp.text else {}
            base["vram_total_gib"] = data.get("vram_total_gib") or data.get("vram_total_gb")
            base["vram_used_gib"] = data.get("vram_used_gib") or data.get("vram_used_gb")
            base["models"] = data.get("models") or data.get("loaded_models") or []
            base["device"] = data.get("device")
            base["profile_loaded"] = data.get("profile_loaded") or data.get("profile")
    except Exception as exc:
        base["inference_error"] = str(exc)
    if not base["models"]:
        env_models = [
            {"id": "sam3", "name": "SAM 3 image", "version": os.getenv("SAM3_MODEL_VERSION", "facebook/sam3"), "status": "configured"},
            {"id": "dinov3-sat", "name": "DINOv3-SAT", "version": os.getenv("DINOV3_SAT_MODEL_ID", "facebook/dinov3-vitl16-pretrain-sat493m"), "status": "configured"},
            {"id": "prithvi", "name": "Prithvi-EO", "version": os.getenv("PRITHVI_BACKBONE_ID", "ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL"), "status": "configured"},
            {"id": "terramind", "name": "TerraMind", "version": os.getenv("TERRAMIND_MODEL_ID", "terramind_v1_large"), "status": "configured"},
        ]
        base["models"] = env_models
    return base

"""Model + training-job routes for the Admin · AI models view."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from auth import SessionUser, require_admin
from database import postgis_db
from events import normalize_domain, publish_event, record_timeline_event
from files import safe_filename, save_upload_file
from platform_schema import ensure_platform_tables
from schemas import TrainingJobCreate

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/models/datasets")
def list_model_datasets(user: SessionUser = Depends(require_admin)):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, name, dataset_type, domain, file_path, status, metadata, created_at, updated_at
            FROM datasets
            ORDER BY created_at DESC
        """)
        return {"datasets": [dict(row) for row in cursor.fetchall()]}


@router.get("/api/models")
def list_models(user: SessionUser = Depends(require_admin)):
    """List deployed/candidate detection models — used by the Admin · Models view."""
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, name, version, model_path, status, metrics, promoted, created_at
            FROM models
            ORDER BY promoted DESC, created_at DESC
        """)
        return {"models": [dict(row) for row in cursor.fetchall()]}


@router.post("/api/models/datasets")
def upload_model_dataset(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    dataset_type: str = Form("object_detection"),
    domain: str = Form("GEOINT"),
    user: SessionUser = Depends(require_admin),
):
    ensure_platform_tables()
    filename = safe_filename(file.filename or "dataset.zip")
    dataset_id = uuid.uuid4().hex
    root = Path(os.getenv("DATASET_PATH", "/data/datasets"))
    root.mkdir(parents=True, exist_ok=True)
    local_path = root / f"{dataset_id}_{filename}"
    size = save_upload_file(file, local_path)
    if size == 0:
        local_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded dataset is empty")
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO datasets (name, dataset_type, domain, file_path, status, metadata)
            VALUES (%s, %s, %s, %s, 'stored', %s)
            RETURNING id, name, dataset_type, domain, file_path, status, metadata, created_at, updated_at
        """, (
            name or filename,
            dataset_type,
            normalize_domain(domain, "GEOINT"),
            str(local_path),
            json.dumps({"bytes": size, "upload_id": dataset_id}),
        ))
        dataset = dict(cursor.fetchone())
    record_timeline_event("ADMIN", "dataset_uploaded", dataset["name"], {"dataset": dataset})
    return {"success": True, "dataset": dataset}


@router.post("/api/models/{model_id}/promote")
def promote_model(model_id: int, user: SessionUser = Depends(require_admin)):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("UPDATE models SET promoted = FALSE")
        cursor.execute("""
            UPDATE models
            SET promoted = TRUE, status = 'available'
            WHERE id = %s
            RETURNING id, name, version, model_path, status, metrics, promoted, created_at
        """, (model_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Model not found")
        model = dict(row)
    record_timeline_event("ADMIN", "model_promoted", model["name"], {"model_id": model_id})
    publish_event("ops", {"type": "model_promoted", "model": model})
    return {"success": True, "model": model}


@router.post("/api/training/jobs")
def create_training_job(req: TrainingJobCreate, user: SessionUser = Depends(require_admin)):
    """Queue a real training run. Requires GPU profile; otherwise rejects so the
    operator knows the job won't run instead of recording a fake 'queued'."""
    ensure_platform_tables()
    gpu_profile = os.getenv("SAM3_GPU_PROFILE") or os.getenv("CUDA_VISIBLE_DEVICES")
    if not gpu_profile:
        raise HTTPException(
            status_code=503,
            detail="no GPU profile detected — run scripts/configure_host.py or set SAM3_GPU_PROFILE before queuing training",
        )
    metrics = {
        "gpu_profile": gpu_profile,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO training_jobs (name, dataset_path, epochs, status, metrics)
            VALUES (%s, %s, %s, 'queued', %s)
            RETURNING id, name, dataset_path, epochs, status, metrics, created_at, updated_at
        """, (req.name, req.dataset_path, req.epochs, json.dumps(metrics)))
        job = dict(cursor.fetchone())
    try:
        from worker import train_model  # lazy import — worker depends on backend objects
        task = train_model.delay(job["id"])
        job["task_id"] = task.id
    except Exception as exc:  # noqa: BLE001
        logger.warning("queueing training task failed: %s", exc, exc_info=True)
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE training_jobs SET status = 'failed', metrics = metrics || %s::jsonb WHERE id = %s",
                (json.dumps({"error": str(exc)}), job["id"]),
            )
        raise HTTPException(status_code=503, detail=f"training worker unavailable: {exc}") from exc
    publish_event("training:%s" % job["id"], {"type": "training_queued", "job": job})
    publish_event("ops", {"type": "training_queued", "job": job})
    return {"success": True, "job": job}


@router.get("/api/training/jobs")
def list_training_jobs(user: SessionUser = Depends(require_admin)):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, name, dataset_path, epochs, status, metrics, created_at, updated_at
            FROM training_jobs
            ORDER BY created_at DESC
        """)
        return {"jobs": [dict(row) for row in cursor.fetchall()]}

"""Ingest router — upload dispatch, status, URL ingest.

Extracted from backend/main.py. Endpoints:

  GET    /api/ingest/uploads             — list active+recent jobs (reconciled with Celery state)
  GET    /api/ingest/jobs/{task_id}      — single Celery task state + progress
  POST   /api/ingest                     — trigger pipeline by image_url + sensor
  POST   /api/ingest/upload              — multipart upload + dispatch (imagery/FMV/document/audio/vector)
  POST   /api/ingest/url                 — queue URL retrieval + LLM extraction

The handlers here delegate to the worker modules (worker.imagery,
worker.fmv) and the helpers in files.py / video_metadata.py /
imagery_metadata.py — the same surfaces the legacy main.py used.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

import provider_lifecycle
from database import postgis_db
from events import publish_event, record_observation, record_timeline_event
from files import classify_upload, safe_filename, save_upload_file
from imagery_metadata import extract_raster_metadata
from ontology import ontology_default_prompts
from platform_schema import ensure_platform_tables
from schemas import IngestRequest, IngestUrlRequest
from fmv_helpers import fmv_public_url, probe_video, transcode_hls
from video_metadata import TelemetryMissingError, extract_telemetry


def _fmv_fallback_prompts() -> list[str]:
    """Lazy view of main.py's FMV_FALLBACK_PROMPTS to avoid import cycle."""
    from main import FMV_FALLBACK_PROMPTS as _p
    return list(_p)


# Module-level alias so existing references resolve; refreshed at call time.
FMV_FALLBACK_PROMPTS: list[str] = []
from worker import celery_app, process_fmv, process_satellite_imagery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


# ─── Helper: domain selection (kept inline because it's trivial) ───────────

def normalize_domain(value: Optional[str], default: str = "OSINT") -> str:
    if not value:
        return default
    return str(value).strip().upper() or default


def domain_for_media(media_type: str, sensor_type: Optional[str]) -> str:
    """Map media_type+sensor → coarse domain bucket used by record_observation."""
    if media_type == "fmv":
        return "FMV"
    if media_type == "imagery":
        return "GEOINT"
    return "OSINT"


# ─── Helper: read short text from a document (for ontology extraction) ─────

def read_document_text(path: str, limit: int = 12000) -> str:
    suffix = Path(path).suffix.lower()
    if suffix not in {".txt", ".csv", ".json", ".md", ".log"}:
        return ""
    try:
        return Path(path).read_text(errors="ignore")[:limit]
    except Exception:
        return ""


# ─── Helper: ontology extraction shim (forwarded from main.py) ─────────────
# Re-exported from main.py since the function is large and stateful; importing
# here keeps the wire-up shape clean and lets the eventual move happen later.

def run_ontology_update(*args, **kwargs):
    from main import run_ontology_update as _impl  # lazy to avoid cycle
    return _impl(*args, **kwargs)


# ─── Helper: celery state ↔ upload_jobs row reconciliation ─────────────────

def celery_status_for_task(task_id: Optional[str]) -> Optional[dict]:
    if not task_id:
        return None
    try:
        from celery.result import AsyncResult

        result = AsyncResult(task_id, app=celery_app)
        payload = {
            "task_id": task_id,
            "celery_state": result.state.lower(),
            "ready": result.ready(),
        }
        if isinstance(result.info, dict):
            payload.update(result.info)
        elif result.ready() and not result.successful():
            payload["error"] = str(result.result)
            payload["message"] = f"Imagery processing failed: {result.result}"
        elif result.successful() and isinstance(result.result, dict):
            payload.update(result.result)
        return payload
    except Exception as exc:
        return {
            "task_id": task_id,
            "celery_state": "unknown",
            "message": f"Unable to inspect task state: {exc}",
        }


def reconciled_upload_job(row: dict) -> dict:
    job = dict(row)
    metadata = job.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    task_status = celery_status_for_task(job.get("celery_task_id"))
    if not task_status:
        return job

    celery_state = task_status.get("celery_state")
    next_status = job.get("status")
    next_metadata = {**metadata, **task_status}

    if celery_state == "progress":
        next_status = "processing"
    elif celery_state == "success":
        next_status = "ready"
        next_metadata.setdefault("progress", 100)
        next_metadata.setdefault("stage", "ready")
        next_metadata.setdefault("message", "Imagery processing complete.")
    elif celery_state == "failure":
        next_status = "failed"
        next_metadata.setdefault("stage", "failed")
        next_metadata.setdefault("message", next_metadata.get("error", "Imagery processing failed."))
    elif celery_state in {"pending", "received", "started", "retry"}:
        next_metadata.setdefault("stage", "queued" if celery_state == "pending" else celery_state)
        next_metadata.setdefault("progress", 5 if celery_state == "pending" else 10)
        next_metadata.setdefault(
            "message",
            "Waiting for imagery worker." if celery_state == "pending" else "Imagery worker accepted the task.",
        )

    job["status"] = next_status
    job["metadata"] = next_metadata
    return job


# ─── GET /api/ingest/uploads ───────────────────────────────────────────────

@router.get("/uploads")
def list_upload_jobs():
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, upload_id, filename, file_path, media_type, handler, status,
                   celery_task_id, metadata, created_at, updated_at
            FROM upload_jobs
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 250
            """
        )
        return {"uploads": [reconciled_upload_job(dict(row)) for row in cursor.fetchall()]}


# ─── GET /api/ingest/jobs/{task_id} ────────────────────────────────────────

@router.get("/jobs/{task_id}")
def get_ingest_job(task_id: str):
    from celery.result import AsyncResult

    result = AsyncResult(task_id, app=celery_app)
    payload = {
        "task_id": task_id,
        "state": result.state.lower(),
        "ready": result.ready(),
        "successful": result.successful() if result.ready() else False,
    }
    if result.ready():
        if result.successful():
            payload["result"] = result.result
        else:
            payload["error"] = str(result.result)
    elif isinstance(result.info, dict):
        payload["progress"] = result.info
    return payload


# ─── POST /api/ingest ──────────────────────────────────────────────────────

@router.post("")
def trigger_ingest(req: IngestRequest):
    task = process_satellite_imagery.delay(req.image_url, req.sensor_type, req.acquisition_time)
    return {
        "success": True,
        "task_id": task.id,
        "status_url": f"/api/ingest/jobs/{task.id}",
        "message": "Satellite imagery pipeline initiated.",
    }


# ─── POST /api/ingest/upload ───────────────────────────────────────────────
# The big one — accepts any of imagery / FMV / vector / document / audio
# and dispatches the right downstream pipeline. Preserved verbatim from
# main.py for fidelity.

@router.post("/upload")
def upload_imagery(
    file: UploadFile = File(...),
    sensor_type: str = Form("Optical"),
    acquisition_time: Optional[str] = Form(None),
    auto_process: bool = Form(True),
    text_prompts: Optional[str] = Form(None),
    ontology_branch: Optional[str] = Form(None),
    modality: Optional[str] = Form(None),
    enabled_layers: Optional[str] = Form(None),
    allow_synthetic_telemetry: bool = Form(False),
):
    ensure_platform_tables()
    filename = safe_filename(file.filename or "upload.tif")
    media_type, handler = classify_upload(filename)

    if media_type in {"imagery", "fmv"}:
        try:
            provider_lifecycle.ensure_running()
        except Exception as exc:
            logger.warning("[UPLOAD] provider_lifecycle.ensure_running failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail=f"Failed to start sam3 inference container: {exc}",
            )

    if media_type == "fmv":
        upload_dir = Path(os.getenv("FMV_PATH", "/data/fmv")) / "incoming"
    else:
        upload_dir = Path(os.getenv("IMAGERY_PATH", "/data/imagery")) / "incoming"
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    local_path = upload_dir / f"{upload_id}_{filename}"

    size = save_upload_file(file, local_path)
    if size == 0:
        local_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    raster_metadata = extract_raster_metadata(local_path, include_hash=False) if media_type == "imagery" else {}
    effective_acquisition_time = acquisition_time or raster_metadata.get("acquisition_time")

    response = {
        "success": True,
        "file_path": str(local_path),
        "filename": filename,
        "bytes": size,
        "sensor_type": sensor_type,
        "acquisition_time": effective_acquisition_time,
        "auto_process": auto_process,
        "upload_id": upload_id,
        "media_type": media_type,
        "handler": handler,
        "metadata": raster_metadata,
    }
    domain = domain_for_media(media_type, sensor_type)
    celery_task_id = None
    status = "stored"
    upload_job_recorded = False

    if media_type == "imagery":
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO upload_jobs (upload_id, filename, file_path, media_type, handler, status, celery_task_id, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    upload_id, filename, str(local_path), media_type, handler, status, None,
                    json.dumps({
                        "sensor_type": sensor_type,
                        "acquisition_time": effective_acquisition_time,
                        "auto_process": auto_process,
                        "text_prompts": text_prompts,
                        "ontology_branch": ontology_branch,
                        "modality": modality,
                        "enabled_layers": enabled_layers,
                        "bytes": size,
                        "raster_metadata": raster_metadata,
                        "source_hash": None,
                        "source_filename": raster_metadata.get("source_filename") or filename,
                        "stage": "stored",
                        "progress": 0,
                        "message": "Upload stored.",
                    }),
                ),
            )
        upload_job_recorded = True

    if media_type == "imagery" and auto_process:
        parsed_enabled_layers = None
        if enabled_layers:
            try:
                parsed_enabled_layers = json.loads(enabled_layers)
                if not isinstance(parsed_enabled_layers, list):
                    parsed_enabled_layers = None
            except (TypeError, json.JSONDecodeError):
                parsed_enabled_layers = None
        task = process_satellite_imagery.delay(
            str(local_path), sensor_type, effective_acquisition_time, upload_id,
            enabled_layers=parsed_enabled_layers,
        )
        celery_task_id = task.id
        status = "queued"
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE upload_jobs
                SET status = %s,
                    celery_task_id = %s,
                    metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb,
                    updated_at = NOW()
                WHERE upload_id = %s
                """,
                (status, celery_task_id,
                 json.dumps({
                    "task_id": celery_task_id,
                    "acquisition_time": effective_acquisition_time,
                    "stage": "queued",
                    "progress": 5,
                    "message": "Imagery processing queued.",
                 }),
                 upload_id,
                ),
            )
        response.update({
            "task_id": task.id,
            "status_url": f"/api/ingest/jobs/{task.id}",
            "message": "Upload received and imagery pipeline queued.",
        })
    elif media_type == "fmv" and auto_process:
        fmv_root = Path(os.getenv("FMV_PATH", "/data/fmv"))
        clip_dir = fmv_root / upload_id
        clip_dir.mkdir(parents=True, exist_ok=True)
        clip_path = clip_dir / filename
        shutil.move(str(local_path), clip_path)
        metadata = probe_video(clip_path)
        hls_path = transcode_hls(clip_path, clip_dir)
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO fmv_clips (name, file_path, hls_path, duration_seconds, width, height, fps, status, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, name, file_path, hls_path, duration_seconds, width, height, fps, status, metadata, created_at, updated_at
                """,
                (
                    filename, str(clip_path), str(hls_path) if hls_path else None,
                    metadata["duration_seconds"], metadata["width"], metadata["height"], metadata["fps"],
                    "ready" if hls_path else "stored",
                    json.dumps({**metadata, "bytes": size, "upload_id": upload_id}),
                ),
            )
            clip = dict(cursor.fetchone())
            try:
                rows = extract_telemetry(
                    clip_path, clip["id"], clip["duration_seconds"], clip["fps"],
                    allow_synthetic=allow_synthetic_telemetry,
                )
            except TelemetryMissingError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            cursor.executemany(
                """
                INSERT INTO fmv_frames (clip_id, frame_index, timestamp_seconds, telemetry, footprint)
                VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326))
                ON CONFLICT (clip_id, frame_index) DO UPDATE SET
                    timestamp_seconds = EXCLUDED.timestamp_seconds,
                    telemetry = EXCLUDED.telemetry,
                    footprint = EXCLUDED.footprint
                """,
                rows,
            )
        clip["stream_url"] = fmv_public_url(clip.get("hls_path"), clip["file_path"])
        status = "ready"
        prompt_list = [item.strip() for item in (text_prompts or "").split(",") if item.strip()]
        if not prompt_list:
            try:
                prompt_list = ontology_default_prompts(None) or _fmv_fallback_prompts()
            except Exception as exc:
                logger.warning("ontology_default_prompts failed for /api/ingest FMV: %s", exc)
                prompt_list = _fmv_fallback_prompts()
        if prompt_list:
            task = process_fmv.delay(clip["id"], str(clip_path), prompt_list)
            celery_task_id = task.id
            status = "queued"
            clip["status"] = "queued"
            response.update({
                "task_id": task.id,
                "status_url": f"/api/ingest/jobs/{task.id}",
                "message": "FMV upload received and SAM3 tracking queued.",
                "clip": clip,
            })
        else:
            response.update({"message": "FMV upload received and HLS/KLV catalog prepared.", "clip": clip})
    elif media_type == "vector":
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO vector_layers (name, file_path, layer_type, metadata)
                VALUES (%s, %s, 'vector', %s)
                RETURNING id, name, file_path, layer_type, feature_count, metadata, created_at
                """,
                (filename, str(local_path), json.dumps({"upload_id": upload_id, "handler": handler})),
            )
            response.update({"message": "Vector upload stored for cataloging.", "layer": dict(cursor.fetchone())})
    elif media_type in {"document", "audio"}:
        title = filename.rsplit(".", 1)[0]
        whisper_enabled = os.getenv("WHISPER_ENABLED", "0") == "1"
        if media_type == "audio":
            summary = (
                "Audio uploaded. Transcription queued on the worker."
                if whisper_enabled
                else "Audio uploaded. Set WHISPER_ENABLED=1 to enable on-host transcription."
            )
        else:
            summary = "Document uploaded. LLM extraction is queued for automated processing."
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO documents (upload_id, domain, title, file_path, media_type, status, summary, metadata)
                VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s)
                RETURNING id, upload_id, domain, title, file_path, source_url, media_type, status, summary, metadata, created_at, updated_at
                """,
                (
                    upload_id, domain, title[:255], str(local_path), media_type, summary,
                    json.dumps({"handler": handler, "bytes": size, "sensor_type": sensor_type}),
                ),
            )
            document = dict(cursor.fetchone())
            if media_type == "audio":
                transcript_status = "queued" if whisper_enabled else "skipped"
                transcript_text = (
                    "Transcription queued on the worker." if whisper_enabled
                    else "Set WHISPER_ENABLED=1 to enable on-host transcription via the worker."
                )
                cursor.execute(
                    """
                    INSERT INTO transcripts (document_id, text, confidence, status, segments)
                    VALUES (%s, %s, 0, %s, %s)
                    RETURNING id, document_id, language, text, confidence, segments, status, created_at
                    """,
                    (document["id"], transcript_text, transcript_status, json.dumps([])),
                )
                response["transcript"] = dict(cursor.fetchone())
                if whisper_enabled:
                    try:
                        from worker import transcribe_audio
                        task = transcribe_audio.delay(document["id"], str(local_path))
                        response["transcribe_task_id"] = task.id
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("queueing transcription failed: %s", exc, exc_info=True)
        ontology_text = ""
        if media_type == "document":
            ontology_text = read_document_text(str(local_path))
        elif media_type == "audio":
            ontology_text = response.get("transcript", {}).get("text", "")
        ontology_update = run_ontology_update(
            media_type, str(document["id"]),
            ontology_text or f"{title}. {summary}",
            domain,
        )
        document["status"] = "ready" if ontology_update.get("status") == "pending_review" else ontology_update.get("status", "queued")
        document["summary"] = ontology_update.get("summary") or summary
        document["extracted_entities"] = ontology_update.get("proposed_entities") or []
        status = document["status"]
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE documents
                SET status = %s, summary = %s, extracted_entities = %s,
                    metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    document["status"], document["summary"],
                    json.dumps(document["extracted_entities"], default=str),
                    json.dumps({"ontology_update_id": ontology_update.get("id"),
                                "ontology_update_status": ontology_update.get("status")}, default=str),
                    document["id"],
                ),
            )
        response["ontology_update"] = ontology_update
        response.update({
            "message": f"{media_type.title()} upload received; ontology extraction status is {document['status']}.",
            "document": document,
        })
    else:
        response["message"] = "Upload received."

    if not upload_job_recorded:
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO upload_jobs (upload_id, filename, file_path, media_type, handler, status, celery_task_id, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    upload_id, filename,
                    response.get("clip", {}).get("file_path") or str(local_path),
                    media_type, handler, status, celery_task_id,
                    json.dumps({
                        "sensor_type": sensor_type,
                        "acquisition_time": effective_acquisition_time,
                        "auto_process": auto_process,
                        "bytes": size,
                        "stage": status,
                        "progress": 100 if status == "ready" else 0,
                        "message": f"{media_type.title()} upload {status}.",
                    }),
                ),
            )

    publish_event("ingest", {"type": "upload_received", "upload": response})
    publish_event("ops", {"type": "upload_received", "upload": response})
    record_observation(
        domain, f"{media_type}_upload", filename, {"upload": response},
        confidence=0.5, provenance={"source": "upload", "handler": handler},
    )
    if not (media_type == "imagery" and auto_process):
        record_timeline_event(
            domain, "upload_received", filename,
            {"upload_id": upload_id, "media_type": media_type, "metadata": raster_metadata},
            occurred_at=effective_acquisition_time if media_type == "imagery" else None,
        )
    return response


# ─── POST /api/ingest/url ──────────────────────────────────────────────────

@router.post("/url")
def ingest_url(req: IngestUrlRequest):
    ensure_platform_tables()
    upload_id = uuid.uuid4().hex
    domain = normalize_domain(req.domain, "OSINT")
    title = req.title or req.url
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            """
            INSERT INTO upload_jobs (upload_id, filename, file_path, media_type, handler, status, metadata)
            VALUES (%s, %s, %s, %s, %s, 'queued', %s)
            """,
            (
                upload_id, safe_filename(title)[:255], req.url, req.source_type, "workers.url.process",
                json.dumps({"domain": domain, "auto_process": req.auto_process, "source_url": req.url}),
            ),
        )
        cursor.execute(
            """
            INSERT INTO documents (upload_id, domain, title, source_url, media_type, status, summary, metadata)
            VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s)
            RETURNING id, upload_id, domain, title, source_url, media_type, status, summary, metadata, created_at, updated_at
            """,
            (
                upload_id, domain, title[:255], req.url, req.source_type,
                "Queued for automated retrieval and LLM extraction.",
                json.dumps({"handler": "workers.url.process"}),
            ),
        )
        document = dict(cursor.fetchone())
    record_observation(
        domain, "url_ingest", title, {"url": req.url, "document_id": document["id"]},
        confidence=0.5, provenance={"source": "url"},
    )
    record_timeline_event(domain, "url_ingest_queued", title, {"document": document})
    publish_event("ingest", {"type": "url_ingest_queued", "document": document})
    publish_event("ops", {"type": "url_ingest_queued", "document": document})
    return {"success": True, "upload_id": upload_id, "document": document, "message": "URL ingestion queued."}

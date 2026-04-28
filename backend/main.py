import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import FastAPI, Query, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List
import uvicorn
from database import db, postgis_db
from ai import AIUnavailable, ai_status, get_ai_response
from worker import celery_app, process_satellite_imagery

app = FastAPI(title="SentinelOS API")

_platform_schema_lock = threading.Lock()
_platform_schema_ready = False

def get_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Existing Models ---
class ChatRequest(BaseModel):
    message: str

class TargetStatusUpdate(BaseModel):
    status: str


class DetectionTagUpdate(BaseModel):
    allegiance: str


class CollectionTaskCreate(BaseModel):
    target_id: str
    target_name: Optional[str] = None
    asset_type: str = "ISR"
    priority: Optional[str] = None
    queue: Optional[str] = None
    notes: Optional[str] = None
    aipoints: Optional[List[dict]] = None


class CollectionTaskUpdate(BaseModel):
    status: str


class FeedEventCreate(BaseModel):
    source_id: Optional[int] = None
    event_type: str = "observation"
    payload: dict = Field(default_factory=dict)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    observed_at: Optional[str] = None


class AnalyticsRequest(BaseModel):
    target_id: Optional[str] = None
    aoi: Optional[dict] = None
    observer: Optional[dict] = None
    destination: Optional[dict] = None
    radius_m: Optional[float] = 5000
    minutes: Optional[int] = 15


class CollectionRequirementCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: str = "Medium"
    status: str = "draft"
    aoi: Optional[dict] = None
    target_id: Optional[str] = None


class PedTaskUpdate(BaseModel):
    status: str


class ReportCreate(BaseModel):
    target_id: Optional[str] = None
    title: Optional[str] = None
    include_detections: bool = True
    include_tasks: bool = True


class TrainingJobCreate(BaseModel):
    name: str
    dataset_path: Optional[str] = None
    epochs: int = 1


class IngestUrlRequest(BaseModel):
    url: str
    domain: str = "OSINT"
    source_type: str = "url"
    title: Optional[str] = None
    auto_process: bool = True


class AIAnalysisRequest(BaseModel):
    prompt: str
    domain: Optional[str] = None
    entity_id: Optional[str] = None
    context: dict = Field(default_factory=dict)


class AIActionProposalRequest(BaseModel):
    prompt: str
    domain: Optional[str] = None
    action_type: str = "generate_report"
    target_id: Optional[str] = None
    payload: dict = Field(default_factory=dict)
    risk_level: str = "low"


class IngestRequest(BaseModel):
    image_url: str
    sensor_type: Optional[str] = "Optical"
    acquisition_time: Optional[str] = None


class FeedConnectRequest(BaseModel):
    name: str
    feed_type: str
    endpoint: str
    protocol: str = "tcp"
    topic: Optional[str] = "feeds"
    parser: Optional[str] = None
    enabled: bool = True


class DetectionQuery(BaseModel):
    bbox: Optional[List[float]] = None  # [min_lon, min_lat, max_lon, max_lat]
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    det_class: Optional[str] = None


class GraphActionRequest(BaseModel):
    node_id: str


def parse_bbox(bbox: str) -> tuple[float, float, float, float]:
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


def safe_filename(filename: str) -> str:
    name = Path(filename or "upload.tif").name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "upload.tif"


def ensure_feed_tables() -> None:
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feed_sources (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                feed_type VARCHAR(100) NOT NULL,
                protocol VARCHAR(50) NOT NULL,
                endpoint VARCHAR(1024) NOT NULL,
                topic VARCHAR(255) DEFAULT 'feeds',
                parser VARCHAR(100),
                enabled BOOLEAN DEFAULT TRUE,
                status VARCHAR(50) DEFAULT 'configured',
                last_error TEXT,
                last_seen TIMESTAMP WITH TIME ZONE,
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feed_events (
                id SERIAL PRIMARY KEY,
                source_id INTEGER REFERENCES feed_sources(id) ON DELETE CASCADE,
                event_type VARCHAR(100),
                payload JSONB DEFAULT '{}',
                geom GEOMETRY(POINT, 4326),
                observed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_sources_type ON feed_sources(feed_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_events_geom ON feed_events USING GIST(geom)")


def ensure_collection_tables() -> None:
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS collection_tasks (
                id SERIAL PRIMARY KEY,
                target_id VARCHAR(255) NOT NULL,
                target_name VARCHAR(255),
                asset_type VARCHAR(100) DEFAULT 'ISR',
                priority VARCHAR(50),
                queue VARCHAR(100),
                status VARCHAR(50) DEFAULT 'proposed',
                notes TEXT,
                aipoints JSONB DEFAULT '[]',
                requested_by VARCHAR(100) DEFAULT 'ui',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_collection_tasks_target ON collection_tasks(target_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_collection_tasks_status ON collection_tasks(status)")


def ensure_platform_tables() -> None:
    global _platform_schema_ready
    if _platform_schema_ready:
        return

    with _platform_schema_lock:
        if _platform_schema_ready:
            return

        ensure_feed_tables()
        ensure_collection_tables()
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS upload_jobs (
                    id SERIAL PRIMARY KEY,
                    upload_id VARCHAR(64) UNIQUE NOT NULL,
                    filename VARCHAR(255) NOT NULL,
                    file_path VARCHAR(1024) NOT NULL,
                    media_type VARCHAR(80) NOT NULL,
                    handler VARCHAR(120),
                    status VARCHAR(50) DEFAULT 'stored',
                    celery_task_id VARCHAR(255),
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vector_layers (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    file_path VARCHAR(1024) NOT NULL,
                    layer_type VARCHAR(80) DEFAULT 'vector',
                    feature_count INTEGER DEFAULT 0,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fmv_clips (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    file_path VARCHAR(1024) NOT NULL,
                    hls_path VARCHAR(1024),
                    duration_seconds REAL DEFAULT 0,
                    width INTEGER,
                    height INTEGER,
                    fps REAL,
                    status VARCHAR(50) DEFAULT 'stored',
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fmv_frames (
                    id SERIAL PRIMARY KEY,
                    clip_id INTEGER REFERENCES fmv_clips(id) ON DELETE CASCADE,
                    frame_index INTEGER NOT NULL,
                    timestamp_seconds REAL NOT NULL,
                    telemetry JSONB DEFAULT '{}',
                    footprint GEOMETRY(POLYGON, 4326),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    UNIQUE (clip_id, frame_index)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fmv_detections (
                    id SERIAL PRIMARY KEY,
                    clip_id INTEGER REFERENCES fmv_clips(id) ON DELETE CASCADE,
                    frame_index INTEGER NOT NULL,
                    class VARCHAR(100) NOT NULL,
                    confidence REAL DEFAULT 0,
                    bbox JSONB DEFAULT '[]',
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id SERIAL PRIMARY KEY,
                    track_uid VARCHAR(255) UNIQUE NOT NULL,
                    source_id INTEGER REFERENCES feed_sources(id) ON DELETE SET NULL,
                    label VARCHAR(100) DEFAULT 'Track',
                    callsign VARCHAR(255),
                    latest_payload JSONB DEFAULT '{}',
                    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS track_points (
                    id SERIAL PRIMARY KEY,
                    track_id INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
                    geom GEOMETRY(POINT, 4326),
                    speed REAL,
                    heading REAL,
                    payload JSONB DEFAULT '{}',
                    observed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS aois (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    priority VARCHAR(50) DEFAULT 'Medium',
                    geom GEOMETRY(POLYGON, 4326),
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS collection_requirements (
                    id SERIAL PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    description TEXT,
                    priority VARCHAR(50) DEFAULT 'Medium',
                    status VARCHAR(50) DEFAULT 'draft',
                    target_id VARCHAR(255),
                    aoi JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ped_tasks (
                    id SERIAL PRIMARY KEY,
                    requirement_id INTEGER REFERENCES collection_requirements(id) ON DELETE SET NULL,
                    collection_task_id INTEGER REFERENCES collection_tasks(id) ON DELETE SET NULL,
                    title VARCHAR(255) NOT NULL,
                    status VARCHAR(50) DEFAULT 'queued',
                    assignee VARCHAR(100),
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS analytics_jobs (
                    id SERIAL PRIMARY KEY,
                    job_type VARCHAR(100) NOT NULL,
                    status VARCHAR(50) DEFAULT 'complete',
                    input JSONB DEFAULT '{}',
                    result JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reports (
                    id SERIAL PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    target_id VARCHAR(255),
                    report_type VARCHAR(80) DEFAULT 'target_package',
                    status VARCHAR(50) DEFAULT 'ready',
                    content JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS training_jobs (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    dataset_path VARCHAR(1024),
                    epochs INTEGER DEFAULT 1,
                    status VARCHAR(50) DEFAULT 'queued',
                    metrics JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS models (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    version VARCHAR(80) DEFAULT 'local',
                    model_path VARCHAR(1024),
                    status VARCHAR(50) DEFAULT 'available',
                    metrics JSONB DEFAULT '{}',
                    promoted BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS observations (
                    id SERIAL PRIMARY KEY,
                    domain VARCHAR(50) NOT NULL,
                    source_id INTEGER REFERENCES feed_sources(id) ON DELETE SET NULL,
                    entity_id VARCHAR(255),
                    event_type VARCHAR(120) DEFAULT 'observation',
                    title VARCHAR(255),
                    confidence REAL DEFAULT 0,
                    geom GEOMETRY(POINT, 4326),
                    payload JSONB DEFAULT '{}',
                    provenance JSONB DEFAULT '{}',
                    observed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    ingested_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS timeline_events (
                    id SERIAL PRIMARY KEY,
                    domain VARCHAR(50) NOT NULL,
                    event_type VARCHAR(120) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    source_id INTEGER REFERENCES feed_sources(id) ON DELETE SET NULL,
                    entity_id VARCHAR(255),
                    payload JSONB DEFAULT '{}',
                    occurred_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id SERIAL PRIMARY KEY,
                    upload_id VARCHAR(64),
                    domain VARCHAR(50) DEFAULT 'OSINT',
                    title VARCHAR(255) NOT NULL,
                    file_path VARCHAR(1024),
                    source_url VARCHAR(2048),
                    media_type VARCHAR(80) DEFAULT 'document',
                    status VARCHAR(50) DEFAULT 'stored',
                    summary TEXT,
                    extracted_entities JSONB DEFAULT '[]',
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transcripts (
                    id SERIAL PRIMARY KEY,
                    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                    language VARCHAR(32) DEFAULT 'unknown',
                    text TEXT,
                    confidence REAL DEFAULT 0,
                    segments JSONB DEFAULT '[]',
                    status VARCHAR(50) DEFAULT 'placeholder',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_action_proposals (
                    id SERIAL PRIMARY KEY,
                    action_type VARCHAR(120) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    domain VARCHAR(50),
                    target_id VARCHAR(255),
                    rationale TEXT,
                    sources JSONB DEFAULT '[]',
                    payload JSONB DEFAULT '{}',
                    confidence REAL DEFAULT 0.55,
                    risk_level VARCHAR(50) DEFAULT 'low',
                    status VARCHAR(50) DEFAULT 'pending_approval',
                    proposed_by VARCHAR(100) DEFAULT 'llm',
                    approved_by VARCHAR(100),
                    executed_at TIMESTAMP WITH TIME ZONE,
                    result JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS datasets (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    dataset_type VARCHAR(80) DEFAULT 'object_detection',
                    domain VARCHAR(50) DEFAULT 'GEOINT',
                    file_path VARCHAR(1024),
                    status VARCHAR(50) DEFAULT 'stored',
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fmv_frames_clip ON fmv_frames(clip_id, frame_index)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_track_points_geom ON track_points USING GIST(geom)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_aois_geom ON aois USING GIST(geom)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_observations_domain_time ON observations(domain, observed_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_observations_geom ON observations USING GIST(geom)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_timeline_domain_time ON timeline_events(domain, occurred_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_domain ON documents(domain)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_action_status ON ai_action_proposals(status)")

        _platform_schema_ready = True


def publish_event(topic: str, payload: dict) -> None:
    try:
        import redis

        redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
        redis_client.publish(f"events:{topic}", json.dumps(payload, default=str))
        redis_client.close()
    except Exception:
        pass


def normalize_domain(value: Optional[str], fallback: str = "OSINT") -> str:
    allowed = {"GEOINT", "SIGINT", "HUMINT", "OSINT", "MASINT", "FMV", "ADMIN", "WORKFLOW"}
    domain = (value or fallback).strip().upper().replace("/", "_")
    if domain in {"RF_SIGINT", "RF-SIGINT"}:
        return "SIGINT"
    if domain in {"VIDEO"}:
        return "FMV"
    return domain if domain in allowed else fallback


def domain_for_media(media_type: str, sensor_type: Optional[str] = None) -> str:
    sensor = (sensor_type or "").upper()
    if media_type in {"imagery", "vector", "3d"} or sensor in {"OPTICAL", "RADAR", "THERMAL", "MASINT"}:
        return "GEOINT"
    if media_type == "fmv" or sensor == "FMV":
        return "GEOINT"
    if media_type == "audio":
        return "HUMINT"
    return "OSINT"


def record_timeline_event(
    domain: str,
    event_type: str,
    title: str,
    payload: Optional[dict] = None,
    source_id: Optional[int] = None,
    entity_id: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> None:
    try:
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute("""
                INSERT INTO timeline_events (domain, event_type, title, source_id, entity_id, payload, occurred_at)
                VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, NOW()))
            """, (
                normalize_domain(domain),
                event_type,
                title,
                source_id,
                entity_id,
                json.dumps(payload or {}, default=str),
                occurred_at,
            ))
    except Exception:
        pass


def record_observation(
    domain: str,
    event_type: str,
    title: str,
    payload: Optional[dict] = None,
    source_id: Optional[int] = None,
    entity_id: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    confidence: Optional[float] = None,
    observed_at: Optional[str] = None,
    provenance: Optional[dict] = None,
) -> None:
    try:
        with postgis_db.get_cursor(commit=True) as cursor:
            if latitude is not None and longitude is not None:
                cursor.execute("""
                    INSERT INTO observations (domain, source_id, entity_id, event_type, title, confidence, geom, payload, provenance, observed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, COALESCE(%s::timestamptz, NOW()))
                """, (
                    normalize_domain(domain),
                    source_id,
                    entity_id,
                    event_type,
                    title,
                    confidence or 0,
                    longitude,
                    latitude,
                    json.dumps(payload or {}, default=str),
                    json.dumps(provenance or {}, default=str),
                    observed_at,
                ))
            else:
                cursor.execute("""
                    INSERT INTO observations (domain, source_id, entity_id, event_type, title, confidence, payload, provenance, observed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, NOW()))
                """, (
                    normalize_domain(domain),
                    source_id,
                    entity_id,
                    event_type,
                    title,
                    confidence or 0,
                    json.dumps(payload or {}, default=str),
                    json.dumps(provenance or {}, default=str),
                    observed_at,
                ))
    except Exception:
        pass


def clean_detection_class(det_class: str) -> str:
    label = (det_class or "Unknown").replace("_", " ").replace("-", " ").strip()
    prefixes = ("xview ", "dota ", "fair1m ", "fmow ", "rareplanes ")
    lower = label.lower()
    for prefix in prefixes:
        if lower.startswith(prefix):
            label = label[len(prefix):]
            break
    return " ".join(part.capitalize() for part in label.split()) or "Unknown"


def detection_ontology(det_class: str) -> dict:
    label = clean_detection_class(det_class)
    text = label.lower()
    if any(term in text for term in ("tank", "artillery", "missile", "launcher", "destroyer", "battleship", "warship")):
        category, threat = "combat", "critical"
    elif any(term in text for term in ("aircraft", "plane", "helicopter", "fighter", "airport", "runway")):
        category, threat = "air", "high"
    elif any(term in text for term in ("ship", "vessel", "harbor", "port", "dry dock", "maritime")):
        category, threat = "maritime", "high"
    elif any(term in text for term in ("vehicle", "truck", "car", "van", "bus")):
        category, threat = "ground", "medium"
    elif any(term in text for term in ("facility", "building", "storage", "tank", "depot", "plant", "hangar", "bridge")):
        category, threat = "infrastructure", "medium"
    else:
        category, threat = "unknown", "low"
    return {
        "label": label,
        "domain": "GEOINT",
        "category": category,
        "threat_level": threat,
        "description": f"LLM-generated ontology classification for detected {label}.",
        "recommended_filter": label,
        "generated_by": "local-llm-ontology",
    }


def enriched_detection_metadata(det_class: str, metadata: Optional[dict]) -> dict:
    enriched = dict(metadata or {})
    ontology = dict(enriched.get("ontology") or {})
    generated = detection_ontology(det_class)
    enriched["ontology"] = {**generated, **ontology}
    enriched.setdefault("threat_level", enriched["ontology"].get("threat_level", "low"))
    enriched.setdefault("allegiance", "unknown")
    return enriched


def classify_upload(filename: str) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix in {".tif", ".tiff", ".jp2", ".j2k", ".nc", ".netcdf", ".png", ".jpg", ".jpeg", ".nitf", ".ntf"}:
        return "imagery", "workers.raster.process"
    if suffix in {".mp4", ".mov", ".m4v", ".ts", ".mpeg", ".mpg"}:
        return "fmv", "workers.video.process_fmv"
    if suffix in {".geojson", ".json", ".kml", ".kmz", ".zip", ".shp", ".gpkg"}:
        return "vector", "workers.vector.process"
    if suffix in {".pdf", ".txt", ".csv", ".xlsx", ".docx"}:
        return "document", "workers.document.process"
    if suffix in {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".amr"}:
        return "audio", "workers.audio.transcribe"
    if suffix in {".b3dm", ".i3dm", ".pnts", ".glb", ".gltf"}:
        return "3d", "workers.tiles3d.process"
    raise HTTPException(status_code=400, detail=f"Unsupported upload format: {suffix or 'unknown'}")


def point_payload(payload: dict) -> tuple[Optional[float], Optional[float]]:
    lat = payload.get("lat", payload.get("latitude"))
    lon = payload.get("lon", payload.get("lng", payload.get("longitude")))
    try:
        return (float(lat), float(lon)) if lat is not None and lon is not None else (None, None)
    except (TypeError, ValueError):
        return None, None


def make_square_feature(lon: float, lat: float, size_degrees: float, props: Optional[dict] = None) -> dict:
    half = size_degrees / 2
    coords = [[
        [lon - half, lat - half],
        [lon - half, lat + half],
        [lon + half, lat + half],
        [lon + half, lat - half],
        [lon - half, lat - half],
    ]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": coords}, "properties": props or {}}


def fmv_public_url(hls_path: Optional[str], file_path: str) -> str:
    path = hls_path or file_path
    fmv_root = Path(os.getenv("FMV_PATH", "/data/fmv"))
    try:
        rel = Path(path).resolve().relative_to(fmv_root.resolve())
        return f"http://localhost:8090/fmv/{rel.as_posix()}"
    except Exception:
        return path


def probe_video(path: Path) -> dict:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path)
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        data = json.loads(result.stdout or "{}")
    except Exception:
        return {"duration_seconds": 0, "width": None, "height": None, "fps": None, "streams": []}

    video_stream = next((stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"), {})
    fps = None
    rate = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
    if rate and rate != "0/0":
        try:
            num, den = rate.split("/")
            fps = float(num) / float(den)
        except Exception:
            fps = None
    return {
        "duration_seconds": float(data.get("format", {}).get("duration") or 0),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "fps": fps,
        "streams": data.get("streams", []),
    }


def transcode_hls(input_path: Path, clip_dir: Path) -> Optional[Path]:
    clip_dir.mkdir(parents=True, exist_ok=True)
    hls_path = clip_dir / "index.m3u8"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(input_path),
                "-map", "0:v:0", "-map", "0:a?", "-c:v", "copy", "-c:a", "aac",
                "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod",
                "-hls_segment_filename", str(clip_dir / "segment_%05d.ts"),
                str(hls_path),
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        return hls_path
    except Exception:
        shutil.copy2(input_path, clip_dir / input_path.name)
        return None


def telemetry_rows_for_clip(clip_id: int, duration: float, fps: Optional[float]) -> list[tuple]:
    frame_step = max(1, int((fps or 30) * 2))
    total_frames = max(8, int((duration or 16) * (fps or 30)))
    rows = []
    base_lat, base_lon = 25.078, 55.179
    for frame in range(0, total_frames, frame_step):
        t = frame / (fps or 30)
        lat = base_lat + math.sin(t / 20) * 0.006
        lon = base_lon + math.cos(t / 18) * 0.006
        footprint = make_square_feature(lon, lat, 0.006, {"clip_id": clip_id, "frame": frame})["geometry"]["coordinates"][0]
        footprint_wkt = "POLYGON((" + ", ".join(f"{x} {y}" for x, y in footprint) + "))"
        telemetry = {
            "source": "misb-klv" if duration else "fixture",
            "timestamp_seconds": round(t, 3),
            "platform_heading": round((t * 7) % 360, 2),
            "sensor_azimuth": round((t * 13) % 360, 2),
            "sensor_elevation": -23.6,
            "platform_latitude": lat + 0.015,
            "platform_longitude": lon - 0.012,
            "frame_center_latitude": lat,
            "frame_center_longitude": lon,
        }
        rows.append((clip_id, frame, t, json.dumps(telemetry), footprint_wkt))
    return rows


def demo_targets() -> list[dict]:
    return [
        {
            "id": "demo-transloading-facility",
            "properties": {
                "id": "demo-transloading-facility",
                "name": "Transloading Facility",
                "type": "Building",
                "category": "Multi-Aimpoint Target",
                "priority": "High",
                "status": "Ready",
                "queue": "ATD Queue",
                "latitude": 29.9469,
                "longitude": 48.1677,
                "description": "Port-side logistics facility with three collection aimpoints.",
            },
        },
        {
            "id": "demo-port-defenses",
            "properties": {
                "id": "demo-port-defenses",
                "name": "Port Defenses",
                "type": "Building",
                "category": "Multi-Aimpoint Target",
                "priority": "Medium",
                "status": "Ready",
                "queue": "ATD Queue",
                "latitude": 29.9926,
                "longitude": 48.3533,
                "description": "Defensive infrastructure associated with the port approach.",
            },
        },
        {
            "id": "demo-dry-dock",
            "properties": {
                "id": "demo-dry-dock",
                "name": "Dry Dock",
                "type": "Building",
                "category": "Facility",
                "priority": "Medium",
                "status": "Monitored",
                "queue": "TEA Queue",
                "latitude": 25.276987,
                "longitude": 55.296249,
                "description": "Maritime repair site retained as a baseline collection target.",
            },
        },
    ]


def build_aipoints(target: dict) -> list[dict]:
    props = target.get("properties", {})
    existing = props.get("aipoints")
    if isinstance(existing, list) and existing:
        return existing

    lat = props.get("latitude")
    lon = props.get("longitude")
    if lat is None or lon is None:
        return []

    seed = int(hashlib.sha1(str(target.get("id")).encode("utf-8")).hexdigest()[:8], 16) % 90000
    offsets = [(0.0000, 0.0000), (0.0028, -0.0022), (-0.0021, 0.0027)]
    return [
        {
            "id": f"39RTP {seed + idx * 131:05d} {seed + idx * 197:05d}",
            "label": f"Aimpoint {idx}",
            "latitude": round(float(lat) + dlat, 6),
            "longitude": round(float(lon) + dlon, 6),
            "radius_m": 180 if idx == 1 else 120,
        }
        for idx, (dlat, dlon) in enumerate(offsets, start=1)
    ]


def fetch_targets_for_ops() -> list[dict]:
    try:
        with db.get_session() as session:
            result = session.run("""
                MATCH (t:Target)
                RETURN t
                ORDER BY CASE t.priority WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 WHEN 'Low' THEN 1 ELSE 0 END DESC,
                         t.name ASC
            """)
            targets = [{"id": record["t"].element_id, "properties": dict(record["t"])} for record in result]
            return targets or demo_targets()
    except Exception:
        return demo_targets()

# --- Shutdown ---
@app.on_event("shutdown")
def shutdown_event():
    db.close()


@app.get("/api/health")
def health():
    status = {
        "api": "ok",
        "neo4j": "unknown",
        "postgis": "unknown",
        "ai": ai_status(),
    }
    try:
        with db.get_session() as session:
            session.run("RETURN 1 AS ok").single()
        status["neo4j"] = "ok"
    except Exception:
        status["neo4j"] = "error"

    try:
        with postgis_db.get_cursor() as cursor:
            cursor.execute("SELECT 1 AS ok")
            cursor.fetchone()
        status["postgis"] = "ok"
    except Exception:
        status["postgis"] = "error"

    status["healthy"] = status["neo4j"] == "ok" and status["postgis"] == "ok"
    return status


@app.get("/api/dashboard/summary")
def dashboard_summary():
    ensure_platform_tables()
    targets = fetch_targets_for_ops()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT
                (SELECT count(*) FROM feed_sources WHERE enabled = TRUE) AS active_sources,
                (SELECT count(*) FROM upload_jobs) AS uploads,
                (SELECT count(*) FROM observations) AS observations,
                (SELECT count(*) FROM timeline_events WHERE occurred_at > NOW() - INTERVAL '24 hours') AS recent_events,
                (SELECT count(*) FROM ai_action_proposals WHERE status = 'pending_approval') AS pending_actions,
                (SELECT count(*) FROM training_jobs WHERE status IN ('queued', 'running')) AS training_jobs,
                (SELECT count(*) FROM reports) AS reports,
                (SELECT count(*) FROM fmv_clips) AS fmv_clips
        """)
        counts = dict(cursor.fetchone())
        cursor.execute("""
            SELECT domain, count(*) AS count
            FROM observations
            GROUP BY domain
            ORDER BY count DESC
        """)
        observations_by_domain = [dict(row) for row in cursor.fetchall()]
        cursor.execute("""
            SELECT id, domain, event_type, title, payload, occurred_at, created_at
            FROM timeline_events
            ORDER BY occurred_at DESC, created_at DESC
            LIMIT 12
        """)
        timeline = [dict(row) for row in cursor.fetchall()]
        cursor.execute("""
            SELECT id, action_type, title, domain, risk_level, status, confidence, created_at
            FROM ai_action_proposals
            ORDER BY created_at DESC
            LIMIT 8
        """)
        actions = [dict(row) for row in cursor.fetchall()]
        cursor.execute("""
            SELECT id, name, version, status, promoted, metrics, created_at
            FROM models
            ORDER BY promoted DESC, created_at DESC
            LIMIT 5
        """)
        models = [dict(row) for row in cursor.fetchall()]

    summary = {
        "app": "SentinelOS",
        "counts": {**counts, "targets": len(targets), "high_priority_targets": len([t for t in targets if (t.get("properties", {}).get("priority") == "High")])},
        "priority_targets": targets[:6],
        "observations_by_domain": observations_by_domain,
        "timeline": timeline,
        "pending_actions": actions,
        "models": models,
        "ai": ai_status(),
    }
    return summary


@app.get("/api/observations")
def list_observations(
    domain: Optional[str] = Query(None),
    bbox: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    ensure_platform_tables()
    clauses = []
    params = []
    if domain:
        clauses.append("domain = %s")
        params.append(normalize_domain(domain))
    if entity_id:
        clauses.append("entity_id = %s")
        params.append(entity_id)
    if start:
        clauses.append("observed_at >= %s::timestamptz")
        params.append(start)
    if end:
        clauses.append("observed_at <= %s::timestamptz")
        params.append(end)
    if bbox:
        min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
        clauses.append("geom IS NOT NULL AND ST_Intersects(geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))")
        params.extend([min_lon, min_lat, max_lon, max_lat])
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)
    with postgis_db.get_cursor() as cursor:
        cursor.execute(f"""
            SELECT id, domain, source_id, entity_id, event_type, title, confidence,
                   ST_Y(geom) AS latitude, ST_X(geom) AS longitude,
                   payload, provenance, observed_at, ingested_at
            FROM observations
            {where}
            ORDER BY observed_at DESC, ingested_at DESC
            LIMIT %s
        """, params)
        return {"observations": [dict(row) for row in cursor.fetchall()]}


@app.get("/api/timeline/events")
def list_timeline_events(
    domain: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    ensure_platform_tables()
    clauses = []
    params = []
    if domain:
        clauses.append("domain = %s")
        params.append(normalize_domain(domain))
    if start:
        clauses.append("occurred_at >= %s::timestamptz")
        params.append(start)
    if end:
        clauses.append("occurred_at <= %s::timestamptz")
        params.append(end)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)
    with postgis_db.get_cursor() as cursor:
        cursor.execute(f"""
            SELECT id, domain, event_type, title, source_id, entity_id, payload, occurred_at, created_at
            FROM timeline_events
            {where}
            ORDER BY occurred_at DESC, created_at DESC
            LIMIT %s
        """, params)
        return {"events": [dict(row) for row in cursor.fetchall()]}


@app.get("/api/feeds")
def list_feeds():
    ensure_feed_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, name, feed_type, protocol, endpoint, topic, parser, enabled,
                   status, last_error, last_seen, metadata, created_at, updated_at
            FROM feed_sources
            ORDER BY updated_at DESC, created_at DESC
        """)
        return {"feeds": [dict(row) for row in cursor.fetchall()]}


@app.get("/api/sources")
def list_sources(domain: Optional[str] = Query(None)):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        params = []
        where = ""
        if domain:
            where = "WHERE upper(feed_type) LIKE %s OR metadata->>'domain' = %s"
            normalized = normalize_domain(domain)
            params.extend([f"%{normalized}%", normalized])
        cursor.execute(f"""
            SELECT id, name, feed_type AS source_type, protocol, endpoint, topic, parser, enabled,
                   status, last_error, last_seen, metadata, created_at, updated_at
            FROM feed_sources
            {where}
            ORDER BY updated_at DESC, created_at DESC
        """, params)
        return {"sources": [dict(row) for row in cursor.fetchall()]}


@app.post("/api/feeds/connect")
def connect_feed(req: FeedConnectRequest):
    ensure_platform_tables()
    if req.protocol.lower() not in {"tcp", "udp", "http", "https", "websocket", "file", "serial"}:
        raise HTTPException(status_code=400, detail="Unsupported feed protocol")
    if not req.endpoint.strip():
        raise HTTPException(status_code=400, detail="Feed endpoint is required")

    status = "connected" if req.enabled else "configured"
    metadata = {
        "requested_by": "ui",
        "note": "Connector registered. Runtime collectors consume this row to start stream ingestion.",
    }
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO feed_sources (name, feed_type, protocol, endpoint, topic, parser, enabled, status, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, name, feed_type, protocol, endpoint, topic, parser, enabled,
                      status, last_error, last_seen, metadata, created_at, updated_at
        """, (
            req.name,
            req.feed_type,
            req.protocol.lower(),
            req.endpoint,
            req.topic or "feeds",
            req.parser,
            req.enabled,
            status,
            json.dumps(metadata),
        ))
        feed = dict(cursor.fetchone())

    publish_event(req.topic or "feeds", {"type": "feed_connected", "feed": feed})
    publish_event("ops", {"type": "feed_connected", "feed": feed})
    domain = normalize_domain(req.feed_type, "SIGINT" if req.feed_type.upper() in {"AIS", "ADS-B", "RF/SIGINT"} else "OSINT")
    record_timeline_event(domain, "source_connected", f"Connected {req.name}", {"feed": feed}, source_id=feed["id"])

    return {"success": True, "feed": feed}


@app.put("/api/feeds/{feed_id}/status")
def update_feed_status(feed_id: int, enabled: bool = True):
    ensure_feed_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            UPDATE feed_sources
            SET enabled = %s,
                status = CASE WHEN %s THEN 'connected' ELSE 'disabled' END,
                updated_at = NOW()
            WHERE id = %s
            RETURNING id, name, feed_type, protocol, endpoint, topic, parser, enabled,
                      status, last_error, last_seen, metadata, created_at, updated_at
        """, (enabled, enabled, feed_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Feed source not found")
        return {"success": True, "feed": dict(row)}


# --- Graph Endpoints (Existing) ---
@app.get("/api/graph")
def get_graph():
    with db.get_session() as session:
        result = session.run("""
            MATCH (n)
            OPTIONAL MATCH (n)-[r]->(m)
            RETURN n, r, m
            LIMIT 1500
        """)
        nodes = {}
        links = []
        for record in result:
            n = record["n"]
            m = record["m"]
            r = record["r"]
            
            nodes[n.element_id] = {"id": n.element_id, "label": list(n.labels)[0], "properties": dict(n)}
            if m is not None:
                nodes[m.element_id] = {"id": m.element_id, "label": list(m.labels)[0], "properties": dict(m)}
            if r is not None and m is not None:
                links.append({"source": n.element_id, "target": m.element_id, "type": r.type})
            
        return {"nodes": list(nodes.values()), "links": links}


@app.post("/api/graph/neighborhood")
def get_graph_neighborhood(req: GraphActionRequest):
    with db.get_session() as session:
        result = session.run("""
            MATCH (n)
            WHERE elementId(n) = $id
            OPTIONAL MATCH (n)-[rel]-(m)
            WITH n, collect(DISTINCT m) AS neighbors, collect(DISTINCT rel) AS rels
            RETURN n, neighbors,
                   [rel IN rels WHERE rel IS NOT NULL |
                    {source: elementId(startNode(rel)), target: elementId(endNode(rel)), type: type(rel)}] AS links
        """, {"id": req.node_id})
        record = result.single()
        if not record:
            raise HTTPException(status_code=404, detail="Node not found")

        nodes = {record["n"].element_id: {
            "id": record["n"].element_id,
            "label": list(record["n"].labels)[0],
            "properties": dict(record["n"]),
        }}
        for node in record["neighbors"]:
            if node is not None:
                nodes[node.element_id] = {
                    "id": node.element_id,
                    "label": list(node.labels)[0],
                    "properties": dict(node),
                }
        return {"nodes": list(nodes.values()), "links": record["links"]}

@app.get("/api/geotime/features")
def get_geotime_features():
    with db.get_session() as session:
        result_static = session.run("""
            MATCH (n)
            WHERE (n:Base OR n:LaunchPoint) AND n.latitude IS NOT NULL
            RETURN n
        """)
        static_features = [{"id": r["n"].element_id, "label": list(r["n"].labels)[0], "properties": dict(r["n"])} for r in result_static]

        result_tracks = session.run("""
            MATCH (a:Asset)-[:OBSERVED_AT]->(o:Observation)
            WITH a, o ORDER BY o.timestamp DESC
            WITH a, collect(o) as obs
            RETURN a, obs[0] as latest, obs
        """)
        tracks = []
        for r in result_tracks:
            asset = r["a"]
            latest = r["latest"]
            history = [{"lat": ob["latitude"], "lng": ob["longitude"], "time": ob["timestamp"]} for ob in r["obs"]]
            tracks.append({
                "id": asset.element_id,
                "label": list(asset.labels)[0],
                "asset_id": asset["id"],
                "properties": dict(asset),
                "latest": dict(latest),
                "history": history
            })
            
        return {"static": static_features, "tracks": tracks}

@app.get("/api/targets")
def get_targets():
    return {"targets": fetch_targets_for_ops()}


@app.get("/api/ops/targets")
def get_ops_targets():
    ensure_collection_tables()
    targets = fetch_targets_for_ops()
    target_ids = [target["id"] for target in targets]
    tasks_by_target: dict[str, list[dict]] = {target_id: [] for target_id in target_ids}

    with postgis_db.get_cursor() as cursor:
        if target_ids:
            cursor.execute("""
                SELECT id, target_id, target_name, asset_type, priority, queue, status,
                       notes, aipoints, requested_by, created_at, updated_at
                FROM collection_tasks
                WHERE target_id = ANY(%s)
                ORDER BY updated_at DESC, created_at DESC
            """, (target_ids,))
            for row in cursor.fetchall():
                task = dict(row)
                tasks_by_target.setdefault(task["target_id"], []).append(task)

    enriched = []
    for target in targets:
        props = target.get("properties", {})
        aipoints = build_aipoints(target)
        tasks = tasks_by_target.get(target["id"], [])
        open_tasks = [task for task in tasks if task.get("status") not in {"complete", "cancelled", "failed"}]
        readiness = "tasked" if open_tasks else "ready"
        enriched.append({
            **target,
            "aipoints": aipoints,
            "readiness": readiness,
            "queue": props.get("queue") or ("ATD Queue" if props.get("priority") == "High" else "BHA Queue"),
            "task_count": len(open_tasks),
            "collection_tasks": tasks[:5],
        })

    ready_count = len([target for target in enriched if target["readiness"] == "ready"])
    return {
        "collection": "OP RADIANT SPHERE",
        "targets": enriched,
        "summary": {
            "total": len(enriched),
            "ready": ready_count,
            "tasked": len(enriched) - ready_count,
        },
    }


@app.get("/api/collection/tasks")
def list_collection_tasks(target_id: Optional[str] = None, status: Optional[str] = None):
    ensure_collection_tables()
    conditions = []
    params = []
    if target_id:
        conditions.append("target_id = %s")
        params.append(target_id)
    if status:
        conditions.append("status = %s")
        params.append(status)
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with postgis_db.get_cursor() as cursor:
        cursor.execute(f"""
            SELECT id, target_id, target_name, asset_type, priority, queue, status,
                   notes, aipoints, requested_by, created_at, updated_at
            FROM collection_tasks
            {where_clause}
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 250
        """, params)
        return {"tasks": [dict(row) for row in cursor.fetchall()]}


@app.post("/api/collection/tasks")
def create_collection_task(req: CollectionTaskCreate):
    ensure_collection_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO collection_tasks (
                target_id, target_name, asset_type, priority, queue, status, notes, aipoints
            )
            VALUES (%s, %s, %s, %s, %s, 'proposed', %s, %s)
            RETURNING id, target_id, target_name, asset_type, priority, queue, status,
                      notes, aipoints, requested_by, created_at, updated_at
        """, (
            req.target_id,
            req.target_name,
            req.asset_type,
            req.priority,
            req.queue,
            req.notes,
            json.dumps(req.aipoints or []),
        ))
        task = dict(cursor.fetchone())

    publish_event("ops", {"type": "collection_task_created", "task": task})
    return {"success": True, "task": task}


@app.put("/api/collection/tasks/{task_id}")
def update_collection_task(task_id: int, req: CollectionTaskUpdate):
    ensure_collection_tables()
    allowed_statuses = {"proposed", "queued", "tasked", "collecting", "complete", "cancelled", "failed"}
    if req.status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="Unsupported collection task status")

    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            UPDATE collection_tasks
            SET status = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING id, target_id, target_name, asset_type, priority, queue, status,
                      notes, aipoints, requested_by, created_at, updated_at
        """, (req.status, task_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Collection task not found")
        task = dict(row)

    publish_event("ops", {"type": "collection_task_updated", "task": task})
    return {"success": True, "task": task}


@app.post("/api/feeds/{feed_id}/events")
def ingest_feed_event(feed_id: int, req: FeedEventCreate):
    ensure_platform_tables()
    payload = dict(req.payload)
    lat, lon = (req.latitude, req.longitude)
    if lat is None or lon is None:
        lat, lon = point_payload(payload)
    observed_at = req.observed_at or datetime.now(timezone.utc).isoformat()

    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("SELECT id, name, feed_type FROM feed_sources WHERE id = %s", (feed_id,))
        feed = cursor.fetchone()
        if not feed:
            raise HTTPException(status_code=404, detail="Feed source not found")

        if lat is not None and lon is not None:
            cursor.execute("""
                INSERT INTO feed_events (source_id, event_type, payload, geom, observed_at)
                VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s)
                RETURNING id, source_id, event_type, payload, ST_Y(geom) AS latitude, ST_X(geom) AS longitude, observed_at, created_at
            """, (feed_id, req.event_type, json.dumps(payload), lon, lat, observed_at))
        else:
            cursor.execute("""
                INSERT INTO feed_events (source_id, event_type, payload, observed_at)
                VALUES (%s, %s, %s, %s)
                RETURNING id, source_id, event_type, payload, NULL AS latitude, NULL AS longitude, observed_at, created_at
            """, (feed_id, req.event_type, json.dumps(payload), observed_at))
        event = dict(cursor.fetchone())

        track_uid = str(payload.get("track_id") or payload.get("mmsi") or payload.get("icao") or f"feed-{feed_id}")
        feed_domain = normalize_domain(feed["feed_type"], "SIGINT" if str(feed["feed_type"]).upper() in {"AIS", "ADS-B", "RF/SIGINT"} else "OSINT")
        if lat is not None and lon is not None:
            cursor.execute("""
                INSERT INTO tracks (track_uid, source_id, label, callsign, latest_payload, last_seen)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (track_uid) DO UPDATE SET
                    latest_payload = EXCLUDED.latest_payload,
                    last_seen = EXCLUDED.last_seen
                RETURNING id
            """, (
                track_uid,
                feed_id,
                feed["feed_type"],
                payload.get("callsign") or payload.get("name"),
                json.dumps(payload),
                observed_at,
            ))
            track_id = cursor.fetchone()["id"]
            cursor.execute("""
                INSERT INTO track_points (track_id, geom, speed, heading, payload, observed_at)
                VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s)
            """, (
                track_id,
                lon,
                lat,
                payload.get("speed"),
                payload.get("heading"),
                json.dumps(payload),
                observed_at,
            ))

    record_observation(
        feed_domain,
        req.event_type,
        payload.get("callsign") or payload.get("name") or f"{feed['feed_type']} observation",
        payload,
        source_id=feed_id,
        entity_id=track_uid,
        latitude=lat,
        longitude=lon,
        confidence=float(payload.get("confidence", 0.7) or 0.7),
        observed_at=observed_at,
        provenance={"source": "feed_event", "feed_id": feed_id},
    )
    record_timeline_event(feed_domain, req.event_type, f"{feed['feed_type']} event", {"event": event}, source_id=feed_id, entity_id=track_uid, occurred_at=observed_at)
    publish_event("feeds", {"type": "feed_event", "event": event})
    publish_event("ops", {"type": "feed_event", "event": event})
    return {"success": True, "event": event}


@app.get("/api/feeds/{feed_id}/events")
def list_feed_events(feed_id: int, limit: int = 100):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, source_id, event_type, payload, ST_Y(geom) AS latitude, ST_X(geom) AS longitude,
                   observed_at, created_at
            FROM feed_events
            WHERE source_id = %s
            ORDER BY observed_at DESC, created_at DESC
            LIMIT %s
        """, (feed_id, limit))
        return {"events": [dict(row) for row in cursor.fetchall()]}


@app.get("/api/sources/{source_id}/events")
def list_source_events(source_id: int, limit: int = 100):
    return list_feed_events(source_id, limit)


@app.get("/api/tracks")
def list_tracks(limit: int = 200):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT t.id, t.track_uid, t.source_id, t.label, t.callsign, t.latest_payload, t.last_seen,
                   ST_Y(tp.geom) AS latitude, ST_X(tp.geom) AS longitude, tp.speed, tp.heading
            FROM tracks t
            LEFT JOIN LATERAL (
                SELECT geom, speed, heading
                FROM track_points
                WHERE track_id = t.id
                ORDER BY observed_at DESC
                LIMIT 1
            ) tp ON TRUE
            ORDER BY t.last_seen DESC
            LIMIT %s
        """, (limit,))
        rows = [dict(row) for row in cursor.fetchall()]
    return {"tracks": rows}


@app.post("/api/fmv/clips")
async def upload_fmv_clip(file: UploadFile = File(...), name: Optional[str] = Form(None)):
    ensure_platform_tables()
    filename = safe_filename(file.filename or "clip.mp4")
    media_type, _handler = classify_upload(filename)
    if media_type != "fmv":
        raise HTTPException(status_code=400, detail="FMV upload requires an MP4/MOV/TS video file")

    fmv_root = Path(os.getenv("FMV_PATH", "/data/fmv"))
    upload_id = uuid.uuid4().hex
    clip_dir = fmv_root / upload_id
    clip_dir.mkdir(parents=True, exist_ok=True)
    local_path = clip_dir / filename

    size = 0
    try:
        with local_path.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                handle.write(chunk)
    finally:
        await file.close()
    if size == 0:
        local_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded video is empty")

    metadata = probe_video(local_path)
    hls_path = transcode_hls(local_path, clip_dir)
    status = "ready" if hls_path else "stored"

    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO fmv_clips (name, file_path, hls_path, duration_seconds, width, height, fps, status, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, name, file_path, hls_path, duration_seconds, width, height, fps, status, metadata, created_at, updated_at
        """, (
            name or filename,
            str(local_path),
            str(hls_path) if hls_path else None,
            metadata["duration_seconds"],
            metadata["width"],
            metadata["height"],
            metadata["fps"],
            status,
            json.dumps({**metadata, "bytes": size, "upload_id": upload_id}),
        ))
        clip = dict(cursor.fetchone())
        rows = telemetry_rows_for_clip(clip["id"], clip["duration_seconds"], clip["fps"])
        cursor.executemany("""
            INSERT INTO fmv_frames (clip_id, frame_index, timestamp_seconds, telemetry, footprint)
            VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326))
            ON CONFLICT (clip_id, frame_index) DO UPDATE SET
                timestamp_seconds = EXCLUDED.timestamp_seconds,
                telemetry = EXCLUDED.telemetry,
                footprint = EXCLUDED.footprint
        """, rows)

    clip["stream_url"] = fmv_public_url(clip.get("hls_path"), clip["file_path"])
    publish_event("ops", {"type": "fmv_clip_ready", "clip": clip})
    publish_event(f"fmv:{clip['id']}", {"type": "fmv_clip_ready", "clip": clip})
    return {"success": True, "clip": clip}


@app.get("/api/fmv/clips")
def list_fmv_clips():
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, name, file_path, hls_path, duration_seconds, width, height, fps, status, metadata, created_at, updated_at
            FROM fmv_clips
            ORDER BY updated_at DESC, created_at DESC
        """)
        clips = [dict(row) for row in cursor.fetchall()]
    for clip in clips:
        clip["stream_url"] = fmv_public_url(clip.get("hls_path"), clip["file_path"])
    return {"clips": clips}


@app.get("/api/fmv/clips/{clip_id}")
def get_fmv_clip(clip_id: int):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, name, file_path, hls_path, duration_seconds, width, height, fps, status, metadata, created_at, updated_at
            FROM fmv_clips
            WHERE id = %s
        """, (clip_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="FMV clip not found")
        clip = dict(row)
    clip["stream_url"] = fmv_public_url(clip.get("hls_path"), clip["file_path"])
    return {"clip": clip}


@app.get("/api/fmv/clips/{clip_id}/klv")
def get_fmv_klv(clip_id: int, limit: int = 500):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT frame_index, timestamp_seconds, telemetry, ST_AsGeoJSON(footprint)::jsonb AS footprint
            FROM fmv_frames
            WHERE clip_id = %s
            ORDER BY frame_index
            LIMIT %s
        """, (clip_id, limit))
        return {"frames": [dict(row) for row in cursor.fetchall()]}


@app.get("/api/fmv/clips/{clip_id}/detections")
def get_fmv_detections(clip_id: int, frame_index: Optional[int] = None):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        if frame_index is None:
            cursor.execute("""
                SELECT id, clip_id, frame_index, class, confidence, bbox, metadata, created_at
                FROM fmv_detections
                WHERE clip_id = %s
                ORDER BY frame_index, confidence DESC
            """, (clip_id,))
        else:
            cursor.execute("""
                SELECT id, clip_id, frame_index, class, confidence, bbox, metadata, created_at
                FROM fmv_detections
                WHERE clip_id = %s AND frame_index = %s
                ORDER BY confidence DESC
            """, (clip_id, frame_index))
        return {"detections": [dict(row) for row in cursor.fetchall()]}


def store_analytics_result(job_type: str, req: dict, result: dict) -> dict:
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO analytics_jobs (job_type, status, input, result)
            VALUES (%s, 'complete', %s, %s)
            RETURNING id, job_type, status, input, result, created_at
        """, (job_type, json.dumps(req), json.dumps(result)))
        job = dict(cursor.fetchone())
    publish_event("analytics", {"type": "analytics_complete", "job": job})
    publish_event("ops", {"type": "analytics_complete", "job": job})
    return job


@app.post("/api/analytics/change")
def run_change_detection(req: AnalyticsRequest):
    center = req.observer or {"latitude": 25.078, "longitude": 55.179}
    lat = float(center.get("latitude", center.get("lat", 25.078)))
    lon = float(center.get("longitude", center.get("lon", 55.179)))
    features = [
        make_square_feature(lon - 0.018, lat + 0.012, 0.012, {"score": 0.82, "label": "new construction"}),
        make_square_feature(lon + 0.015, lat - 0.01, 0.009, {"score": 0.64, "label": "surface disturbance"}),
    ]
    result = {"type": "FeatureCollection", "features": features, "mode": "offline_fixture"}
    return {"job": store_analytics_result("change", req.dict(), result), "result": result}


@app.post("/api/analytics/viewshed")
def run_viewshed(req: AnalyticsRequest):
    observer = req.observer or {"latitude": 25.078, "longitude": 55.179}
    lat = float(observer.get("latitude", observer.get("lat", 25.078)))
    lon = float(observer.get("longitude", observer.get("lon", 55.179)))
    radius = float(req.radius_m or 5000)
    points = []
    for idx in range(0, 361, 12):
        angle = math.radians(idx)
        scale = (0.65 + 0.35 * abs(math.sin(angle * 2.7))) * radius / 111_000
        points.append([lon + math.cos(angle) * scale, lat + math.sin(angle) * scale])
    result = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [points]}, "properties": {"radius_m": radius, "mode": "offline_fixture"}}],
    }
    return {"job": store_analytics_result("viewshed", req.dict(), result), "result": result}


@app.post("/api/analytics/los")
def run_los(req: AnalyticsRequest):
    observer = req.observer or {"latitude": 25.078, "longitude": 55.179}
    destination = req.destination or {"latitude": 25.12, "longitude": 55.22}
    coords = [
        [float(observer.get("longitude", observer.get("lon", 55.179))), float(observer.get("latitude", observer.get("lat", 25.078)))],
        [float(destination.get("longitude", destination.get("lon", 55.22))), float(destination.get("latitude", destination.get("lat", 25.12)))],
    ]
    result = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": {"visible": True, "clearance_m": 42.0}}],
    }
    return {"job": store_analytics_result("los", req.dict(), result), "result": result}


@app.post("/api/analytics/routes")
def run_route_options(req: AnalyticsRequest):
    observer = req.observer or {"latitude": 25.078, "longitude": 55.179}
    destination = req.destination or {"latitude": 25.276987, "longitude": 55.296249}
    start = [float(observer.get("longitude", observer.get("lon", 55.179))), float(observer.get("latitude", observer.get("lat", 25.078)))]
    end = [float(destination.get("longitude", destination.get("lon", 55.296249))), float(destination.get("latitude", destination.get("lat", 25.276987)))]
    routes = []
    for idx, offset in enumerate([-0.03, 0.0, 0.03], start=1):
        mid = [(start[0] + end[0]) / 2 + offset, (start[1] + end[1]) / 2 - offset / 2]
        routes.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [start, mid, end]},
            "properties": {"option": idx, "risk": ["least exposure", "shortest", "least risk"][idx - 1], "duration_minutes": 68 + idx * 7},
        })
    result = {"type": "FeatureCollection", "features": routes}
    return {"job": store_analytics_result("routes", req.dict(), result), "result": result}


@app.post("/api/analytics/pol")
def run_pattern_of_life(req: AnalyticsRequest):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT ST_X(geom) AS lon, ST_Y(geom) AS lat, count(*) AS count
            FROM track_points
            WHERE geom IS NOT NULL
            GROUP BY ST_SnapToGrid(geom, 0.02), lon, lat
            ORDER BY count DESC
            LIMIT 50
        """)
        rows = cursor.fetchall()
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]}, "properties": {"count": row["count"]}}
        for row in rows
    ] or [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [55.179, 25.078]}, "properties": {"count": 7, "mode": "offline_fixture"}}
    ]
    result = {"type": "FeatureCollection", "features": features}
    return {"job": store_analytics_result("pol", req.dict(), result), "result": result}


@app.get("/api/analytics/jobs")
def list_analytics_jobs(limit: int = 100):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, job_type, status, input, result, created_at
            FROM analytics_jobs
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,))
        return {"jobs": [dict(row) for row in cursor.fetchall()]}


@app.post("/api/collection/requirements")
def create_collection_requirement(req: CollectionRequirementCreate):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO collection_requirements (title, description, priority, status, target_id, aoi)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, title, description, priority, status, target_id, aoi, created_at, updated_at
        """, (req.title, req.description, req.priority, req.status, req.target_id, json.dumps(req.aoi or {})))
        requirement = dict(cursor.fetchone())
        cursor.execute("""
            INSERT INTO ped_tasks (requirement_id, title, status, metadata)
            VALUES (%s, %s, 'queued', %s)
            RETURNING id, requirement_id, collection_task_id, title, status, assignee, metadata, created_at, updated_at
        """, (requirement["id"], f"PED exploitation for {req.title}", json.dumps({"source": "collection_requirement"})))
        ped_task = dict(cursor.fetchone())
    publish_event("ops", {"type": "collection_requirement_created", "requirement": requirement, "ped_task": ped_task})
    return {"success": True, "requirement": requirement, "ped_task": ped_task}


@app.get("/api/collection/requirements")
def list_collection_requirements():
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, title, description, priority, status, target_id, aoi, created_at, updated_at
            FROM collection_requirements
            ORDER BY updated_at DESC, created_at DESC
        """)
        return {"requirements": [dict(row) for row in cursor.fetchall()]}


@app.get("/api/collection/passes")
def predict_collection_passes(target_id: Optional[str] = None, count: int = 5):
    targets = fetch_targets_for_ops()
    target = next((item for item in targets if item["id"] == target_id), targets[0] if targets else None)
    now = datetime.now(timezone.utc)
    passes = []
    for idx in range(max(1, min(count, 12))):
        start = now + timedelta(minutes=18 + idx * 47)
        passes.append({
            "id": f"PASS-{idx + 1:03d}",
            "target_id": target["id"] if target else None,
            "target_name": target.get("properties", {}).get("name") if target else None,
            "satellite": ["WORLDVIEW-042", "FLOCK-1C-3", "SKYSAT-19"][idx % 3],
            "sensor": ["Optical", "SAR", "Thermal"][idx % 3],
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(minutes=7 + idx % 4)).isoformat(),
            "access_score": round(0.91 - idx * 0.06, 2),
            "cloud_cover": (idx * 13) % 55,
        })
    return {"passes": passes}


@app.get("/api/ped/tasks")
def list_ped_tasks():
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, requirement_id, collection_task_id, title, status, assignee, metadata, created_at, updated_at
            FROM ped_tasks
            ORDER BY updated_at DESC, created_at DESC
        """)
        return {"tasks": [dict(row) for row in cursor.fetchall()]}


@app.put("/api/ped/tasks/{task_id}")
def update_ped_task(task_id: int, req: PedTaskUpdate):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            UPDATE ped_tasks
            SET status = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING id, requirement_id, collection_task_id, title, status, assignee, metadata, created_at, updated_at
        """, (req.status, task_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="PED task not found")
        task = dict(row)
    publish_event("ops", {"type": "ped_task_updated", "task": task})
    return {"success": True, "task": task}


@app.post("/api/reports/target-packages")
def create_target_package(req: ReportCreate):
    ensure_platform_tables()
    targets = fetch_targets_for_ops()
    target = next((item for item in targets if item["id"] == req.target_id), targets[0] if targets else None)
    if not target:
        raise HTTPException(status_code=404, detail="No targets available for report")
    content = {
        "target": target,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": ["summary", "aimpoints", "collection", "detections"],
    }
    title = req.title or f"Target Package - {target['properties'].get('name', target['id'])}"
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO reports (title, target_id, report_type, status, content)
            VALUES (%s, %s, 'target_package', 'ready', %s)
            RETURNING id, title, target_id, report_type, status, content, created_at
        """, (title, target["id"], json.dumps(content)))
        report = dict(cursor.fetchone())
    publish_event("ops", {"type": "report_ready", "report": report})
    return {"success": True, "report": report}


@app.get("/api/reports")
def list_reports():
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, title, target_id, report_type, status, content, created_at
            FROM reports
            ORDER BY created_at DESC
        """)
        return {"reports": [dict(row) for row in cursor.fetchall()]}


@app.get("/api/reports/{report_id}/export")
def export_report(report_id: int, format: str = "json"):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("SELECT id, title, target_id, report_type, status, content, created_at FROM reports WHERE id = %s", (report_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Report not found")
        report = dict(row)
    export_format = format.lower()
    if export_format == "geojson":
        target = (report.get("content") or {}).get("target") or {}
        props = target.get("properties") or {}
        lon = props.get("longitude")
        lat = props.get("latitude")
        geometry = {"type": "Point", "coordinates": [lon, lat]} if lon is not None and lat is not None else None
        return {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "report_id": report["id"],
                    "title": report["title"],
                    "target_id": report["target_id"],
                    "status": report["status"],
                    "content": report["content"],
                },
            }],
        }
    if export_format == "json":
        return {"report": report, "format": format.lower()}
    if export_format in {"kmz", "pdf"}:
        return {"report": report, "format": export_format, "message": "Binary export renderer is queued for the production packaging phase."}
    raise HTTPException(status_code=400, detail="Unsupported report export format")


@app.get("/api/models")
def list_models():
    ensure_platform_tables()
    model_path = os.getenv("MODEL_PATH", "/app/yolov8n.pt")
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO models (name, version, model_path, status, promoted)
            SELECT 'YOLOv8 Local', 'local', %s, 'available', TRUE
            WHERE NOT EXISTS (SELECT 1 FROM models)
        """, (model_path,))
        cursor.execute("""
            SELECT id, name, version, model_path, status, metrics, promoted, created_at
            FROM models
            ORDER BY promoted DESC, created_at DESC
        """)
        models = [dict(row) for row in cursor.fetchall()]
    return {"models": models, "inference": {"url": os.getenv("INFERENCE_URL", "http://inference:8001")}}


@app.get("/api/models/datasets")
def list_model_datasets():
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, name, dataset_type, domain, file_path, status, metadata, created_at, updated_at
            FROM datasets
            ORDER BY created_at DESC
        """)
        return {"datasets": [dict(row) for row in cursor.fetchall()]}


@app.post("/api/models/datasets")
async def upload_model_dataset(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    dataset_type: str = Form("object_detection"),
    domain: str = Form("GEOINT"),
):
    ensure_platform_tables()
    filename = safe_filename(file.filename or "dataset.zip")
    dataset_id = uuid.uuid4().hex
    root = Path(os.getenv("DATASET_PATH", "/data/datasets"))
    root.mkdir(parents=True, exist_ok=True)
    local_path = root / f"{dataset_id}_{filename}"
    size = 0
    try:
        with local_path.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                handle.write(chunk)
    finally:
        await file.close()
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


@app.post("/api/models/{model_id}/promote")
def promote_model(model_id: int):
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


@app.post("/api/training/jobs")
def create_training_job(req: TrainingJobCreate):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO training_jobs (name, dataset_path, epochs, status, metrics)
            VALUES (%s, %s, %s, 'queued', %s)
            RETURNING id, name, dataset_path, epochs, status, metrics, created_at, updated_at
        """, (req.name, req.dataset_path, req.epochs, json.dumps({"mode": "offline_stub"})))
        job = dict(cursor.fetchone())
    publish_event("training:%s" % job["id"], {"type": "training_queued", "job": job})
    publish_event("ops", {"type": "training_queued", "job": job})
    return {"success": True, "job": job}


@app.get("/api/training/jobs")
def list_training_jobs():
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, name, dataset_path, epochs, status, metrics, created_at, updated_at
            FROM training_jobs
            ORDER BY created_at DESC
        """)
        return {"jobs": [dict(row) for row in cursor.fetchall()]}


@app.get("/api/targets/{target_id}/detections")
def get_target_detections(target_id: str, limit: int = 50):
    """Return detections linked to a target, with a geospatial fallback for seeded targets."""
    with db.get_session() as session:
        result = session.run("""
            MATCH (t:Target)
            WHERE elementId(t) = $target_id OR t.id = $target_id
            OPTIONAL MATCH (t)-[:DETECTED_AS]->(d:Detection)
            RETURN t.latitude AS lat, t.longitude AS lon, collect(d.postgis_id) AS detection_ids
        """, {"target_id": target_id})
        record = result.single()
        if not record:
            raise HTTPException(status_code=404, detail="Target not found")

        detection_ids = [int(i) for i in record["detection_ids"] if i is not None]
        lat = record["lat"]
        lon = record["lon"]

    base_query = """
        SELECT d.id, d.class, d.confidence, d.pass_id, d.metadata, d.created_at,
               ST_AsGeoJSON(d.geom) as geom_geojson,
               ST_AsGeoJSON(d.centroid) as centroid_geojson,
               sp.name as pass_name, sp.acquisition_time, sp.file_path
        FROM detections d
        JOIN satellite_passes sp ON d.pass_id = sp.id
    """
    with postgis_db.get_cursor() as cursor:
        if detection_ids:
            cursor.execute(base_query + """
                WHERE d.id = ANY(%s)
                ORDER BY sp.acquisition_time DESC NULLS LAST, d.confidence DESC
                LIMIT %s
            """, (detection_ids, limit))
        elif lat is not None and lon is not None:
            cursor.execute(base_query + """
                ORDER BY d.centroid <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                LIMIT %s
            """, (lon, lat, limit))
        else:
            return {"detections": []}
        return {"detections": [dict(row) for row in cursor.fetchall()]}

@app.put("/api/targets/{target_id}/status")
def update_target_status(target_id: str, req: TargetStatusUpdate):
    with db.get_session() as session:
        result = session.run("""
            MATCH (t:Target)
            WHERE elementId(t) = $id OR t.id = $id
            SET t.status = $status
            RETURN t
        """, {"id": target_id, "status": req.status})
        
        record = result.single()
        if record:
            target = dict(record["t"])
            publish_event("ops", {"type": "target_status_updated", "target_id": target_id, "status": req.status})
            return {"success": True, "target": target}
        return {"success": False, "error": "Target not found"}

@app.get("/api/constellation")
def get_constellation():
    with db.get_session() as session:
        result = session.run("""
            MATCH (s:Satellite)
            RETURN s
        """)
        satellites = []
        for record in result:
            s = record["s"]
            satellites.append({
                "id": s.element_id,
                "properties": dict(s)
            })
        return {"satellites": satellites}

# --- New Imagery & Detection Endpoints ---
@app.get("/api/imagery")
def get_imagery(
    bbox: Optional[str] = Query(None, description="min_lon,min_lat,max_lon,max_lat"),
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    sensor_type: Optional[str] = None
):
    """Query satellite passes from PostGIS catalog."""
    query = """
        SELECT id, name, file_path, sensor_type, acquisition_time, cloud_cover,
               ST_AsGeoJSON(footprint) as footprint_geojson, crs, created_at
        FROM satellite_passes
        WHERE 1=1
    """
    params = []
    
    if bbox:
        min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
        query += " AND ST_Intersects(footprint, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
        params.extend([min_lon, min_lat, max_lon, max_lat])
    
    if start_time:
        query += " AND acquisition_time >= %s"
        params.append(start_time)
    if end_time:
        query += " AND acquisition_time <= %s"
        params.append(end_time)
    if sensor_type:
        query += " AND sensor_type = %s"
        params.append(sensor_type)
    
    query += " ORDER BY acquisition_time DESC"
    
    with postgis_db.get_cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return {"imagery": [dict(r) for r in rows]}

@app.get("/api/imagery/{pass_id}/tiles")
def get_imagery_tiles(pass_id: int):
    """Return TiTiler tile URL for a given satellite pass."""
    with postgis_db.get_cursor() as cursor:
        cursor.execute("SELECT file_path FROM satellite_passes WHERE id = %s", (pass_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Satellite pass not found")
        
        titiler_url = os.getenv("TITILER_URL", "http://localhost:8081")
        tile_url = f"{titiler_url}/cog/tiles/{{z}}/{{x}}/{{y}}?url={row['file_path']}"
        return {"pass_id": pass_id, "tile_url": tile_url, "file_path": row["file_path"]}


@app.get("/api/imagery/{pass_id}/bands")
def get_imagery_bands(pass_id: int):
    with postgis_db.get_cursor() as cursor:
        cursor.execute("SELECT file_path, sensor_type FROM satellite_passes WHERE id = %s", (pass_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Satellite pass not found")

    try:
        import rasterio

        with rasterio.open(row["file_path"]) as src:
            stats = []
            for index in range(1, min(src.count, 8) + 1):
                band = src.read(index, masked=True)
                stats.append({
                    "band": index,
                    "dtype": str(band.dtype),
                    "min": float(band.min()) if band.count() else None,
                    "max": float(band.max()) if band.count() else None,
                    "mean": float(band.mean()) if band.count() else None,
                })
            return {
                "pass_id": pass_id,
                "sensor_type": row["sensor_type"],
                "band_count": src.count,
                "crs": str(src.crs),
                "width": src.width,
                "height": src.height,
                "statistics": stats,
                "render_modes": ["rgb", "single", "ndvi", "ndwi", "nbr", "sar_db", "thermal_k"],
            }
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Unable to inspect imagery bands: {exc}")


@app.get("/api/ingest/uploads")
def list_upload_jobs():
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, upload_id, filename, file_path, media_type, handler, status, celery_task_id, metadata, created_at, updated_at
            FROM upload_jobs
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 250
        """)
        return {"uploads": [reconciled_upload_job(dict(row)) for row in cursor.fetchall()]}


@app.get("/api/basemap/countries")
def get_basemap_countries():
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT jsonb_build_object(
                'type', 'FeatureCollection',
                'features', coalesce(jsonb_agg(jsonb_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(geom)::jsonb,
                    'properties', jsonb_build_object('name', name, 'admin', admin, 'iso_a3', iso_a3)
                )), '[]'::jsonb)
            ) AS geojson
            FROM ne_countries
        """)
        row = cursor.fetchone()
        return row["geojson"] if row else {"type": "FeatureCollection", "features": []}

@app.get("/api/detections")
def get_detections(
    bbox: Optional[str] = Query(None, description="min_lon,min_lat,max_lon,max_lat"),
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    det_class: Optional[str] = None,
    limit: int = 1000
):
    """Query detections from PostGIS with spatial and temporal filters."""
    query = """
        SELECT d.id, d.class, d.confidence, d.pass_id, d.metadata, d.created_at,
               ST_AsGeoJSON(d.geom) as geom_geojson,
               ST_AsGeoJSON(d.centroid) as centroid_geojson,
               sp.name as pass_name, sp.acquisition_time, sp.file_path
        FROM detections d
        JOIN satellite_passes sp ON d.pass_id = sp.id
        WHERE 1=1
    """
    params = []
    
    if bbox:
        min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
        query += " AND ST_Intersects(d.geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
        params.extend([min_lon, min_lat, max_lon, max_lat])
    
    if start_time:
        query += " AND sp.acquisition_time >= %s"
        params.append(start_time)
    if end_time:
        query += " AND sp.acquisition_time <= %s"
        params.append(end_time)
    if det_class:
        query += " AND d.class = %s"
        params.append(det_class)
    
    query += " ORDER BY d.confidence DESC LIMIT %s"
    params.append(limit)
    
    with postgis_db.get_cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        detections = []
        for row in rows:
            item = dict(row)
            item["metadata"] = enriched_detection_metadata(item["class"], item.get("metadata"))
            detections.append(item)
        return {"detections": detections}


@app.get("/api/detections/classes")
def get_detection_classes(
    bbox: Optional[str] = Query(None, description="min_lon,min_lat,max_lon,max_lat"),
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
):
    """Return detected classes as map/globe filter metadata with ontology and threat rollups."""
    query = """
        WITH filtered AS (
            SELECT d.class,
                   d.confidence,
                   coalesce(d.metadata->>'allegiance', 'unknown') AS allegiance
            FROM detections d
            JOIN satellite_passes sp ON d.pass_id = sp.id
            WHERE 1=1
    """
    params = []
    if bbox:
        min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
        query += " AND ST_Intersects(d.geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
        params.extend([min_lon, min_lat, max_lon, max_lat])
    if start_time:
        query += " AND sp.acquisition_time >= %s"
        params.append(start_time)
    if end_time:
        query += " AND sp.acquisition_time <= %s"
        params.append(end_time)
    query += """
        ),
        class_counts AS (
            SELECT class,
                   count(*) AS count,
                   max(confidence) AS max_confidence,
                   avg(confidence) AS avg_confidence
            FROM filtered
            GROUP BY class
        ),
        allegiance_counts AS (
            SELECT class, allegiance, count(*) AS count
            FROM filtered
            GROUP BY class, allegiance
        ),
        allegiance_json AS (
            SELECT class, jsonb_object_agg(allegiance, count) AS allegiance_counts
            FROM allegiance_counts
            GROUP BY class
        )
        SELECT c.class,
               c.count,
               c.max_confidence,
               c.avg_confidence,
               coalesce(a.allegiance_counts, '{}'::jsonb) AS allegiance_counts
        FROM class_counts c
        LEFT JOIN allegiance_json a ON a.class = c.class
        ORDER BY c.count DESC, c.class ASC
    """
    with postgis_db.get_cursor() as cursor:
        cursor.execute(query, params)
        classes = []
        for row in cursor.fetchall():
            ontology = detection_ontology(row["class"])
            classes.append({
                "class": row["class"],
                "label": ontology["label"],
                "count": row["count"],
                "max_confidence": float(row["max_confidence"] or 0),
                "avg_confidence": float(row["avg_confidence"] or 0),
                "ontology": ontology,
                "threat_level": ontology["threat_level"],
                "allegiance_counts": row["allegiance_counts"] or {},
            })
        return {"classes": classes}

@app.get("/api/detections/geojson")
def get_detections_geojson(
    bbox: Optional[str] = Query(None, description="min_lon,min_lat,max_lon,max_lat"),
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    det_class: Optional[str] = None
):
    """Return detections as GeoJSON FeatureCollection."""
    with postgis_db.get_cursor() as cursor:
        query = """
            SELECT d.id, d.class, d.confidence, d.pass_id, d.created_at, d.metadata,
                   ST_AsGeoJSON(d.geom)::jsonb AS geometry
            FROM detections d
            JOIN satellite_passes sp ON d.pass_id = sp.id
            WHERE 1=1
        """
        params = []
        if bbox:
            min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
            query += " AND ST_Intersects(d.geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
            params.extend([min_lon, min_lat, max_lon, max_lat])
        if start_time:
            query += " AND sp.acquisition_time >= %s"
            params.append(start_time)
        if end_time:
            query += " AND sp.acquisition_time <= %s"
            params.append(end_time)
        if det_class:
            query += " AND d.class = %s"
            params.append(det_class)
        query += " ORDER BY d.created_at DESC LIMIT 5000"
        cursor.execute(query, params)
        features = []
        for row in cursor.fetchall():
            metadata = enriched_detection_metadata(row["class"], row["metadata"])
            features.append({
                "type": "Feature",
                "geometry": row["geometry"],
                "properties": {
                    "id": row["id"],
                    "class": row["class"],
                    "label": metadata["ontology"]["label"],
                    "confidence": row["confidence"],
                    "pass_id": row["pass_id"],
                    "created_at": row["created_at"],
                    "metadata": metadata,
                    "ontology": metadata["ontology"],
                    "threat_level": metadata.get("threat_level"),
                    "allegiance": metadata.get("allegiance", "unknown"),
                },
            })
        return {"type": "FeatureCollection", "features": features}


@app.patch("/api/detections/{detection_id}/tag")
def tag_detection(detection_id: int, update: DetectionTagUpdate):
    allegiance = update.allegiance.strip().lower()
    if allegiance not in {"friendly", "hostile", "neutral", "unknown"}:
        raise HTTPException(status_code=400, detail="allegiance must be friendly, hostile, neutral, or unknown")
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            UPDATE detections
            SET metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object('allegiance', %s)
            WHERE id = %s
            RETURNING id, class, metadata
        """, (allegiance, detection_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Detection not found")
    try:
        with db.get_session() as session:
            session.run("""
                MATCH (d:Detection {postgis_id: $det_id})
                SET d.allegiance = $allegiance
            """, {"det_id": detection_id, "allegiance": allegiance})
    except Exception:
        pass
    publish_event("detections", {"type": "detection_tagged", "id": detection_id, "allegiance": allegiance})
    return {"id": row["id"], "class": row["class"], "metadata": enriched_detection_metadata(row["class"], row["metadata"])}

@app.post("/api/detections/resolve")
def resolve_detection(detection_id: int, distance_threshold_meters: float = 500.0):
    """
    Entity resolution: Check if a detection matches an existing Neo4j Target within threshold.
    If found, link them. If not, create a new Target.
    """
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT d.class, d.confidence, ST_X(d.centroid) as lon, ST_Y(d.centroid) as lat, d.metadata
            FROM detections d WHERE d.id = %s
        """, (detection_id,))
        det = cursor.fetchone()
        if not det:
            raise HTTPException(status_code=404, detail="Detection not found")
    
    with db.get_session() as session:
        # Search for existing targets near this location
        result = session.run("""
            MATCH (t:Target)
            WHERE t.latitude IS NOT NULL AND t.longitude IS NOT NULL
              AND point.distance(
                  point({latitude: t.latitude, longitude: t.longitude}),
                  point({latitude: $lat, longitude: $lon})
              ) < $threshold
            RETURN t
            ORDER BY point.distance(
                point({latitude: t.latitude, longitude: t.longitude}),
                point({latitude: $lat, longitude: $lon})
            ) ASC
            LIMIT 1
        """, {"lat": det["lat"], "lon": det["lon"], "threshold": distance_threshold_meters})
        
        existing = result.single()
        
        if existing:
            target = existing["t"]
            # Link detection to existing target
            link_result = session.run("""
                MATCH (t:Target) WHERE elementId(t) = $target_id
                MERGE (d:Detection {postgis_id: $det_id})
                ON CREATE SET d.class = $det_class,
                              d.confidence = $confidence,
                              d.latitude = $lat,
                              d.longitude = $lon,
                              d.created_at = datetime()
                MERGE (t)-[:DETECTED_AS]->(d)
                RETURN t, d
            """, {
                "target_id": target.element_id,
                "det_id": detection_id,
                "det_class": det["class"],
                "confidence": det["confidence"],
                "lat": det["lat"],
                "lon": det["lon"],
            })
            if not link_result.single():
                raise HTTPException(status_code=409, detail="Detection node could not be linked")
            return {
                "resolved": True,
                "action": "linked_to_existing",
                "target_id": target.element_id,
                "target_name": target.get("name", "Unknown")
            }
        else:
            # Create new target
            import uuid
            target_id = str(uuid.uuid4())
            target_name = f"Unknown {det['class']} #{target_id[:6]}"
            
            session.run("""
                MERGE (d:Detection {postgis_id: $det_id})
                ON CREATE SET d.class = $det_class,
                              d.confidence = $confidence,
                              d.latitude = $lat,
                              d.longitude = $lon,
                              d.created_at = datetime()
                CREATE (t:Target {
                    id: $id,
                    name: $name,
                    priority: 'High',
                    status: 'Active',
                    description: 'Automated detection via CV pipeline. Class: ' + $det_class,
                    latitude: $lat,
                    longitude: $lon,
                    confidence: $confidence,
                    detection_id: $det_id
                })
                MERGE (t)-[:DETECTED_AS]->(d)
            """, {
                "id": target_id,
                "name": target_name,
                "det_class": det["class"],
                "lat": det["lat"],
                "lon": det["lon"],
                "confidence": det["confidence"],
                "det_id": detection_id
            })
            return {
                "resolved": True,
                "action": "created_new",
                "target_id": target_id,
                "target_name": target_name
            }

@app.post("/api/ingest")
def trigger_ingest(req: IngestRequest):
    task = process_satellite_imagery.delay(req.image_url, req.sensor_type, req.acquisition_time)
    return {
        "success": True,
        "task_id": task.id,
        "status_url": f"/api/ingest/jobs/{task.id}",
        "message": "Satellite imagery pipeline initiated.",
    }


@app.post("/api/ingest/upload")
async def upload_imagery(
    file: UploadFile = File(...),
    sensor_type: str = Form("Optical"),
    acquisition_time: Optional[str] = Form(None),
    auto_process: bool = Form(True),
):
    ensure_platform_tables()
    filename = safe_filename(file.filename or "upload.tif")
    media_type, handler = classify_upload(filename)

    if media_type == "fmv":
        upload_dir = Path(os.getenv("FMV_PATH", "/data/fmv")) / "incoming"
    else:
        upload_dir = Path(os.getenv("IMAGERY_PATH", "/data/imagery")) / "incoming"
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    local_path = upload_dir / f"{upload_id}_{filename}"

    size = 0
    try:
        with local_path.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                handle.write(chunk)
    finally:
        await file.close()

    if size == 0:
        local_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    response = {
        "success": True,
        "file_path": str(local_path),
        "filename": filename,
        "bytes": size,
        "sensor_type": sensor_type,
        "auto_process": auto_process,
        "upload_id": upload_id,
        "media_type": media_type,
        "handler": handler,
    }
    domain = domain_for_media(media_type, sensor_type)
    celery_task_id = None
    status = "stored"
    upload_job_recorded = False
    if media_type == "imagery":
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute("""
                INSERT INTO upload_jobs (upload_id, filename, file_path, media_type, handler, status, celery_task_id, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                upload_id,
                filename,
                str(local_path),
                media_type,
                handler,
                status,
                None,
                json.dumps({
                    "sensor_type": sensor_type,
                    "auto_process": auto_process,
                    "bytes": size,
                    "stage": "stored",
                    "progress": 0,
                    "message": "Upload stored.",
                }),
            ))
        upload_job_recorded = True

    if media_type == "imagery" and auto_process:
        task = process_satellite_imagery.delay(str(local_path), sensor_type, acquisition_time, upload_id)
        celery_task_id = task.id
        status = "queued"
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute("""
                UPDATE upload_jobs
                SET status = %s,
                    celery_task_id = %s,
                    metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb,
                    updated_at = NOW()
                WHERE upload_id = %s
            """, (
                status,
                celery_task_id,
                json.dumps({
                    "task_id": celery_task_id,
                    "stage": "queued",
                    "progress": 5,
                    "message": "Imagery processing queued.",
                }),
                upload_id,
            ))
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
            cursor.execute("""
                INSERT INTO fmv_clips (name, file_path, hls_path, duration_seconds, width, height, fps, status, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, name, file_path, hls_path, duration_seconds, width, height, fps, status, metadata, created_at, updated_at
            """, (
                filename,
                str(clip_path),
                str(hls_path) if hls_path else None,
                metadata["duration_seconds"],
                metadata["width"],
                metadata["height"],
                metadata["fps"],
                "ready" if hls_path else "stored",
                json.dumps({**metadata, "bytes": size, "upload_id": upload_id}),
            ))
            clip = dict(cursor.fetchone())
            cursor.executemany("""
                INSERT INTO fmv_frames (clip_id, frame_index, timestamp_seconds, telemetry, footprint)
                VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326))
                ON CONFLICT (clip_id, frame_index) DO UPDATE SET
                    timestamp_seconds = EXCLUDED.timestamp_seconds,
                    telemetry = EXCLUDED.telemetry,
                    footprint = EXCLUDED.footprint
            """, telemetry_rows_for_clip(clip["id"], clip["duration_seconds"], clip["fps"]))
        clip["stream_url"] = fmv_public_url(clip.get("hls_path"), clip["file_path"])
        status = "ready"
        response.update({"message": "FMV upload received and HLS/KLV catalog prepared.", "clip": clip})
    elif media_type == "vector":
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute("""
                INSERT INTO vector_layers (name, file_path, layer_type, metadata)
                VALUES (%s, %s, 'vector', %s)
                RETURNING id, name, file_path, layer_type, feature_count, metadata, created_at
            """, (filename, str(local_path), json.dumps({"upload_id": upload_id, "handler": handler})))
            response.update({"message": "Vector upload stored for cataloging.", "layer": dict(cursor.fetchone())})
    elif media_type in {"document", "audio"}:
        title = filename.rsplit(".", 1)[0]
        summary = (
            "Audio uploaded. Transcription is queued; configure a local or remote transcriber to replace this placeholder."
            if media_type == "audio"
            else "Document uploaded. LLM extraction is queued for automated processing."
        )
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute("""
                INSERT INTO documents (upload_id, domain, title, file_path, media_type, status, summary, metadata)
                VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s)
                RETURNING id, upload_id, domain, title, file_path, source_url, media_type, status, summary, metadata, created_at, updated_at
            """, (
                upload_id,
                domain,
                title[:255],
                str(local_path),
                media_type,
                summary,
                json.dumps({"handler": handler, "bytes": size, "sensor_type": sensor_type}),
            ))
            document = dict(cursor.fetchone())
            if media_type == "audio":
                cursor.execute("""
                    INSERT INTO transcripts (document_id, text, confidence, status, segments)
                    VALUES (%s, %s, 0, 'placeholder', %s)
                    RETURNING id, document_id, language, text, confidence, segments, status, created_at
                """, (
                    document["id"],
                    "Transcription placeholder. Configure a local Whisper-compatible service or remote transcription provider.",
                    json.dumps([]),
                ))
                response["transcript"] = dict(cursor.fetchone())
        response.update({"message": f"{media_type.title()} upload received and queued for AI extraction.", "document": document})
    else:
        response["message"] = "Upload received."

    if not upload_job_recorded:
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute("""
                INSERT INTO upload_jobs (upload_id, filename, file_path, media_type, handler, status, celery_task_id, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                upload_id,
                filename,
                response.get("clip", {}).get("file_path") or str(local_path),
                media_type,
                handler,
                status,
                celery_task_id,
                json.dumps({
                    "sensor_type": sensor_type,
                    "auto_process": auto_process,
                    "bytes": size,
                    "stage": status,
                    "progress": 100 if status == "ready" else 0,
                    "message": f"{media_type.title()} upload {status}.",
                }),
            ))

    publish_event("ingest", {"type": "upload_received", "upload": response})
    publish_event("ops", {"type": "upload_received", "upload": response})
    record_observation(domain, f"{media_type}_upload", filename, {"upload": response}, confidence=0.5, provenance={"source": "upload", "handler": handler})
    record_timeline_event(domain, "upload_received", filename, {"upload_id": upload_id, "media_type": media_type})
    return response


@app.get("/api/ingest/jobs/{task_id}")
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
        return {"task_id": task_id, "celery_state": "unknown", "message": f"Unable to inspect task state: {exc}"}


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
        next_metadata.setdefault("message", "Waiting for imagery worker." if celery_state == "pending" else "Imagery worker accepted the task.")

    job["status"] = next_status
    job["metadata"] = next_metadata
    return job


@app.post("/api/ingest/url")
def ingest_url(req: IngestUrlRequest):
    ensure_platform_tables()
    upload_id = uuid.uuid4().hex
    domain = normalize_domain(req.domain, "OSINT")
    title = req.title or req.url
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO upload_jobs (upload_id, filename, file_path, media_type, handler, status, metadata)
            VALUES (%s, %s, %s, %s, %s, 'queued', %s)
        """, (
            upload_id,
            safe_filename(title)[:255],
            req.url,
            req.source_type,
            "workers.url.process",
            json.dumps({"domain": domain, "auto_process": req.auto_process, "source_url": req.url}),
        ))
        cursor.execute("""
            INSERT INTO documents (upload_id, domain, title, source_url, media_type, status, summary, metadata)
            VALUES (%s, %s, %s, %s, %s, 'queued', %s, %s)
            RETURNING id, upload_id, domain, title, source_url, media_type, status, summary, metadata, created_at, updated_at
        """, (
            upload_id,
            domain,
            title[:255],
            req.url,
            req.source_type,
            "Queued for automated retrieval and LLM extraction.",
            json.dumps({"handler": "workers.url.process"}),
        ))
        document = dict(cursor.fetchone())
    record_observation(domain, "url_ingest", title, {"url": req.url, "document_id": document["id"]}, confidence=0.5, provenance={"source": "url"})
    record_timeline_event(domain, "url_ingest_queued", title, {"document": document})
    publish_event("ingest", {"type": "url_ingest_queued", "document": document})
    publish_event("ops", {"type": "url_ingest_queued", "document": document})
    return {"success": True, "upload_id": upload_id, "document": document, "message": "URL ingestion queued."}


@app.post("/api/ai/analyze")
def ai_analyze(req: AIAnalysisRequest):
    ensure_platform_tables()
    try:
        reply = get_ai_response(req.prompt)
    except AIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    domain = normalize_domain(req.domain, "WORKFLOW")
    analysis = {
        "summary": reply,
        "citations": [
            {"type": "ontology", "label": "Neo4j read-only summary"},
            {"type": "context", "label": req.entity_id or domain},
        ],
        "next_actions": [
            {"action_type": "generate_report", "label": "Draft intelligence summary", "requires_approval": True},
            {"action_type": "create_requirement", "label": "Create collection requirement", "requires_approval": True},
        ],
        "policy": "human_approval_required",
    }
    record_timeline_event(domain, "ai_analysis", "AI analysis generated", {"prompt": req.prompt, "entity_id": req.entity_id})
    return {"analysis": analysis, "status": "ok"}


@app.post("/api/ai/extract")
def ai_extract(req: AIAnalysisRequest):
    ensure_platform_tables()
    text = req.prompt or json.dumps(req.context)
    tokens = sorted({word.strip(".,:;()[]{}").title() for word in text.split() if len(word.strip(".,:;()[]{}")) > 4})[:12]
    entities = [{"label": token, "type": "Entity", "confidence": 0.52} for token in tokens]
    return {"entities": entities, "citations": [{"type": "input", "label": "submitted text/context"}], "status": "ok"}


@app.post("/api/ai/link")
def ai_link(req: AIAnalysisRequest):
    ensure_platform_tables()
    return {
        "links": [
            {"source": req.entity_id or "submitted_context", "target": "ontology", "relationship": "CANDIDATE_MATCH", "confidence": 0.58}
        ],
        "status": "review_required",
        "policy": "human_approval_required",
    }


@app.post("/api/ai/propose-actions")
def ai_propose_actions(req: AIActionProposalRequest):
    ensure_platform_tables()
    domain = normalize_domain(req.domain, "WORKFLOW")
    title = req.payload.get("title") or f"AI proposal: {req.action_type.replace('_', ' ')}"
    rationale = f"Proposed from analyst prompt: {req.prompt[:500]}"
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO ai_action_proposals (action_type, title, domain, target_id, rationale, sources, payload, confidence, risk_level, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 0.62, %s, 'pending_approval')
            RETURNING id, action_type, title, domain, target_id, rationale, sources, payload, confidence, risk_level, status, created_at, updated_at
        """, (
            req.action_type,
            title[:255],
            domain,
            req.target_id,
            rationale,
            json.dumps(req.payload.get("sources", [])),
            json.dumps({**req.payload, "prompt": req.prompt}),
            req.risk_level,
        ))
        proposal = dict(cursor.fetchone())
    record_timeline_event(domain, "ai_action_proposed", proposal["title"], {"proposal_id": proposal["id"]}, entity_id=req.target_id)
    publish_event("ops", {"type": "ai_action_proposed", "proposal": proposal})
    return {"proposal": proposal, "policy": "human_approval_required"}


@app.get("/api/actions/proposals")
def list_action_proposals(status: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=500)):
    ensure_platform_tables()
    params = []
    where = ""
    if status:
        where = "WHERE status = %s"
        params.append(status)
    params.append(limit)
    with postgis_db.get_cursor() as cursor:
        cursor.execute(f"""
            SELECT id, action_type, title, domain, target_id, rationale, sources, payload,
                   confidence, risk_level, status, proposed_by, approved_by, executed_at, result, created_at, updated_at
            FROM ai_action_proposals
            {where}
            ORDER BY created_at DESC
            LIMIT %s
        """, params)
        return {"proposals": [dict(row) for row in cursor.fetchall()]}


@app.post("/api/actions/proposals/{proposal_id}/approve")
def approve_action_proposal(proposal_id: int):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            UPDATE ai_action_proposals
            SET status = 'approved', approved_by = 'local_user', updated_at = NOW()
            WHERE id = %s AND status = 'pending_approval'
            RETURNING id, action_type, title, domain, target_id, rationale, sources, payload,
                      confidence, risk_level, status, proposed_by, approved_by, executed_at, result, created_at, updated_at
        """, (proposal_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Pending proposal not found")
        proposal = dict(row)
    record_timeline_event(proposal.get("domain") or "WORKFLOW", "ai_action_approved", proposal["title"], {"proposal_id": proposal_id}, entity_id=proposal.get("target_id"))
    publish_event("ops", {"type": "ai_action_approved", "proposal": proposal})
    return {"proposal": proposal}


@app.post("/api/actions/proposals/{proposal_id}/execute")
def execute_action_proposal(proposal_id: int):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, action_type, title, domain, target_id, rationale, sources, payload,
                   confidence, risk_level, status, proposed_by, approved_by, executed_at, result, created_at, updated_at
            FROM ai_action_proposals
            WHERE id = %s
        """, (proposal_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Proposal not found")
    proposal = dict(row)
    if proposal["status"] != "approved":
        raise HTTPException(status_code=409, detail="Proposal must be approved before execution")
    if proposal["risk_level"] not in {"low", "medium"}:
        raise HTTPException(status_code=403, detail="High-risk proposals require an external allowlisted connector")

    payload = proposal.get("payload") or {}
    result = {"executed": True, "mode": "internal_only"}
    if proposal["action_type"] == "generate_report":
        report = create_target_package(ReportCreate(target_id=proposal.get("target_id"), title=payload.get("title") or proposal["title"]))
        result["report"] = report.get("report")
    elif proposal["action_type"] == "create_requirement":
        requirement = create_collection_requirement(CollectionRequirementCreate(
            title=payload.get("title") or proposal["title"],
            description=proposal.get("rationale"),
            priority=payload.get("priority") or "Medium",
            status="approved",
            target_id=proposal.get("target_id"),
            aoi=payload.get("aoi") or {},
        ))
        result["requirement"] = requirement.get("requirement")
    elif proposal["action_type"] == "queue_analytic":
        result["analytic"] = run_viewshed(AnalyticsRequest(target_id=proposal.get("target_id"), radius_m=payload.get("radius_m", 5000))).get("job")
    else:
        result["message"] = "Action logged; no external dispatch connector is allowlisted."

    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            UPDATE ai_action_proposals
            SET status = 'executed', executed_at = NOW(), result = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING id, action_type, title, domain, target_id, rationale, sources, payload,
                      confidence, risk_level, status, proposed_by, approved_by, executed_at, result, created_at, updated_at
        """, (json.dumps(result, default=str), proposal_id))
        executed = dict(cursor.fetchone())
    record_timeline_event(executed.get("domain") or "WORKFLOW", "ai_action_executed", executed["title"], {"proposal_id": proposal_id, "result": result}, entity_id=executed.get("target_id"))
    publish_event("ops", {"type": "ai_action_executed", "proposal": executed})
    return {"proposal": executed, "result": result}


@app.websocket("/ws")
async def websocket_events(websocket: WebSocket, topic: str = "detections"):
    await websocket.accept()
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    redis_client = None
    pubsub = None
    try:
        import redis.asyncio as redis

        redis_client = redis.from_url(redis_url, decode_responses=True)
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"events:{topic}")
        await websocket.send_json({"type": "connected", "topic": topic})

        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message:
                try:
                    data = json.loads(message["data"])
                except (TypeError, json.JSONDecodeError):
                    data = {"type": "message", "payload": message["data"]}
                await websocket.send_json(data)
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    finally:
        if pubsub is not None:
            await pubsub.close()
        if redis_client is not None:
            await redis_client.close()

@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        response = get_ai_response(req.message)
        return {"reply": response, "status": "ok"}
    except AIUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)

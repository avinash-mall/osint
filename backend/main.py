import asyncio
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import Depends, FastAPI, Query, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List
import uvicorn
import requests
from database import db, postgis_db
from auth import (
    LDAPSettings,
    SessionUser,
    authenticate_admin,
    authenticate_ldap,
    cookie_kwargs,
    create_session_cookie,
    get_current_user,
    get_optional_user,
    load_auth_config,
    require_admin,
    save_auth_config,
    test_ldap_connection,
    SESSION_COOKIE,
)
from ai import AIUnavailable, ai_status, get_ai_response, get_llm_json
from imagery_metadata import extract_raster_metadata
from video_metadata import TelemetryMissingError, extract_telemetry
from detection_policy import active_detection_policy, detection_decision, parent_class_for_label
from candidate_linking import rank_candidate_links
from threat_assessment import (
    assess_detection_threat,
    category_for_class,
    conservative_detection_ontology,
    detection_ontology,
)
from worker import celery_app, process_fmv, process_satellite_imagery
import provider_lifecycle
import ontology as ontology_module
from ontology import (
    bump_version as ontology_bump_version,
    get_version as ontology_get_version,
    invalidate_cache as ontology_invalidate_cache,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Forward references resolve at call time, not definition time — the
    # imports below this point (e.g. _auto_seed_ontology_if_empty) are bound
    # by the time the ASGI server runs startup.
    _auto_seed_ontology_if_empty()
    # Neo4j uniqueness constraints / indexes for the Link Graph redesign.
    # Best-effort: failures are logged inside, never raised.
    from graph_schema import ensure_graph_schema
    ensure_graph_schema()
    try:
        yield
    finally:
        db.close()


app = FastAPI(title="Sentinel API", lifespan=lifespan)
logger = logging.getLogger(__name__)

_platform_schema_lock = threading.Lock()
_platform_schema_ready = False
_llm_detection_ontology_cache: dict[str, dict] = {}
DETECTION_POLICY = active_detection_policy()

def get_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Public mutating endpoints — everything else POST/PUT/PATCH/DELETE requires
# a valid session cookie. The login endpoint is the only public mutating one;
# logout is allowed unauthenticated so a stale cookie can always be cleared.
_PUBLIC_MUTATING_PATHS = {"/api/auth/login", "/api/auth/logout"}


@app.middleware("http")
async def require_session_on_mutations(request: Request, call_next):
    """Centralized auth gate for every mutating verb.

    Endpoints can still re-declare ``Depends(get_current_user)`` to receive the
    parsed user — this middleware only short-circuits unauthenticated mutating
    requests so we don't need to remember to add the dependency individually.
    """
    method = request.method.upper()
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        path = request.url.path
        # Allow CORS preflight to pass; that's an OPTIONS request handled above.
        if path not in _PUBLIC_MUTATING_PATHS:
            from fastapi.responses import JSONResponse  # local import keeps cold-start light
            user = get_optional_user(request)
            if user is None:
                return JSONResponse(status_code=401, content={"detail": "not authenticated"})
    return await call_next(request)

from schemas import (
    AIActionProposalRequest,
    AIAnalysisRequest,
    AnalyticsRequest,
    AuthTestRequest,
    CandidateLinkDecision,
    CollectionTaskCreate,
    ConfidenceConfig,
    DetectionQuery,
    DetectionTagUpdate,
    FeedConnectRequest,
    FeedEventCreate,
    GraphActionRequest,
    IngestRequest,
    IngestUrlRequest,
    LoginRequest,
    ManualDetectionBody,
    ObjectDetailsBody,
    OntologyAssignBody,
    OntologyBranchIn,
    OntologyBranchPatch,
    OntologyCreateObject,
    OntologyObjectIn,
    OntologyObjectPatch,
    OntologyUpdateRequest,
    PinRequest,
    PromptProfileBody,
    ReprocessRequest,
    ReviewUpdate,
    TrainingJobCreate,
)


from geometry import (
    make_square_feature,
    parse_bbox,
    point_payload,
)


from files import classify_upload, safe_filename, save_upload_file
from platform_schema import (
    acquire_schema_xact_lock,
    auto_seed_ontology_if_empty as _auto_seed_ontology_if_empty,
    ensure_collection_tables,
    ensure_feed_tables,
    ensure_platform_tables,
)
from fmv_helpers import (
    fmv_public_url,
    probe_video,
    transcode_hls,
)

from routers import ai as _ai_router
from routers import analytics as _analytics_router
from routers import auth as _auth_router
from routers import detections as _detections_router
from routers import fmv as _fmv_router
from routers import graph as _graph_router
from routers import health as _health_router
from routers import imagery as _imagery_router
from routers import inference as _inference_router
from routers import ingest as _ingest_router
from routers import models_training as _models_training_router
from routers import ontology as _ontology_router
from routers import reports as _reports_router
from routers import system as _system_router
from routers import ws as _ws_router
app.include_router(_ai_router.router)
app.include_router(_analytics_router.router)
app.include_router(_auth_router.router)
app.include_router(_detections_router.router)
app.include_router(_fmv_router.router)
app.include_router(_graph_router.router)
app.include_router(_health_router.router)
app.include_router(_imagery_router.router)
app.include_router(_inference_router.router)
app.include_router(_ingest_router.router)
app.include_router(_models_training_router.router)
app.include_router(_ontology_router.router)
app.include_router(_reports_router.router)
app.include_router(_system_router.router)
app.include_router(_ws_router.router)


from events import (
    domain_for_media,
    get_redis_client,
    normalize_domain,
    publish_event,
    record_observation,
    record_timeline_event,
)


def llm_detection_ontology(det_class: str, count: int = 0, avg_confidence: float = 0.0) -> dict:
    base = conservative_detection_ontology(det_class, confidence=avg_confidence)
    cached = _llm_detection_ontology_cache.get(det_class)
    if cached:
        return {**base, **cached}
    prompt = json.dumps({
        "task": "Classify a GEOINT computer-vision detection class for UI filtering.",
        "input": {
            "raw_class": det_class,
            "fallback_label": base["label"],
            "fallback_category": base["category"],
            "fallback_threat_level": base["threat_level"],
            "count_in_current_view": count,
            "avg_confidence": avg_confidence,
        },
        "required_json_schema": {
            "label": "short human label",
            "domain": "GEOINT",
            "category": "one of air, maritime, ground, combat, infrastructure, logistics, energy, facility, unknown",
            "threat_level": "one of low, medium, high, critical",
            "description": "one short analyst-facing sentence",
            "recommended_filter": "short filter chip text",
        },
    }, default=str)
    system = (
        "Return only compact JSON. Do not invent sightings or facts. "
        "Use the provided class name and counts only."
    )
    data = get_llm_json(prompt, system=system, max_tokens=260)
    generated = {
        **base,
        "label": str(data.get("label") or base["label"])[:80],
        "domain": "GEOINT",
        "category": base["category"],
        "threat_level": base["threat_level"],
        "threat_confidence": base["threat_confidence"],
        "assessment_status": base["assessment_status"],
        "evidence": base["evidence"],
        "description": str(data.get("description") or base["description"])[:280],
        "recommended_filter": str(data.get("recommended_filter") or data.get("label") or base["recommended_filter"])[:80],
        "generated_by": f"{ai_status().get('model') or 'llm'}; threat=deterministic-rules",
        "status": "ok",
    }
    _llm_detection_ontology_cache[det_class] = generated
    return generated


def enriched_detection_metadata(det_class: str, metadata: Optional[dict]) -> dict:
    enriched = dict(metadata or {})
    original_class = enriched.get("original_class") or det_class
    parent_class = enriched.get("parent_class") or parent_class_for_label(original_class)
    decision = detection_decision(original_class, enriched.get("confidence", 0), DETECTION_POLICY)
    enriched.setdefault("original_class", original_class)
    enriched.setdefault("parent_class", parent_class)
    enriched.setdefault("calibrated_confidence", enriched.get("confidence", 0))
    enriched.setdefault("review_status", decision["review_status"])
    enriched.setdefault("threshold_profile", decision["threshold_profile"])
    enriched.setdefault("class_threshold", decision["class_threshold"])
    enriched.setdefault("model_version", decision["model_version"])
    enriched.setdefault("taxonomy_version", decision["taxonomy_version"])
    ontology = dict(enriched.get("ontology") or {})
    generated = conservative_detection_ontology(
        det_class,
        confidence=enriched.get("confidence", 0),
        allegiance=enriched.get("allegiance"),
        description=ontology.get("description"),
    )
    enriched["ontology"] = {**generated, "original_class": original_class, "parent_class": parent_class, **ontology}
    assessment = assess_detection_threat(
        det_class,
        confidence=enriched.get("confidence", 0),
        allegiance=enriched.get("allegiance"),
    )
    enriched["threat_level"] = assessment["threat_level"]
    enriched["threat_confidence"] = assessment["threat_confidence"]
    enriched["assessment_status"] = assessment["assessment_status"]
    enriched["evidence"] = assessment["evidence"]
    enriched["ontology"]["threat_level"] = assessment["threat_level"]
    enriched["ontology"]["threat_confidence"] = assessment["threat_confidence"]
    enriched["ontology"]["assessment_status"] = assessment["assessment_status"]
    enriched["ontology"]["evidence"] = assessment["evidence"]
    enriched.setdefault("allegiance", "unknown")
    return enriched


def safe_excerpt(value: Optional[str], limit: int = 12000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def read_document_text(path: str, limit: int = 12000) -> str:
    suffix = Path(path).suffix.lower()
    if suffix not in {".txt", ".csv", ".json", ".md", ".log"}:
        return ""
    try:
        return safe_excerpt(Path(path).read_text(encoding="utf-8", errors="ignore"), limit)
    except Exception:
        return ""


def sanitize_ontology_label(value: Optional[str], fallback: str = "Entity") -> str:
    label = re.sub(r"\s+", " ", str(value or "")).strip()
    label = re.sub(r"[^A-Za-z0-9 ._:/()#-]+", "", label)
    return (label or fallback)[:120]


def sanitize_entity_type(value: Optional[str]) -> str:
    label = re.sub(r"[^A-Za-z0-9_ -]+", "", str(value or "Entity")).strip().replace("-", " ")
    label = "".join(part.capitalize() for part in label.split()) or "Entity"
    return label[:60]


def sanitize_relationship_type(value: Optional[str]) -> str:
    rel = re.sub(r"[^A-Za-z0-9_ ]+", "", str(value or "related_to")).strip().upper().replace(" ", "_")
    if not rel:
        rel = "RELATED_TO"
    if not rel.startswith("CANDIDATE_"):
        rel = f"CANDIDATE_{rel}"
    return rel[:80]


def ontology_context_snapshot(limit: int = 24) -> dict:
    context: dict = {"detections": [], "graph": {"nodes": [], "relationships": []}}
    try:
        with postgis_db.get_cursor() as cursor:
            cursor.execute("""
                SELECT d.id, d.class, d.confidence,
                       ST_Y(d.centroid) AS latitude,
                       ST_X(d.centroid) AS longitude,
                       d.metadata,
                       sp.name AS imagery_name,
                       sp.acquisition_time
                FROM detections d
                LEFT JOIN satellite_passes sp ON d.pass_id = sp.id
                WHERE d.deleted_at IS NULL
                ORDER BY d.created_at DESC
                LIMIT %s
            """, (limit,))
            context["detections"] = [dict(row) for row in cursor.fetchall()]
    except Exception:
        context["detections"] = []

    try:
        with db.get_session() as session:
            node_rows = session.run("""
                MATCH (n)
                RETURN elementId(n) AS id, labels(n) AS labels,
                       coalesce(n.name, n.label, n.id, n.class, elementId(n)) AS label,
                       properties(n) AS properties
                LIMIT $limit
            """, {"limit": limit})
            context["graph"]["nodes"] = [dict(record) for record in node_rows]
            rel_rows = session.run("""
                MATCH (a)-[r]->(b)
                RETURN coalesce(a.name, a.label, a.id, a.class, elementId(a)) AS source,
                       type(r) AS type,
                       coalesce(b.name, b.label, b.id, b.class, elementId(b)) AS target
                LIMIT $limit
            """, {"limit": limit})
            context["graph"]["relationships"] = [dict(record) for record in rel_rows]
    except Exception:
        context["graph"] = {"nodes": [], "relationships": []}
    return context


def insert_ontology_update(
    source_type: str,
    source_id: Optional[str],
    domain: str,
    status: str,
    summary: str,
    entities: list[dict],
    relationships: list[dict],
    context: dict,
    error: Optional[str] = None,
) -> dict:
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO ontology_updates
                (source_type, source_id, domain, status, summary, proposed_entities,
                 proposed_relationships, context, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, source_type, source_id, domain, status, summary,
                      proposed_entities, proposed_relationships, context, error,
                      created_at, updated_at
        """, (
            source_type,
            source_id,
            normalize_domain(domain),
            status,
            summary,
            json.dumps(entities, default=str),
            json.dumps(relationships, default=str),
            json.dumps(context, default=str),
            error,
        ))
        return dict(cursor.fetchone())


def persist_ontology_update_to_graph(update: dict) -> None:
    entities = update.get("proposed_entities") or []
    relationships = update.get("proposed_relationships") or []
    entity_keys: dict[str, str] = {}
    with db.get_session() as session:
        session.run("""
            MERGE (u:OntologyUpdate {id: $id})
            SET u.source_type = $source_type,
                u.source_id = $source_id,
                u.domain = $domain,
                u.status = $status,
                u.summary = $summary,
                u.created_at = $created_at
        """, {
            "id": str(update["id"]),
            "source_type": update.get("source_type"),
            "source_id": update.get("source_id"),
            "domain": update.get("domain"),
            "status": update.get("status"),
            "summary": update.get("summary") or "",
            "created_at": str(update.get("created_at") or datetime.now(timezone.utc).isoformat()),
        })
        for entity in entities[:40]:
            label = sanitize_ontology_label(entity.get("label"))
            entity_type = sanitize_entity_type(entity.get("type"))
            key = f"{entity_type}:{label}".lower()
            entity_keys[label.lower()] = key
            confidence = float(entity.get("confidence") or 0)
            session.run("""
                MATCH (u:OntologyUpdate {id: $update_id})
                MERGE (c:OntologyCandidate {key: $key})
                SET c.label = $label,
                    c.entity_type = $entity_type,
                    c.description = $description,
                    c.confidence = $confidence,
                    c.status = 'pending_review',
                    c.updated_at = datetime()
                MERGE (u)-[:PROPOSES]->(c)
            """, {
                "update_id": str(update["id"]),
                "key": key,
                "label": label,
                "entity_type": entity_type,
                "description": safe_excerpt(entity.get("description"), 500),
                "confidence": confidence,
            })
            for detection_id in (entity.get("related_detection_ids") or [])[:8]:
                try:
                    det_id = int(detection_id)
                except (TypeError, ValueError):
                    continue
                session.run("""
                    MATCH (c:OntologyCandidate {key: $key})
                    MATCH (d:Detection {postgis_id: $det_id})
                    MERGE (c)-[:SUPPORTED_BY]->(d)
                """, {"key": key, "det_id": det_id})

        for relationship in relationships[:60]:
            source = sanitize_ontology_label(relationship.get("source_label"))
            target = sanitize_ontology_label(relationship.get("target_label"))
            if not source or not target or source == target:
                continue
            rel_type = sanitize_relationship_type(relationship.get("type"))
            source_key = entity_keys.get(source.lower()) or f"entity:{source}".lower()
            target_key = entity_keys.get(target.lower()) or f"entity:{target}".lower()
            session.run("""
                MATCH (u:OntologyUpdate {id: $update_id})
                MERGE (a:OntologyCandidate {key: $source_key})
                SET a.label = coalesce(a.label, $source), a.status = 'pending_review'
                MERGE (b:OntologyCandidate {key: $target_key})
                SET b.label = coalesce(b.label, $target), b.status = 'pending_review'
                MERGE (a)-[r:CANDIDATE_RELATED_TO {source_update_id: $update_id, relation_type: $rel_type}]->(b)
                SET r.confidence = $confidence,
                    r.evidence = $evidence,
                    r.status = 'pending_review',
                    r.updated_at = datetime()
                MERGE (u)-[:PROPOSES]->(a)
                MERGE (u)-[:PROPOSES]->(b)
            """, {
                "update_id": str(update["id"]),
                "source_key": source_key,
                "target_key": target_key,
                "source": source,
                "target": target,
                "rel_type": rel_type,
                "confidence": float(relationship.get("confidence") or 0),
                "evidence": safe_excerpt(relationship.get("evidence"), 500),
            })


def run_ontology_update(source_type: str, source_id: Optional[str], text: str, domain: str = "OSINT") -> dict:
    ensure_platform_tables()
    context = ontology_context_snapshot()
    prompt = json.dumps({
        "task": (
            "Analyze the submitted analyst text or document excerpt and propose reviewable ontology updates. "
            "Use existing detections and ontology context to ground proposals. Do not create approved target assertions."
        ),
        "source": {"type": source_type, "id": source_id, "domain": normalize_domain(domain)},
        "input_text": safe_excerpt(text, 10000),
        "existing_context": context,
        "required_json_schema": {
            "summary": "one concise analyst-facing summary",
            "entities": [
                {
                    "label": "entity name or canonical label",
                    "type": "facility/person/vehicle/vessel/aircraft/organization/location/other",
                    "description": "short description grounded in source or context",
                    "confidence": 0.0,
                    "related_detection_ids": [0],
                }
            ],
            "relationships": [
                {
                    "source_label": "entity label",
                    "target_label": "entity label",
                    "type": "relationship label",
                    "confidence": 0.0,
                    "evidence": "short evidence phrase",
                }
            ],
        },
    }, default=str)
    system = (
        "Return only valid compact JSON. Treat this as intelligence analysis support. "
        "Propose candidates for analyst review only; never mark a relationship as confirmed. "
        "Do not invent coordinates, identities, or hostile intent beyond the provided source and context."
    )
    try:
        llm_data = get_llm_json(prompt, system=system, max_tokens=1300, timeout_seconds=12)
        entities = llm_data.get("entities") if isinstance(llm_data.get("entities"), list) else []
        relationships = llm_data.get("relationships") if isinstance(llm_data.get("relationships"), list) else []
        update = insert_ontology_update(
            source_type,
            source_id,
            domain,
            "pending_review",
            safe_excerpt(llm_data.get("summary") or "Ontology candidates generated for analyst review.", 2000),
            entities[:40],
            relationships[:60],
            context,
        )
        try:
            persist_ontology_update_to_graph(update)
        except Exception as graph_exc:
            update["status"] = "stored_graph_error"
            update["error"] = str(graph_exc)
            with postgis_db.get_cursor(commit=True) as cursor:
                cursor.execute("""
                    UPDATE ontology_updates
                    SET status = 'stored_graph_error', error = %s, updated_at = NOW()
                    WHERE id = %s
                """, (str(graph_exc), update["id"]))
        publish_event("ontology", {"type": "ontology_update_proposed", "update": update})
        record_timeline_event(normalize_domain(domain), "ontology_update_proposed", update.get("summary") or "Ontology update proposed", {"update_id": update["id"], "source_type": source_type})
        return update
    except AIUnavailable as exc:
        return insert_ontology_update(
            source_type,
            source_id,
            domain,
            "unavailable",
            "Ontology update unavailable because the LLM is not configured or did not return usable JSON.",
            [],
            [],
            context,
            str(exc),
        )




# Startup and shutdown handlers are wired via the `lifespan` async
# contextmanager passed to FastAPI() above.


# Health + alerts routes are registered via routers.health.
# Auth routes are registered via routers.auth.

INFERENCE_SAM3_URL = os.getenv("INFERENCE_SAM3_URL", "http://inference-sam3:8001")


# Inference proxy + confidence-overrides + dashboard routes live in routers.inference.

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


# Graph routes (/api/graph, /api/graph/neighborhood, /api/geotime/features)
# live in routers.graph.

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


# Default open-vocabulary prompt set for drone aerial FMV. SAM3's text-prompted
# tracker takes these and reports a track per matching object across frames.
# Override by passing a comma-separated `prompts` form field on upload.
# Precision-first fallback used when the upload does not provide prompts.
# Keeping this small avoids launching one tracking session per ontology object
# across every FMV window.
FMV_FALLBACK_PROMPTS = ["vehicle", "person", "building"]


@app.post("/api/fmv/clips")
def upload_fmv_clip(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    srt: Optional[UploadFile] = File(None),
    prompts: Optional[str] = Form(None),
    prompt_mode: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    # Phase 8.41: when no KLV/GPMD/SRT telemetry is present, the upload fails
    # by default. Tick this on the upload form to fall back to the synthetic
    # Dubai sine-wave fixture for offline demos.
    allow_synthetic_telemetry: bool = Form(False),
):
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

    size = save_upload_file(file, local_path)
    if size == 0:
        local_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded video is empty")

    sidecar_path: Optional[Path] = None
    if srt is not None and srt.filename:
        sidecar_path = clip_dir / safe_filename(srt.filename)
        save_upload_file(srt, sidecar_path)
    else:
        # Some upload UIs ship the .srt as a co-named file in the same form;
        # also auto-detect any .srt that ffmpeg may have written.
        sidecar_path = next(iter(clip_dir.glob("*.srt")), None) or next(iter(clip_dir.glob("*.SRT")), None)

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
        try:
            rows = extract_telemetry(
                local_path,
                clip["id"],
                clip["duration_seconds"],
                clip["fps"],
                sidecar_srt=sidecar_path,
                allow_synthetic=allow_synthetic_telemetry,
            )
        except TelemetryMissingError as exc:
            # Phase 8.41: refuse the upload rather than ship sine-wave Dubai
            # georeference into the analyst's review queue.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        cursor.executemany("""
            INSERT INTO fmv_frames (clip_id, frame_index, timestamp_seconds, telemetry, footprint)
            VALUES (%s, %s, %s, %s, ST_GeomFromText(%s, 4326))
            ON CONFLICT (clip_id, frame_index) DO UPDATE SET
                timestamp_seconds = EXCLUDED.timestamp_seconds,
                telemetry = EXCLUDED.telemetry,
                footprint = EXCLUDED.footprint
        """, rows)

    clip["stream_url"] = fmv_public_url(clip.get("hls_path"), clip["file_path"])

    # Queue SAM3 video tracking. Detections stream back into fmv_detections
    # asynchronously; the frontend subscribes to fmv:{clip_id} and refetches
    # when the worker publishes `fmv_detections_complete`.
    # Prompt resolution: explicit upload field -> small precision-first FMV
    # defaults. The previous ontology-wide fallback fanned out every admin
    # prompt across every window, which was slow and noisy for analyst review.
    mode = (prompt_mode or "pcs").strip().lower()
    if mode not in {"pcs", "amg"}:
        raise HTTPException(status_code=400, detail=f"prompt_mode must be 'pcs' or 'amg', got {prompt_mode!r}")
    model_choice = (model or "sam3").strip().lower()
    if model_choice not in {"sam3", "yolo26"}:
        raise HTTPException(status_code=400, detail=f"model must be 'sam3' or 'yolo26', got {model!r}")
    if model_choice == "sam3" and mode == "amg":
        raise HTTPException(
            status_code=400,
            detail="SAM 3.1 no longer supports AMG; pick model='yolo26' for promptless detection, or use prompt_mode='pcs'",
        )
    if mode == "amg":
        # Promptless path — prompts/ontology defaults are ignored. Worker
        # synthesises a single "_amg" sentinel prompt so the per-window task
        # fan-out yields exactly one inference call per window.
        prompt_list: list[str] = []
    else:
        explicit_prompts = [p.strip() for p in (prompts or "").split(",") if p.strip()]
        if explicit_prompts:
            prompt_list = explicit_prompts
        else:
            prompt_list = list(FMV_FALLBACK_PROMPTS)
    # Map (model, mode) → the worker's prompt_mode token. YOLO 26 collapses
    # both AMG and PCS onto a single "yoloe" worker mode; the empty vs
    # non-empty prompt_list selects -pf vs -seg in the inference service.
    if model_choice == "yolo26":
        worker_mode = "yoloe"
    else:
        worker_mode = mode
    task_id: Optional[str] = None
    try:
        task = process_fmv.delay(clip["id"], str(local_path), prompt_list,
                                 None, None, worker_mode)
        clip["task_id"] = task.id
        clip["status"] = "queued"
        clip["prompt_mode"] = mode  # UI-level mode preserved
        clip["model"] = model_choice
        task_id = task.id
    except Exception as exc:
        logger.warning("Failed to queue process_fmv for clip %s: %s", clip["id"], exc)

    # Mirror the imagery path: record the clip as an upload_job so it shows up
    # in the global StatusBar footer and the Admin → Processing tab without
    # needing a separate FMV-jobs feed.
    try:
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute("""
                INSERT INTO upload_jobs (upload_id, filename, file_path, media_type, handler, status, celery_task_id, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (upload_id) DO NOTHING
            """, (
                upload_id,
                filename,
                str(local_path),
                "fmv",
                "fmv_video",
                clip["status"],
                task_id,
                json.dumps({
                    "clip_id": clip["id"],
                    "duration_seconds": clip.get("duration_seconds"),
                    "fps": clip.get("fps"),
                    "model": model_choice,
                    "prompt_mode": mode,
                    "bytes": size,
                    "stage": clip["status"],
                    "progress": 5 if clip["status"] == "queued" else 0,
                    "message": "FMV clip received and tracking queued."
                        if clip["status"] == "queued"
                        else "FMV clip stored.",
                }),
            ))
    except Exception as exc:
        logger.warning("Failed to record upload_job for FMV clip %s: %s", clip["id"], exc)

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
                WHERE clip_id = %s AND deleted_at IS NULL
                ORDER BY frame_index, confidence DESC
            """, (clip_id,))
        else:
            cursor.execute("""
                SELECT id, clip_id, frame_index, class, confidence, bbox, metadata, created_at
                FROM fmv_detections
                WHERE clip_id = %s AND frame_index = %s AND deleted_at IS NULL
                ORDER BY confidence DESC
            """, (clip_id, frame_index))
        return {"detections": [dict(row) for row in cursor.fetchall()]}


# Analytics, models, and training routes live in routers.analytics + routers.models_training.

# Imagery + basemap routes live in routers.imagery.

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
        WHERE d.deleted_at IS NULL
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
            item_metadata = dict(item.get("metadata") or {})
            item_metadata["confidence"] = item.get("confidence")
            item["metadata"] = enriched_detection_metadata(item["class"], item_metadata)
            detections.append(item)
        return {"detections": detections}


@app.get("/api/detections/classes")
def get_detection_classes(
    bbox: Optional[str] = Query(None, description="min_lon,min_lat,max_lon,max_lat"),
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    llm: bool = Query(False, description="Generate class labels/descriptions with the configured LLM")
):
    """Return detected classes as map/globe filter metadata with ontology and threat rollups."""
    query = """
        WITH filtered AS (
            SELECT d.class,
                   d.confidence,
                   d.metadata,
                   coalesce(d.metadata->>'parent_class', d.class) AS parent_class,
                   coalesce(d.metadata->>'review_status', 'review_candidate') AS review_status,
                   coalesce(d.metadata->>'allegiance', 'unknown') AS allegiance
            FROM detections d
            JOIN satellite_passes sp ON d.pass_id = sp.id
            WHERE d.deleted_at IS NULL
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
                   parent_class,
                   count(*) AS count,
                   max(confidence) AS max_confidence,
                   avg(confidence) AS avg_confidence,
                   mode() WITHIN GROUP (ORDER BY filtered.metadata->>'branch_id') AS branch_id,
                   mode() WITHIN GROUP (ORDER BY filtered.metadata->>'icon_key') AS icon_key
            FROM filtered
            GROUP BY class, parent_class
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
        ),
        -- Phase 7.33: expose the full branch breakdown per class so minority
        -- branches (e.g. an 'aircraft' class that maps to both
        -- Civilian_Aviation and Military_Forces) aren't silently hidden by
        -- the mode() reduction above.
        branch_counts AS (
            SELECT class,
                   coalesce(filtered.metadata->>'branch_id', 'Other') AS branch_id,
                   coalesce(filtered.metadata->>'icon_key', 'circle_help') AS icon_key,
                   count(*) AS count
            FROM filtered
            GROUP BY class, branch_id, icon_key
        ),
        branch_json AS (
            SELECT class,
                   jsonb_agg(
                     jsonb_build_object(
                       'branch_id', branch_id,
                       'icon_key', icon_key,
                       'count', count
                     )
                     ORDER BY count DESC
                   ) AS branch_breakdown
            FROM branch_counts
            GROUP BY class
        )
        SELECT c.class,
               c.parent_class,
               c.count,
               c.max_confidence,
               c.avg_confidence,
               c.branch_id,
               c.icon_key,
               coalesce(a.allegiance_counts, '{}'::jsonb) AS allegiance_counts,
               coalesce(b.branch_breakdown, '[]'::jsonb) AS branch_breakdown
        FROM class_counts c
        LEFT JOIN allegiance_json a ON a.class = c.class
        LEFT JOIN branch_json b ON b.class = c.class
        ORDER BY c.count DESC, c.class ASC
    """
    with postgis_db.get_cursor() as cursor:
        cursor.execute(query, params)
        classes = []
        for index, row in enumerate(cursor.fetchall()):
            # Phase 6.23: the deterministic ontology is the AUTHORITATIVE
            # value the analyst sees as the class label/category/threat. The
            # LLM refinement (when requested) is captured as a separate
            # `llm_advisory` field so the UI can render it as an "AI
            # suggestion" pill instead of silently overwriting the model's
            # raw class with a hallucinated refinement.
            ontology = conservative_detection_ontology(row["class"], confidence=float(row["avg_confidence"] or 0))
            classification_status = "unavailable"
            llm_advisory = None
            if llm and index < 8:
                try:
                    advisory = llm_detection_ontology(
                        row["class"],
                        count=int(row["count"] or 0),
                        avg_confidence=float(row["avg_confidence"] or 0),
                    )
                    classification_status = "ok"
                    # Surface only the non-authoritative fields. The LLM is
                    # explicitly NOT trusted for category or threat_level —
                    # those come from deterministic rules in ontology /
                    # threat_assessment, which use the raw class.
                    llm_advisory = {
                        "label": advisory.get("label"),
                        "description": advisory.get("description"),
                        "recommended_filter": advisory.get("recommended_filter"),
                        "generated_by": advisory.get("generated_by"),
                    }
                except AIUnavailable:
                    classification_status = "unavailable"
            classes.append({
                "class": row["class"],
                "parent_class": row["parent_class"],
                "label": ontology["label"],
                "count": row["count"],
                "max_confidence": float(row["max_confidence"] or 0),
                "avg_confidence": float(row["avg_confidence"] or 0),
                "ontology": ontology,
                "threat_level": ontology["threat_level"],
                "allegiance_counts": row["allegiance_counts"] or {},
                "classification_status": classification_status,
                "branch_id": row["branch_id"] or "Other",
                "icon_key": row["icon_key"] or "circle_help",
                "branch_breakdown": row["branch_breakdown"] or [],
                "llm_advisory": llm_advisory,
            })
        return {"classes": classes, "classification_status": "ok" if llm and any(item["classification_status"] == "ok" for item in classes) else "unavailable" if llm else "heuristic"}

def _encode_detection_cursor(created_at: datetime, detection_id: int) -> str:
    raw_cursor = json.dumps(
        [created_at.isoformat(), int(detection_id)],
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw_cursor).decode("ascii").rstrip("=")


def _decode_detection_cursor(cursor: str) -> tuple[str, int]:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii") + b"===")
    cursor_created_at, cursor_id = json.loads(raw.decode("utf-8"))
    return str(cursor_created_at), int(cursor_id)


@app.get("/api/detections/geojson")
def get_detections_geojson(
    bbox: Optional[str] = Query(None, description="min_lon,min_lat,max_lon,max_lat"),
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    det_class: Optional[str] = None,
    limit: int = Query(20000, ge=1, le=50000),
    cursor: Optional[str] = Query(None, description="Opaque cursor from the previous page's next_cursor"),
):
    """Return detections as GeoJSON FeatureCollection.

    Phase 7.32: cursor pagination. Pages are ordered by ``(created_at DESC, id
    DESC)`` and the cursor encodes both values so ids inserted out-of-order do
    not create gaps.
    """
    with postgis_db.get_cursor() as db_cursor:
        query = """
            SELECT d.id, d.class, d.confidence, d.pass_id, d.created_at, d.metadata,
                   sp.name AS pass_name, sp.acquisition_time, sp.metadata AS imagery_metadata,
                   ST_AsGeoJSON(d.geom)::jsonb AS geometry
            FROM detections d
            JOIN satellite_passes sp ON d.pass_id = sp.id
            WHERE d.deleted_at IS NULL AND ST_Intersects(d.geom, sp.footprint)
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
        if cursor is not None:
            try:
                cursor_created_at, cursor_id = _decode_detection_cursor(cursor)
            except Exception as exc:
                raise HTTPException(status_code=400, detail="invalid detection cursor") from exc
            query += " AND (d.created_at, d.id) < (%s, %s)"
            params.extend([cursor_created_at, int(cursor_id)])
        # Fetch one extra row so we can tell the client whether more remain.
        query += " ORDER BY d.created_at DESC, d.id DESC LIMIT %s"
        params.append(limit + 1)
        db_cursor.execute(query, params)
        rows = db_cursor.fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_more and rows:
            next_cursor = _encode_detection_cursor(rows[-1]["created_at"], rows[-1]["id"])
        features = []
        for row in rows:
            raw_metadata = dict(row["metadata"] or {})
            raw_metadata["confidence"] = row["confidence"]
            metadata = enriched_detection_metadata(row["class"], raw_metadata)
            features.append({
                "type": "Feature",
                "geometry": row["geometry"],
                "properties": {
                    "id": row["id"],
                    "class": row["class"],
                    "label": metadata["ontology"]["label"],
                    "confidence": row["confidence"],
                    "calibrated_confidence": metadata.get("calibrated_confidence", row["confidence"]),
                    # Phase 7.36: surface the pre-calibration score + the per-
                    # model temperature so the provenance panel can show the
                    # full "raw → calibrated" story.
                    "raw_confidence": metadata.get("raw_confidence"),
                    "model_temperature": metadata.get("model_temperature"),
                    "original_class": metadata.get("original_class", row["class"]),
                    "parent_class": metadata.get("parent_class", row["class"]),
                    "review_status": metadata.get("review_status", "review_candidate"),
                    "threshold_profile": metadata.get("threshold_profile"),
                    "class_threshold": metadata.get("class_threshold"),
                    "model_version": metadata.get("model_version"),
                    "taxonomy_version": metadata.get("taxonomy_version"),
                    "chip_id": metadata.get("chip_id"),
                    "coverage_fraction": metadata.get("coverage_fraction"),
                    # Phase 3.13: chip-sampling transparency — when the
                    # planner sub-samples a large raster (>MAX_INFERENCE_CHIPS),
                    # the analyst should see that this AOI is not fully
                    # covered. These three fields ride alongside every
                    # detection so the UI can surface the gap.
                    "planned_chips": metadata.get("planned_chips"),
                    "source_total_chips": metadata.get("source_total_chips"),
                    "sampling_enabled": metadata.get("sampling_enabled"),
                    "pass_id": row["pass_id"],
                    "pass_name": row["pass_name"],
                    "acquisition_time": row["acquisition_time"],
                    "imagery_metadata": row["imagery_metadata"] or {},
                    "created_at": row["created_at"],
                    "metadata": metadata,
                    "ontology": metadata["ontology"],
                    "threat_level": metadata.get("threat_level"),
                    "threat_confidence": metadata.get("threat_confidence"),
                    "assessment_status": metadata.get("assessment_status"),
                    "evidence": metadata.get("evidence", []),
                    "allegiance": metadata.get("allegiance", "unknown"),
                    "branch_id": metadata.get("branch_id") or "Other",
                    "icon_key": metadata.get("icon_key") or "circle_help",
                    "canonical_label": metadata.get("canonical_label"),
                    "was_unknown": bool(metadata.get("was_unknown")),
                    "ontology_object_id": metadata.get("ontology_object_id"),
                    "position_uncertainty_m": metadata.get("position_uncertainty_m"),
                    "position_uncertainty_ellipse": metadata.get("position_uncertainty_ellipse"),
                    "scale_pass": metadata.get("scale_pass"),
                },
            })
        return {
            "type": "FeatureCollection",
            "features": features,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }


@app.patch("/api/detections/{detection_id}/tag")
def tag_detection(detection_id: int, update: DetectionTagUpdate, user: SessionUser = Depends(get_current_user)):
    allegiance = update.allegiance.strip().lower()
    if allegiance not in {"friendly", "hostile", "neutral", "unknown"}:
        raise HTTPException(status_code=400, detail="allegiance must be friendly, hostile, neutral, or unknown")
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("SELECT class, confidence, metadata FROM detections WHERE id = %s", (detection_id,))
        existing = cursor.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Detection not found")
        assessment = assess_detection_threat(existing["class"], confidence=existing["confidence"], allegiance=allegiance)
        ontology = conservative_detection_ontology(existing["class"], confidence=existing["confidence"], allegiance=allegiance)
        cursor.execute("""
            UPDATE detections
            SET metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb
            WHERE id = %s
            RETURNING id, class, metadata
        """, (json.dumps({
            "allegiance": allegiance,
            "threat_level": assessment["threat_level"],
            "threat_confidence": assessment["threat_confidence"],
            "assessment_status": "analyst_override" if allegiance == "hostile" else assessment["assessment_status"],
            "evidence": assessment["evidence"],
            "ontology": ontology,
        }), detection_id))
        row = cursor.fetchone()
    try:
        with db.get_session() as session:
            session.run("""
                MATCH (d:Detection {postgis_id: $det_id})
                SET d.allegiance = $allegiance,
                    d.threat_level = $threat_level,
                    d.threat_confidence = $threat_confidence,
                    d.assessment_status = $assessment_status
            """, {
                "det_id": detection_id,
                "allegiance": allegiance,
                "threat_level": assessment["threat_level"],
                "threat_confidence": assessment["threat_confidence"],
                "assessment_status": "analyst_override" if allegiance == "hostile" else assessment["assessment_status"],
            })
    except Exception:
        logger.warning("Failed to mirror detection tag to Neo4j detection_id=%s", detection_id, exc_info=True)
    publish_event("detections", {"type": "detection_tagged", "id": detection_id, "allegiance": allegiance})
    return {"id": row["id"], "class": row["class"], "metadata": enriched_detection_metadata(row["class"], row["metadata"])}


# ============================================================================
# Object details (operator-edited metadata) — helpers hoisted to
# detection_helpers.py so routers/{detections,fmv}.py can share them.
# Re-exported here so any older import path in this file still resolves.
# ============================================================================

from detection_helpers import (
    AFFILIATIONS,
    THREAT_LEVELS,
    _normalize_affiliation,
    _normalize_threat,
    _read_object_details,
    _upsert_object_details,
)


# ============================================================================
# Detection / FMV detail + draw + delete endpoints live in
# routers/detections.py and routers/fmv.py.
# Round 2 endpoints — Map+, FMV+, Admin advanced
# ============================================================================


REVIEW_STATUSES = {"pending", "accepted", "flagged", "rejected", "review_candidate", "high_confidence"}


@app.patch("/api/detections/{detection_id}/review")
def patch_detection_review(detection_id: int, body: ReviewUpdate, user: SessionUser = Depends(get_current_user)):
    """Set the operator review status on a detection. Stored in
    ``detections.metadata.review_status`` so existing GeoJSON / queue queries
    pick it up without a schema change."""
    status = body.status.strip().lower()
    if status not in REVIEW_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(REVIEW_STATUSES)}")
    patch: dict = {"review_status": status, "reviewed_by": user.username}
    if body.note:
        patch["review_note"] = body.note
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE detections
            SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
            WHERE id = %s AND deleted_at IS NULL
            RETURNING id, class, metadata
            """,
            (json.dumps(patch), detection_id),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="detection not found")
    publish_event("detections", {"type": "detection_review_updated", "id": detection_id, "status": status, "by": user.username})
    return {"id": row["id"], "class": row["class"], "review_status": status, "metadata": row["metadata"]}


@app.get("/api/detections/queue")
def get_review_queue(
    status: str = Query("pending", description="review status to filter"),
    limit: int = Query(50, ge=1, le=500),
    user: SessionUser = Depends(get_current_user),
):
    """Review queue for the Map+ Review tab. Filters by ``metadata.review_status``."""
    if status.lower() == "pending":
        # Pending = explicit "pending" plus the historical default values when
        # the operator hasn't touched the row yet.
        where_review = "(coalesce(d.metadata->>'review_status', 'review_candidate') IN ('pending','review_candidate'))"
        params: list = [limit]
    else:
        where_review = "(coalesce(d.metadata->>'review_status', 'review_candidate') = %s)"
        params = [status.lower(), limit]
    with postgis_db.get_cursor() as cur:
        cur.execute(
            f"""
            SELECT d.id, d.class, d.confidence, d.metadata,
                   ST_AsGeoJSON(d.geom)::jsonb AS geometry,
                   ST_Y(d.centroid) AS lat, ST_X(d.centroid) AS lon,
                   sp.acquisition_time, sp.name AS pass_name
            FROM detections d
            LEFT JOIN satellite_passes sp ON sp.id = d.pass_id
            WHERE d.deleted_at IS NULL AND {where_review}
            ORDER BY d.created_at DESC
            LIMIT %s
            """,
            params,
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"status": status.lower(), "count": len(rows), "detections": rows}


def _cosine(a: list, b: list) -> float:
    import math as _m
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        try:
            x = float(x); y = float(y)
        except (TypeError, ValueError):
            return 0.0
        dot += x * y
        na += x * x
        nb += y * y
    denom = _m.sqrt(na) * _m.sqrt(nb)
    return dot / denom if denom > 0 else 0.0


def _detection_embedding(meta: dict | None) -> list | None:
    if not isinstance(meta, dict):
        return None
    emb = meta.get("embedding")
    if isinstance(emb, list) and emb:
        return emb
    emb = meta.get("terramind_embedding")
    if isinstance(emb, list) and emb:
        return emb
    return None


@app.get("/api/detections/{detection_id}/similar")
def get_similar_detections(detection_id: int, k: int = Query(12, ge=1, le=50), user: SessionUser = Depends(get_current_user)):
    """Return the k cosine-similar detections by DINOv3 embedding."""
    with postgis_db.get_cursor() as cur:
        cur.execute(
            "SELECT id, class, confidence, metadata, ST_Y(centroid) AS lat, ST_X(centroid) AS lon "
            "FROM detections WHERE id = %s AND deleted_at IS NULL",
            (detection_id,),
        )
        anchor = cur.fetchone()
        if not anchor:
            raise HTTPException(status_code=404, detail="detection not found")
        anchor_emb = _detection_embedding(dict(anchor).get("metadata"))
        if not anchor_emb:
            return {"detection_id": detection_id, "method": "embedding", "results": [], "reason": "no embedding stored on anchor"}

        cur.execute(
            "SELECT id, class, confidence, metadata, ST_Y(centroid) AS lat, ST_X(centroid) AS lon "
            "FROM detections "
            "WHERE id <> %s AND deleted_at IS NULL AND metadata ? 'embedding' "
            "ORDER BY created_at DESC "
            "LIMIT 2000",
            (detection_id,),
        )
        candidates = [dict(r) for r in cur.fetchall()]

    scored: list[dict] = []
    for c in candidates:
        emb = _detection_embedding(c.get("metadata"))
        if not emb:
            continue
        sim = _cosine(anchor_emb, emb)
        if sim <= 0:
            continue
        scored.append({**c, "similarity": sim})
    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return {"detection_id": detection_id, "method": "embedding", "results": scored[:k]}


@app.get("/api/fmv/detections/{detection_id}/similar")
def get_similar_fmv_detections(detection_id: int, k: int = Query(12, ge=1, le=50), user: SessionUser = Depends(get_current_user)):
    """LVD-side cosine similarity for FMV (Re-ID cluster)."""
    with postgis_db.get_cursor() as cur:
        cur.execute(
            "SELECT id, clip_id, class, confidence, metadata FROM fmv_detections WHERE id = %s AND deleted_at IS NULL",
            (detection_id,),
        )
        anchor = cur.fetchone()
        if not anchor:
            raise HTTPException(status_code=404, detail="fmv detection not found")
        anchor_meta = dict(anchor).get("metadata") or {}
        anchor_emb = _detection_embedding(anchor_meta)
        if not anchor_emb:
            return {"detection_id": detection_id, "method": "embedding", "results": [], "reason": "no embedding stored"}
        clip_id = dict(anchor).get("clip_id")

        cur.execute(
            "SELECT id, clip_id, frame_index, class, confidence, metadata "
            "FROM fmv_detections "
            "WHERE id <> %s AND deleted_at IS NULL AND metadata ? 'embedding' "
            "ORDER BY created_at DESC "
            "LIMIT 4000",
            (detection_id,),
        )
        candidates = [dict(r) for r in cur.fetchall()]
    scored: list[dict] = []
    for c in candidates:
        emb = _detection_embedding(c.get("metadata"))
        if not emb:
            continue
        sim = _cosine(anchor_emb, emb)
        if sim <= 0:
            continue
        scored.append({**c, "similarity": sim, "track_id": (c.get("metadata") or {}).get("track_id")})
    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return {
        "detection_id": detection_id,
        "clip_id": clip_id,
        "method": "lvd",
        "results": scored[:k],
    }


# --- Prompt profiles --------------------------------------------------------


# --- Taxonomy version history ----------------------------------------------


# --- Prithvi overlays -------------------------------------------------------


PRITHVI_KINDS = {"flood", "burn", "burn_scar", "crops", "crop"}


@app.get("/api/detections/prithvi-overlays")
def get_prithvi_overlays(
    kind: str = Query(..., description="flood | burn | crops"),
    bbox: Optional[str] = None,
    limit: int = Query(500, ge=1, le=5000),
    user: SessionUser = Depends(get_current_user),
):
    """GeoJSON FeatureCollection of detections tagged with the requested
    Prithvi label. Source: ``metadata.prithvi_labels`` (a list of label keys
    emitted by the imagery worker)."""
    norm = kind.strip().lower()
    if norm not in PRITHVI_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {sorted(PRITHVI_KINDS)}")
    label_filter = "burn" if norm in {"burn", "burn_scar"} else ("crop" if norm in {"crop", "crops"} else "flood")
    params: list = [label_filter]
    where = "metadata->'prithvi_labels' ? %s AND d.deleted_at IS NULL"
    if bbox:
        min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
        where += " AND ST_Intersects(d.geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))"
        params.extend([min_lon, min_lat, max_lon, max_lat])
    params.append(limit)
    with postgis_db.get_cursor() as cur:
        cur.execute(
            f"""
            SELECT d.id, d.class, d.metadata,
                   ST_AsGeoJSON(d.geom)::jsonb AS geometry
            FROM detections d
            WHERE {where}
            ORDER BY d.created_at DESC
            LIMIT %s
            """,
            params,
        )
        rows = [dict(r) for r in cur.fetchall()]
    features = [
        {
            "type": "Feature",
            "geometry": r["geometry"],
            "properties": {
                "id": r["id"],
                "class": r["class"],
                "prithvi_labels": (r.get("metadata") or {}).get("prithvi_labels", []),
                "kind": norm,
            },
        }
        for r in rows
    ]
    return {"type": "FeatureCollection", "kind": norm, "count": len(features), "features": features}

def _target_history_anchor(target_id: str) -> float:
    """Phase 4.14 (history_anchor term).

    Returns ``[0.0, 1.0]`` based on the number of *accepted* prior
    candidate links for this target. The more often analysts have
    confirmed this target, the higher the prior on it being correctly
    matched again. Saturates at 5 accepted links to avoid stale targets
    dominating; ignores rejected/flagged links.
    """
    try:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                "SELECT count(*) AS c FROM detection_target_candidates "
                "WHERE target_id = %s AND status IN ('accepted', 'confirmed')",
                (target_id,),
            )
            row = cursor.fetchone()
    except Exception:
        return 0.0
    if not row:
        return 0.0
    accepted = int(row["c"] if isinstance(row, dict) else row[0])
    return min(1.0, accepted / 5.0)


def detection_row_for_candidate(detection_id: int) -> dict:
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT d.id, d.class, d.confidence, d.metadata,
                   ST_X(d.centroid) AS lon, ST_Y(d.centroid) AS lat,
                   sp.acquisition_time, sp.name AS pass_name
            FROM detections d
            JOIN satellite_passes sp ON sp.id = d.pass_id
            WHERE d.id = %s AND d.deleted_at IS NULL
        """, (detection_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Detection not found")
        return dict(row)


def generate_candidate_links_for_detection(
    detection_id: int,
    max_distance_m: float = 1500.0,
    max_candidates_per_detection: int = 5,
) -> list[dict]:
    """Phase 4.14/4.15: rebalanced score + top-N truncation.

    Old score: ``0.45·distance + ≤0.35·compat + 0.20·confidence`` — proximity
    dominated. A 0.1-confidence detection at zero distance beat a
    0.9-confidence detection at 500 m, generating false target associations.

    New score: ``0.30·distance + 0.30·compat + 0.30·confidence + 0.10·history``
    where ``history`` boosts targets the analyst has previously confirmed,
    so re-sightings of known targets rank above spurious one-off matches.

    Phase 4.15: keep only the top ``max_candidates_per_detection`` links per
    detection (default 5) rather than emitting every target within
    ``max_distance_m`` — this prevents a crowded AOI from generating
    hundreds of low-quality links per detection.
    """
    ensure_platform_tables()
    detection = detection_row_for_candidate(detection_id)
    try:
        with db.get_session() as session:
            result = session.run("""
                MATCH (t:Target)
                WHERE t.latitude IS NOT NULL AND t.longitude IS NOT NULL
                RETURN elementId(t) AS element_id, t.id AS stable_id, t.name AS name,
                       t.latitude AS lat, t.longitude AS lon, properties(t) AS props
            """)
            targets = [dict(record) for record in result]
    except Exception:
        targets = []

    top = rank_candidate_links(
        detection,
        targets,
        max_distance_m=max_distance_m,
        max_candidates_per_detection=max_candidates_per_detection,
        history_lookup=_target_history_anchor,
    )

    candidates = []
    with postgis_db.get_cursor(commit=True) as cursor:
        for item in top:
            evidence = {
                "distance_m": round(item["distance_m"], 2),
                "compatibility_reason": item["compatibility_reason"],
                "compatibility_score": round(item["compatibility_score"], 3),
                "history_anchor": round(item["history_anchor"], 3),
                "score_weights": item["score_weights"],
                "detection_class": detection["class"],
                "detection_confidence": item["detection_confidence"],
                "acquisition_time": detection.get("acquisition_time"),
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
                RETURNING id, detection_id, target_id, target_name, score, reason, status, evidence, reviewed_by, reviewed_at, created_at, updated_at
            """, (
                detection_id, item["target_id"], item["target_name"],
                item["score"], item["reason"], json.dumps(evidence, default=str),
            ))
            candidates.append(dict(cursor.fetchone()))
    publish_event("detections", {"type": "candidate_links_updated", "detection_id": detection_id, "count": len(candidates)})
    return candidates


@app.get("/api/detections/{detection_id}/candidate-links")
def list_detection_candidate_links(detection_id: int):
    ensure_platform_tables()
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT id, detection_id, target_id, target_name, score, reason, status, evidence, reviewed_by, reviewed_at, created_at, updated_at
            FROM detection_target_candidates
            WHERE detection_id = %s
            ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, score DESC, updated_at DESC
        """, (detection_id,))
        return {"candidates": [dict(row) for row in cursor.fetchall()]}


@app.post("/api/detections/{detection_id}/candidate-links")
def create_detection_candidate_links(detection_id: int):
    candidates = generate_candidate_links_for_detection(detection_id)
    return {"success": True, "candidates": candidates}


@app.post("/api/detection-target-candidates/{candidate_id}/approve")
def approve_detection_target_candidate(candidate_id: int, req: CandidateLinkDecision = CandidateLinkDecision()):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            SELECT c.id, c.detection_id, c.target_id, c.target_name,
                   d.class, d.confidence, ST_X(d.centroid) AS lon, ST_Y(d.centroid) AS lat
            FROM detection_target_candidates c
            JOIN detections d ON d.id = c.detection_id
            WHERE c.id = %s
        """, (candidate_id,))
        candidate = cursor.fetchone()
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate link not found")
        candidate = dict(candidate)
        cursor.execute("""
            UPDATE detection_target_candidates
            SET status = 'approved', reviewed_by = %s, reviewed_at = NOW(), updated_at = NOW()
            WHERE id = %s
            RETURNING id, detection_id, target_id, target_name, score, reason, status, evidence, reviewed_by, reviewed_at, created_at, updated_at
        """, (req.analyst or "analyst", candidate_id))
        updated = dict(cursor.fetchone())

    with db.get_session() as session:
        result = session.run("""
            MATCH (t:Target)
            WHERE elementId(t) = $target_id OR t.id = $target_id
            MERGE (d:Detection {postgis_id: $det_id})
            ON CREATE SET d.class = $det_class,
                          d.confidence = $confidence,
                          d.latitude = $lat,
                          d.longitude = $lon,
                          d.created_at = datetime()
            SET d.class = $det_class,
                d.confidence = $confidence,
                d.latitude = $lat,
                d.longitude = $lon
            MERGE (t)-[rel:DETECTED_AS]->(d)
            ON CREATE SET rel.created_at = datetime()
            SET rel.status = 'approved',
                rel.reviewed_by = $reviewed_by,
                rel.reviewed_at = datetime()
            RETURN t, d
        """, {
            "target_id": candidate["target_id"],
            "det_id": candidate["detection_id"],
            "det_class": candidate["class"],
            "confidence": candidate["confidence"],
            "lat": candidate["lat"],
            "lon": candidate["lon"],
            "reviewed_by": req.analyst or "analyst",
        })
        if not result.single():
            raise HTTPException(status_code=409, detail="Approved candidate target could not be found in graph")

    publish_event("detections", {"type": "candidate_link_approved", "candidate": updated})
    return {"success": True, "candidate": updated}


@app.post("/api/detection-target-candidates/{candidate_id}/reject")
def reject_detection_target_candidate(candidate_id: int, req: CandidateLinkDecision = CandidateLinkDecision()):
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            UPDATE detection_target_candidates
            SET status = 'rejected', reviewed_by = %s, reviewed_at = NOW(), updated_at = NOW()
            WHERE id = %s
            RETURNING id, detection_id, target_id, target_name, score, reason, status, evidence, reviewed_by, reviewed_at, created_at, updated_at
        """, (req.analyst or "analyst", candidate_id))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Candidate link not found")
        candidate = dict(row)
    publish_event("detections", {"type": "candidate_link_rejected", "candidate": candidate})
    return {"success": True, "candidate": candidate}


@app.post("/api/detections/resolve")
def resolve_detection(detection_id: int, distance_threshold_meters: float = 500.0):
    """Compatibility endpoint: generate reviewable candidates, never graph links."""
    candidates = generate_candidate_links_for_detection(detection_id, max_distance_m=distance_threshold_meters)
    return {
        "resolved": False,
        "action": "candidate_links_created",
        "requires_review": True,
        "candidates": candidates,
    }

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


# AI + action-proposal routes live in routers.ai.

# WebSocket bridge lives in routers.ws.
# ---------------------------------------------------------------------------
# Detection Tracks API
# ---------------------------------------------------------------------------

def _dt_iso(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return v.isoformat()


@app.get("/api/tracks/detections")
def list_detection_tracks(
    bbox: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    status: str = "confirmed,coast,pinned",
    category: Optional[str] = None,
    min_obs: int = 1,
    limit: int = 200,
):
    limit = min(limit, 500)
    status_list = [s.strip() for s in status.split(",") if s.strip()]

    start_dt = datetime.fromisoformat(start_time) if start_time else None
    end_dt = datetime.fromisoformat(end_time) if end_time else None

    bbox_parts = None
    if bbox:
        try:
            bbox_parts = [float(x) for x in bbox.split(",")]
            if len(bbox_parts) != 4:
                bbox_parts = None
        except ValueError:
            bbox_parts = None

    sql = """
        SELECT
            dt.id, dt.track_uid, dt.primary_class, dt.category, dt.threat_level,
            dt.status, dt.pinned, dt.obs_count, dt.miss_count,
            dt.first_seen, dt.last_seen,
            ST_X(dt.last_centroid) AS lon, ST_Y(dt.last_centroid) AS lat,
            dt.last_velocity, dt.metadata,
            ST_AsGeoJSON(dt.path)::text AS path_geojson,
            json_agg(
                json_build_object(
                    'lat', ST_Y(m.centroid),
                    'lng', ST_X(m.centroid),
                    'time', m.observed_at,
                    'detection_id', m.detection_id,
                    'seq_index', m.seq_index,
                    'cost', m.cost
                ) ORDER BY m.observed_at
            ) AS history
        FROM detection_tracks dt
        LEFT JOIN detection_track_members m ON m.track_id = dt.id
        WHERE dt.status = ANY(%s)
          AND dt.obs_count >= %s
          AND (%s IS NULL OR dt.last_seen >= %s)
          AND (%s IS NULL OR dt.first_seen <= %s)
          AND (%s IS NULL OR dt.category = %s)
          AND (%s IS NULL OR ST_Intersects(dt.last_centroid,
              ST_MakeEnvelope(%s, %s, %s, %s, 4326)))
        GROUP BY dt.id
        ORDER BY dt.last_seen DESC NULLS LAST
        LIMIT %s
    """
    bbox_minlon = bbox_parts[0] if bbox_parts else None
    bbox_minlat = bbox_parts[1] if bbox_parts else None
    bbox_maxlon = bbox_parts[2] if bbox_parts else None
    bbox_maxlat = bbox_parts[3] if bbox_parts else None

    params = (
        status_list, min_obs,
        start_dt, start_dt,
        end_dt, end_dt,
        category, category,
        bbox_minlon, bbox_minlon, bbox_minlat, bbox_maxlon, bbox_maxlat,
        limit,
    )

    with postgis_db.get_cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    tracks = []
    for row in rows:
        row = dict(row)
        tracks.append({
            "id": row["track_uid"],
            "track_uid": row["track_uid"],
            "primary_class": row["primary_class"],
            "category": row["category"],
            "threat_level": row["threat_level"],
            "status": row["status"],
            "pinned": row["pinned"],
            "obs_count": row["obs_count"],
            "miss_count": row["miss_count"],
            "first_seen": _dt_iso(row["first_seen"]),
            "last_seen": _dt_iso(row["last_seen"]),
            "latest": {"lat": row["lat"], "lon": row["lon"], "class": row["primary_class"]},
            "history": row["history"] or [],
            "path_geojson": row["path_geojson"],
            "last_velocity": row["last_velocity"] or {},
            "metadata": row["metadata"] or {},
        })
    return {"tracks": tracks, "total": len(tracks)}


@app.get("/api/tracks/detections/{track_uid}")
def get_detection_track(track_uid: str):
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT dt.id, dt.track_uid, dt.primary_class, dt.category, dt.threat_level,
                   dt.status, dt.pinned, dt.obs_count, dt.miss_count,
                   dt.first_seen, dt.last_seen,
                   ST_X(dt.last_centroid) AS lon, ST_Y(dt.last_centroid) AS lat,
                   dt.last_velocity, dt.metadata,
                   ST_AsGeoJSON(dt.path)::text AS path_geojson
            FROM detection_tracks dt WHERE dt.track_uid = %s
        """, (track_uid,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Track not found")
        row = dict(row)

        cursor.execute("""
            SELECT m.detection_id, m.pass_id, m.observed_at,
                   ST_Y(m.centroid) AS lat, ST_X(m.centroid) AS lon,
                   m.seq_index, m.cost,
                   d.class, d.confidence
            FROM detection_track_members m
            JOIN detections d ON d.id = m.detection_id
            WHERE m.track_id = %s
            ORDER BY m.observed_at
        """, (row["id"],))
        members = [dict(r) for r in cursor.fetchall()]

    history = [
        {
            "lat": m["lat"], "lng": m["lon"],
            "time": _dt_iso(m["observed_at"]),
            "detection_id": m["detection_id"],
            "seq_index": m["seq_index"],
            "cost": m["cost"],
            "class": m["class"],
            "confidence": m["confidence"],
        }
        for m in members
    ]

    track = {
        "id": row["track_uid"],
        "track_uid": row["track_uid"],
        "primary_class": row["primary_class"],
        "category": row["category"],
        "threat_level": row["threat_level"],
        "status": row["status"],
        "pinned": row["pinned"],
        "obs_count": row["obs_count"],
        "miss_count": row["miss_count"],
        "first_seen": _dt_iso(row["first_seen"]),
        "last_seen": _dt_iso(row["last_seen"]),
        "latest": {"lat": row["lat"], "lon": row["lon"], "class": row["primary_class"]},
        "history": history,
        "path_geojson": row["path_geojson"],
        "last_velocity": row["last_velocity"] or {},
        "metadata": row["metadata"] or {},
    }
    return {"track": track}


@app.post("/api/tracks/detections/reprocess")
def reprocess_detection_tracks(req: ReprocessRequest):
    since_dt = None
    if req.since:
        try:
            since_dt = datetime.fromisoformat(req.since)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid since datetime format")
    try:
        from tracker import reprocess_all_tracks
    except ImportError:
        raise HTTPException(status_code=501, detail="Tracker module not available")
    result = reprocess_all_tracks(postgis_db=postgis_db, since=since_dt)
    return {"status": "ok", "result": result}


@app.post("/api/tracks/detections/pin")
def pin_detection(req: PinRequest):
    detection_id = req.detection_id
    with postgis_db.get_cursor() as cursor:
        cursor.execute("""
            SELECT d.id, d.class, d.confidence, d.pass_id,
                   ST_Y(d.centroid) AS lat, ST_X(d.centroid) AS lon,
                   sp.acquisition_time
            FROM detections d
            JOIN satellite_passes sp ON sp.id = d.pass_id
            WHERE d.id = %s AND d.deleted_at IS NULL
        """, (detection_id,))
        det = cursor.fetchone()
        if not det:
            raise HTTPException(status_code=404, detail="Detection not found")
        det = dict(det)

        cursor.execute(
            "SELECT track_id FROM detection_track_members WHERE detection_id = %s",
            (detection_id,)
        )
        existing = cursor.fetchone()
        existing_track_id = existing["track_id"] if existing else None

    if existing_track_id:
        with postgis_db.get_cursor(commit=True) as cursor:
            cursor.execute("""
                UPDATE detection_tracks SET pinned = TRUE, status = 'pinned', updated_at = NOW()
                WHERE id = %s
                RETURNING id, track_uid, status, pinned, primary_class, obs_count
            """, (existing_track_id,))
            updated = dict(cursor.fetchone())
        return {"track": updated, "action": "pinned_existing"}

    det_class = det["class"]
    try:
        cat = category_for_class(det_class)
    except Exception:
        cat = "unknown"
    try:
        threat = assess_detection_threat(det_class, confidence=det["confidence"]).get("threat_level", "unknown")
    except Exception:
        threat = "unknown"

    track_uid = "dt_" + uuid.uuid4().hex[:12]
    acq_time = det["acquisition_time"]

    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            INSERT INTO detection_tracks
              (track_uid, primary_class, category, threat_level, status, pinned, obs_count,
               first_seen, last_seen, last_centroid, last_velocity, metadata)
            VALUES (%s, %s, %s, %s, 'pinned', TRUE, 1, %s, %s,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326), '{}', %s)
            RETURNING id
        """, (
            track_uid, det_class, cat, threat,
            acq_time, acq_time,
            det["lon"], det["lat"],
            json.dumps({"source": "analyst_pin", "pinned_by": "analyst"}),
        ))
        new_track_id = cursor.fetchone()["id"]

        cursor.execute("""
            INSERT INTO detection_track_members
              (track_id, detection_id, pass_id, observed_at, centroid, seq_index, cost)
            VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), 0, 0.0)
            ON CONFLICT (detection_id) DO NOTHING
        """, (
            new_track_id, detection_id, det["pass_id"],
            acq_time, det["lon"], det["lat"],
        ))

    return {"track": {"track_uid": track_uid, "status": "pinned", "pinned": True,
                      "primary_class": det_class, "obs_count": 1}, "action": "created"}


@app.delete("/api/tracks/detections/{track_uid}/pin")
def unpin_detection_track(track_uid: str):
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute("""
            UPDATE detection_tracks SET
              pinned = FALSE,
              status = CASE
                WHEN miss_count >= 3 THEN 'lost'
                WHEN obs_count >= 2 THEN 'confirmed'
                ELSE 'tentative'
              END,
              updated_at = NOW()
            WHERE track_uid = %s
            RETURNING id, status
        """, (track_uid,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Track not found")
    return {"status": "unpinned", "track_uid": track_uid, "new_status": row["status"]}


# --- Ontology admin API ---
# Step 6 of the ontology refactor plan
# (/home/avinash/.claude/plans/the-inference-system-has-piped-nest.md).
# Single-tenant, no auth. Every successful write bumps ontology_version
# (which also invalidates the in-process normalizer cache) so the next
# normalize() call picks up the change.

# Ontology row helpers + sensor filters were hoisted into ontology.py so
# routers/ontology.py can share them. Re-exported here for any older callers
# in this file that still reference the prefixed-underscore names.
from ontology import (
    _branch_row_to_dict,
    _filter_branch_by_sensor,
    _filter_object_by_sensor,
    _object_row_to_dict,
)


def _fetch_branch(cursor, branch_id: str) -> Optional[dict]:
    cursor.execute(
        "SELECT id, parent_id, label, color, short, icon_key, matchers, "
        "       sensors, order_index, created_at, updated_at "
        "FROM ontology_branches WHERE id = %s",
        (branch_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _fetch_object(cursor, object_id: str) -> Optional[dict]:
    cursor.execute(
        "SELECT id, branch_id, label, prompt, sensors, min_gsd_meters, "
        "       icon_key, order_index, created_at, updated_at "
        "FROM ontology_objects WHERE id = %s",
        (object_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)

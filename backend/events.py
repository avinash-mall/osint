"""Shared event-plumbing helpers used by both the FastAPI app and the Celery worker.

* ``publish_event`` — fire-and-forget Redis pub/sub on the ``events:<topic>`` channel
  consumed by the backend's WebSocket bridge.
* ``record_timeline_event`` — append a row to ``timeline_events`` (UI feed).
* ``record_observation`` — append a row to ``observations`` (optional point geom).
* ``normalize_domain`` / ``domain_for_media`` — INT-discipline normalization for the
  ``domain`` column on every event/observation row.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import redis

from database import postgis_db

logger = logging.getLogger(__name__)

_REDIS_POOL: Optional[redis.ConnectionPool] = None


def get_redis_client() -> redis.Redis:
    global _REDIS_POOL
    if _REDIS_POOL is None:
        _REDIS_POOL = redis.ConnectionPool.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0"),
            decode_responses=True,
        )
    return redis.Redis(connection_pool=_REDIS_POOL)


def publish_event(topic: str, payload: dict) -> None:
    """Best-effort publish to ``events:<topic>``. Swallows transport failures."""
    try:
        client = get_redis_client()
        client.publish(f"events:{topic}", json.dumps(payload, default=str))
    except Exception:
        logger.warning("Failed to publish %s event", topic, exc_info=True)


_ALLOWED_DOMAINS = {"GEOINT", "SIGINT", "HUMINT", "OSINT", "MASINT", "FMV", "ADMIN", "WORKFLOW"}


def normalize_domain(value: Optional[str], fallback: str = "OSINT") -> str:
    domain = (value or fallback).strip().upper().replace("/", "_")
    if domain in {"RF_SIGINT", "RF-SIGINT"}:
        return "SIGINT"
    if domain in {"VIDEO"}:
        return "FMV"
    return domain if domain in _ALLOWED_DOMAINS else fallback


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
            cursor.execute(
                """
                INSERT INTO timeline_events (domain, event_type, title, source_id, entity_id, payload, occurred_at)
                VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, NOW()))
                """,
                (
                    normalize_domain(domain),
                    event_type,
                    title,
                    source_id,
                    entity_id,
                    json.dumps(payload or {}, default=str),
                    occurred_at,
                ),
            )
    except Exception:
        logger.warning("Failed to record timeline event type=%s domain=%s", event_type, domain, exc_info=True)


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
) -> Optional[int]:
    """Insert an ``observations`` row. Returns the new id or ``None`` on failure.

    When ``entity_id`` is set, queues the Phase 2.D Neo4j projector so the
    observation arrives in Evidence mode without operator action. Best-effort —
    queue failures log and proceed.
    """
    new_id: Optional[int] = None
    try:
        with postgis_db.get_cursor(commit=True) as cursor:
            if latitude is not None and longitude is not None:
                cursor.execute(
                    """
                    INSERT INTO observations (domain, source_id, entity_id, event_type, title, confidence, geom, payload, provenance, observed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, COALESCE(%s::timestamptz, NOW()))
                    RETURNING id
                    """,
                    (
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
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO observations (domain, source_id, entity_id, event_type, title, confidence, payload, provenance, observed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, NOW()))
                    RETURNING id
                    """,
                    (
                        normalize_domain(domain),
                        source_id,
                        entity_id,
                        event_type,
                        title,
                        confidence or 0,
                        json.dumps(payload or {}, default=str),
                        json.dumps(provenance or {}, default=str),
                        observed_at,
                    ),
                )
            row = cursor.fetchone()
            if row is not None:
                new_id = int(row["id"] if isinstance(row, dict) else row[0])
    except Exception:
        logger.warning("Failed to record observation type=%s domain=%s", event_type, domain, exc_info=True)
        return None

    # Phase 2.D: queue the Neo4j observation projector when entity_id is set.
    # Lazy import avoids a worker → events → worker cycle at module load.
    if new_id is not None and entity_id:
        try:
            from worker import project_observations_to_graph
            project_observations_to_graph.delay(new_id)
        except Exception:
            logger.warning(
                "Failed to queue worker.project_observations_to_graph for observation %s",
                new_id,
                exc_info=True,
            )
    return new_id

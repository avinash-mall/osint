"""Target-package and collection-requirement helpers.

Persist intelligence artefacts via the existing ``reports`` and
``collection_requirements`` tables (see ``platform_schema.py``). Called from
the AI action-proposal execute path in ``routers/ai.py``.
"""

from __future__ import annotations

import json
from typing import Optional

from database import postgis_db
from events import publish_event, record_timeline_event
from platform_schema import ensure_platform_tables


def _latest_observations(target_id: str, limit: int = 25) -> list[dict]:
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, domain, event_type, title, confidence,
                   ST_Y(geom) AS latitude, ST_X(geom) AS longitude,
                   payload, observed_at
            FROM observations
            WHERE entity_id = %s
            ORDER BY observed_at DESC, ingested_at DESC
            LIMIT %s
            """,
            (target_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def _latest_timeline(target_id: str, limit: int = 25) -> list[dict]:
    with postgis_db.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, domain, event_type, title, payload, occurred_at
            FROM timeline_events
            WHERE entity_id = %s
            ORDER BY occurred_at DESC, created_at DESC
            LIMIT %s
            """,
            (target_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def create_target_package(
    target_id: Optional[str],
    title: str,
    sources: list,
    payload: dict,
) -> dict:
    """Snapshot recent observations + timeline for a target into the reports table."""
    ensure_platform_tables()
    observations = _latest_observations(target_id, limit=25) if target_id else []
    timeline = _latest_timeline(target_id, limit=25) if target_id else []
    content = {
        "summary": payload.get("summary") or title,
        "sources": sources or [],
        "observation_count": len(observations),
        "timeline_count": len(timeline),
        "observations": observations,
        "timeline": timeline,
        "extra": {k: v for k, v in payload.items() if k not in {"summary", "sources"}},
    }
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            """
            INSERT INTO reports (title, target_id, report_type, status, content)
            VALUES (%s, %s, 'target_package', 'ready', %s::jsonb)
            RETURNING id, title, target_id, report_type, status, content, created_at
            """,
            (title[:255], target_id, json.dumps(content, default=str)),
        )
        report = dict(cursor.fetchone())
    record_timeline_event(
        "WORKFLOW",
        "report_generated",
        report["title"],
        {"report_id": report["id"], "report_type": "target_package"},
        entity_id=target_id,
    )
    publish_event("ops", {"type": "report_generated", "report": report})
    return report


def create_collection_requirement(
    target_id: Optional[str],
    title: str,
    description: str,
    priority: str,
    aoi: dict,
) -> dict:
    """Persist a new collection requirement against a target."""
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cursor:
        cursor.execute(
            """
            INSERT INTO collection_requirements (title, description, priority, status, target_id, aoi)
            VALUES (%s, %s, %s, 'draft', %s, %s::jsonb)
            RETURNING id, title, description, priority, status, target_id, aoi, created_at, updated_at
            """,
            (title[:255], description, priority or "Medium", target_id, json.dumps(aoi or {}, default=str)),
        )
        requirement = dict(cursor.fetchone())
    record_timeline_event(
        "WORKFLOW",
        "collection_requirement_created",
        requirement["title"],
        {"requirement_id": requirement["id"], "priority": requirement["priority"]},
        entity_id=target_id,
    )
    publish_event("ops", {"type": "collection_requirement_created", "requirement": requirement})
    return requirement

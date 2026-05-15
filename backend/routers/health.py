"""Health probes + operator alerts derived from the same probes."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests
from fastapi import APIRouter

from ai import ai_status
from database import db, postgis_db
from detection_policy import active_detection_policy

router = APIRouter()


_DETECTION_POLICY = active_detection_policy()
_INFERENCE_SAM3_URL = os.getenv("INFERENCE_SAM3_URL", "http://inference-sam3:8001")


@router.get("/api/health")
def health():
    status = {
        "api": "ok",
        "neo4j": "unknown",
        "postgis": "unknown",
        "ai": ai_status(),
        "detection_policy": _DETECTION_POLICY,
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


@router.get("/api/alerts")
def alerts():
    """Operator-facing health alerts derived from the same checks /api/health runs.

    Returns a list of ``{id, severity, title, source, at}`` so the Admin · Alerts
    panel can render a unified feed without duplicating the health probes.
    """
    items: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        with db.get_session() as session:
            session.run("RETURN 1 AS ok").single()
    except Exception as exc:  # noqa: BLE001
        items.append({
            "id": "neo4j-down",
            "severity": "high",
            "title": "Neo4j (ontology graph) unreachable",
            "source": "service:neo4j",
            "detail": str(exc)[:200],
            "at": now_iso,
        })

    try:
        with postgis_db.get_cursor() as cursor:
            cursor.execute("SELECT 1 AS ok")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001
        items.append({
            "id": "postgis-down",
            "severity": "high",
            "title": "PostGIS (detections / tracks) unreachable",
            "source": "service:postgis",
            "detail": str(exc)[:200],
            "at": now_iso,
        })

    try:
        resp = requests.get(f"{_INFERENCE_SAM3_URL}/health", timeout=2)
        if resp.status_code >= 400:
            items.append({
                "id": "inference-error",
                "severity": "medium",
                "title": f"Inference service responded {resp.status_code}",
                "source": "service:sam3",
                "detail": resp.text[:200],
                "at": now_iso,
            })
    except requests.RequestException as exc:
        items.append({
            "id": "inference-unreachable",
            "severity": "medium",
            "title": "Inference service unreachable",
            "source": "service:sam3",
            "detail": str(exc)[:200],
            "at": now_iso,
        })

    # Failed ingest tasks in the last 24 h surface as alerts.
    try:
        with postgis_db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT upload_id, filename, status, updated_at
                FROM upload_jobs
                WHERE status IN ('failed', 'error')
                  AND updated_at > NOW() - INTERVAL '24 hours'
                ORDER BY updated_at DESC
                LIMIT 25
                """
            )
            rows = cursor.fetchall() or []
            for row in rows:
                if hasattr(row, "get"):
                    upload_id = row.get("upload_id")
                    filename = row.get("filename")
                    updated_at = row.get("updated_at")
                else:
                    upload_id, filename, _status, updated_at = row
                items.append({
                    "id": f"upload-failed-{upload_id}",
                    "severity": "medium",
                    "title": f"Ingest failed · {filename or upload_id}",
                    "source": f"upload:{upload_id}",
                    "detail": "Ingest pipeline failed within the last 24 h",
                    "at": (updated_at.isoformat() if hasattr(updated_at, "isoformat") else now_iso),
                })
    except Exception:
        # upload_jobs query is best-effort; absence shouldn't itself alert.
        pass

    return {"alerts": items, "count": len(items), "generated_at": now_iso}

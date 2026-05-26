"""Unit tests for /api/detections/classes LLM display-label policy."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _ensure_envs() -> None:
    os.environ.setdefault("SESSION_SECRET", "test-session-secret-please-replace-1234567890abcdef")
    os.environ.setdefault("ADMIN_USERNAME", "test-admin")
    os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")
    os.environ.setdefault("NEO4J_URI", "bolt://localhost:9999")
    os.environ.setdefault("POSTGIS_URI", "postgresql://nobody:nobody@localhost:9999/none")


def _row(
    cls: str,
    *,
    count: int = 3,
    amg_image_count: int = 0,
    amg_image_primary: bool = False,
) -> dict:
    return {
        "class": cls,
        "parent_class": cls,
        "count": count,
        "max_confidence": 0.91,
        "avg_confidence": 0.81,
        "amg_image_count": amg_image_count,
        "amg_image_primary": amg_image_primary,
        "branch_id": "Other",
        "icon_key": "circle_help",
        "allegiance_counts": {},
        "branch_breakdown": [],
    }


def _stub_postgis(rows: list[dict]):
    cursor = MagicMock()
    cursor.execute = MagicMock()
    cursor.fetchall = MagicMock(return_value=rows)
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cursor)
    cm.__exit__ = MagicMock(return_value=False)
    pg = MagicMock()
    pg.get_cursor = MagicMock(return_value=cm)
    return cursor, pg


def _patch_ontology(monkeypatch, main):
    def _ontology(det_class: str, confidence: float = 0.0, allegiance: str = "unknown") -> dict:
        return {
            "label": f"Default {det_class}",
            "category": "unknown",
            "threat_level": "low",
            "threat_confidence": 0.1,
            "assessment_status": "heuristic",
            "evidence": [],
            "description": "deterministic",
            "recommended_filter": det_class,
        }

    monkeypatch.setattr(main, "conservative_detection_ontology", _ontology)


def test_amg_image_rows_promote_llm_display_label(monkeypatch):
    _ensure_envs()
    import main  # noqa: WPS433

    _patch_ontology(monkeypatch, main)
    monkeypatch.setattr(
        main,
        "llm_detection_ontology",
        lambda det_class, count=0, avg_confidence=0.0: {
            "label": f"AI {det_class}",
            "description": f"Generated description for {det_class}",
            "recommended_filter": f"ai-{det_class}",
            "generated_by": "test-llm",
        },
    )
    rows = [
        _row("lvis_airport_terminal", count=4, amg_image_count=4, amg_image_primary=True),
        _row("building", count=2, amg_image_count=0, amg_image_primary=False),
        _row("bus", count=5, amg_image_count=3, amg_image_primary=False),
    ]
    cursor, pg = _stub_postgis(rows)
    monkeypatch.setattr(main, "postgis_db", pg)

    result = main.get_detection_classes(bbox=None, start_time=None, end_time=None, llm=True)

    by_class = {item["class"]: item for item in result["classes"]}
    assert by_class["lvis_airport_terminal"]["display_label"] == "AI lvis_airport_terminal"
    assert by_class["lvis_airport_terminal"]["label_source"] == "llm_advisory"
    assert by_class["lvis_airport_terminal"]["label"] == "Default lvis_airport_terminal"
    assert by_class["lvis_airport_terminal"]["amg_image_count"] == 4
    assert by_class["building"]["display_label"] == "Default building"
    assert by_class["building"]["label_source"] == "deterministic"
    assert by_class["bus"]["display_label"] == "Default bus"
    assert by_class["bus"]["label_source"] == "deterministic"
    assert "upload_jobs" in cursor.execute.call_args.args[0]


def test_llm_unavailable_keeps_deterministic_display_label(monkeypatch):
    _ensure_envs()
    import main  # noqa: WPS433

    _patch_ontology(monkeypatch, main)

    def _raise(*args, **kwargs):
        raise main.AIUnavailable("offline")

    monkeypatch.setattr(main, "llm_detection_ontology", _raise)
    _, pg = _stub_postgis([
        _row("lvis_viaduct", count=3, amg_image_count=3, amg_image_primary=True),
    ])
    monkeypatch.setattr(main, "postgis_db", pg)

    result = main.get_detection_classes(bbox=None, start_time=None, end_time=None, llm=True)
    item = result["classes"][0]
    assert item["display_label"] == "Default lvis_viaduct"
    assert item["label_source"] == "deterministic"
    assert item["classification_status"] == "unavailable"

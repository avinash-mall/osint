"""Unit tests for the read-only AOI brief helpers (B1).

Offline, no DB, no LLM: exercise the pure digest/prompt builders directly.
"""

from __future__ import annotations

from routers.ai import _build_brief_prompt, _summarize_detections


def test_summarize_counts_and_top_classes():
    dets = [
        {"object_class": "vehicle", "confidence": 0.9},
        {"object_class": "vehicle", "confidence": 0.7},
        {"object_class": "aircraft", "confidence": 0.95},
        {"object_class": "vessel", "confidence": None},
    ]
    s = _summarize_detections(dets)
    assert s["total"] == 4
    assert s["distinct_classes"] == 3
    assert s["classes"]["vehicle"] == 2
    assert s["max_confidence"] == 0.95


def test_summarize_empty():
    s = _summarize_detections([])
    assert s["total"] == 0
    assert s["distinct_classes"] == 0
    assert s["classes"] == {}
    assert s["max_confidence"] == 0.0


def test_summarize_tolerates_bad_confidence():
    s = _summarize_detections([{"object_class": "x", "confidence": "oops"}])
    assert s["total"] == 1
    assert s["max_confidence"] == 0.0


def test_brief_prompt_mentions_center_and_classes():
    s = _summarize_detections([{"object_class": "tank", "confidence": 0.8}])
    p = _build_brief_prompt(25.1234, 55.5678, 5000, s, [{"title": "new detection"}])
    assert "25.1234" in p
    assert "55.5678" in p
    assert "5000" in p
    assert "tank" in p
    assert "new detection" in p
    # guardrail against fabrication is present
    assert "do not invent" in p.lower()


def test_brief_prompt_handles_no_detections():
    s = _summarize_detections([])
    p = _build_brief_prompt(0.0, 0.0, 1000, s, [])
    assert "no detections" in p
    assert "none" in p  # no recent activity

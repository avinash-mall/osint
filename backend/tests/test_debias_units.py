from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from candidate_linking import rank_candidate_links, score_candidate_link
from main import _decode_detection_cursor, _encode_detection_cursor
from routers import analytics
from tracker import _embedding_payload, _observation_sigma_m, _predicted_position_sigma_m, _track_state
from worker import _DetectionDedupeIndex, _WeightedBoxFusionIndex


def _det(bbox, **extra):
    return {
        "class": "ship",
        "parent_class": "ship",
        "confidence": 0.8,
        "pixel_bbox": bbox,
        **extra,
    }


def test_candidate_ranker_uses_canonical_score_and_top_n():
    det = {"class": "tank", "confidence": 0.9, "lat": 25.0, "lon": 55.0}
    targets = [
        {"stable_id": "a", "name": "tank alpha", "type": "tank", "category": "armored_vehicle", "lat": 25.0, "lon": 55.0},
        {"stable_id": "b", "name": "civilian lot", "type": "parking", "category": "civilian", "lat": 25.001, "lon": 55.001},
    ]
    ranked = rank_candidate_links(det, targets, max_candidates_per_detection=1)
    direct = score_candidate_link(det, targets[0], max_distance_m=1500.0)
    assert [item["target_id"] for item in ranked] == ["a"]
    assert ranked[0]["score"] == direct["score"]


def test_wbf_streaming_emits_only_changed_heads():
    idx = _WeightedBoxFusionIndex(iou_threshold=0.2)
    first = idx.add([_det([0, 0, 10, 10])])
    second = idx.add([_det([1, 1, 11, 11])])
    third = idx.add([_det([100, 100, 110, 110])])
    assert len(first) == len(second) == len(third) == 1
    assert len(idx.heads()) == 2
    assert first[0] is second[0]  # same fused head updated, not a replay set
    assert third[0] is not first[0]


def test_sar_overlap_dedupe_suppresses_cross_chip_duplicate():
    idx = _DetectionDedupeIndex(iou_threshold=0.45)
    kept_a = idx.add([_det([0, 0, 10, 10], modality="sar")])
    kept_b = idx.add([_det([1, 1, 11, 11], modality="sar")])
    assert len(kept_a) == 1
    assert kept_b == []
    assert idx.raw_seen == 2
    assert idx.kept_count == 1


def test_detection_cursor_round_trip_keeps_composite_order_key():
    created = datetime(2026, 5, 18, 12, 30, tzinfo=timezone.utc)
    token = _encode_detection_cursor(created, 17)
    decoded_created, decoded_id = _decode_detection_cursor(token)
    assert decoded_created == created.isoformat()
    assert decoded_id == 17
    assert isinstance(token, str)


def test_tracker_reads_persisted_uncertainty_motion_and_embedding():
    det = {"metadata": {"position_uncertainty_m": 12.5, "embedding": [1.0, 0.0]}}
    assert _observation_sigma_m(det) == 12.5
    assert _embedding_payload(det) == [1.0, 0.0]
    track = {
        "position_sigma_m": 5.0,
        "velocity_sigma_mps": 1.0,
        "motion_state": "highway",
        "last_velocity": {"vx_mps": 1.0, "vy_mps": 0.0},
    }
    assert _track_state(track, "ground") == "highway"
    assert _predicted_position_sigma_m(track, 10.0, "ground", "highway") > 5.0


def test_analytics_missing_dem_fails_honestly_by_default(monkeypatch):
    monkeypatch.delenv("ANALYTICS_ALLOW_FIXTURES", raising=False)
    monkeypatch.setattr(analytics, "dem_available", lambda: False)
    with pytest.raises(HTTPException) as exc:
        analytics.run_viewshed(analytics.AnalyticsRequest())
    assert exc.value.status_code == 503

"""Tests for backend/fmv_tracker.py — post-inference FMV track consolidation.

Unit tests (offline) exercise the pure consolidation core. The integration
test touches live PostGIS and is skipped when the DB is unavailable (see
conftest.py).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import fmv_tracker  # noqa: E402


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _det(det_id, frame, cls, conf, bbox, orig_tid=None, emb=None):
    """Build a consolidation detection dict."""
    return {
        "id": det_id, "frame": frame, "cls": cls, "conf": conf,
        "bbox": list(bbox), "orig_tid": orig_tid, "emb": emb,
    }


def _track(last_frame, bbox, cls, emb=None):
    return {"last_frame": last_frame, "last_bbox": list(bbox),
            "last_emb": emb, "last_class": cls}


BOX = [0.5, 0.5, 0.2, 0.2]
FAR_BOX = [0.1, 0.1, 0.05, 0.05]


# --------------------------------------------------------------------------
# Cost function
# --------------------------------------------------------------------------

def test_pair_cost_identical_box_is_cheap():
    cost = fmv_tracker._pair_cost(_track(0, BOX, "car"), _det(1, 1, "car", 0.8, BOX),
                                  frame=1, max_gap=60)
    assert cost is not None
    assert cost < fmv_tracker._MATCH_THRESHOLD


def test_pair_cost_disjoint_without_embedding_is_gated_out():
    cost = fmv_tracker._pair_cost(_track(0, BOX, "car"), _det(1, 1, "car", 0.8, FAR_BOX),
                                  frame=1, max_gap=60)
    assert cost is None  # neither IoU nor embedding clears its gate


def test_pair_cost_temporal_gate():
    cost = fmv_tracker._pair_cost(_track(0, BOX, "car"), _det(1, 999, "car", 0.8, BOX),
                                  frame=999, max_gap=60)
    assert cost is None  # too far in time


def test_pair_cost_embedding_rescues_iou_zero():
    vec = np.ones(8, dtype=np.float32)
    vec = vec / np.linalg.norm(vec)
    cost = fmv_tracker._pair_cost(_track(0, BOX, "car", emb=vec),
                                  _det(1, 1, "car", 0.8, FAR_BOX, emb=vec),
                                  frame=1, max_gap=60)
    assert cost is not None  # embedding similarity clears the gate


def test_class_penalty_is_soft_not_a_gate():
    # Different classes still produce a finite penalty, never +inf.
    assert 0.0 < fmv_tracker._class_penalty("vehicle", "person") <= 0.6
    assert fmv_tracker._class_penalty("car", "car") == 0.0


# --------------------------------------------------------------------------
# Class voting
# --------------------------------------------------------------------------

def test_vote_class_prefers_temporal_support_over_peak_confidence():
    # "car" seen on 10 frames at modest confidence; "boat" a single
    # high-confidence misfire. Temporal support must win.
    dets = [_det(i, i, "car", 0.40, BOX) for i in range(10)]
    dets.append(_det(99, 5, "boat", 0.99, BOX))
    assert fmv_tracker._vote_class(dets) == "car"


def test_vote_class_collapses_yoloe_label_flicker():
    # YOLOE flips the label every frame; the majority label wins.
    flips = ["truck", "car", "truck", "truck", "car", "truck"]
    dets = [_det(i, i, c, 0.6, BOX) for i, c in enumerate(flips)]
    assert fmv_tracker._vote_class(dets) == "truck"


# --------------------------------------------------------------------------
# Full consolidation core
# --------------------------------------------------------------------------

def _drifting_box(step):
    return [0.30 + 0.01 * step, 0.50, 0.20, 0.20]


def test_consolidate_merges_cross_prompt_duplicate_into_one_track():
    # One physical object, sampled every 8 source frames, detected by two
    # prompts ("vehicle" + "person") — each frame yields two near-identical
    # boxes. Expect a single consolidated track.
    dets = []
    did = 0
    frames = [0, 8, 16, 24, 32]
    for step, frame in enumerate(frames):
        box = _drifting_box(step)
        dets.append(_det(did, frame, "vehicle", 0.80, box, orig_tid=1)); did += 1
        dets.append(_det(did, frame, "person", 0.50, box, orig_tid=2)); did += 1

    plan = fmv_tracker.consolidate(dets, max_gap_frames=60)

    assert len(plan["tracks"]) == 1
    # "vehicle" has equal frame support but higher confidence -> canonical.
    assert plan["tracks"][0]["canonical_class"] == "vehicle"
    # One row per frame survives; the duplicate is soft-deleted.
    assert len(plan["soft_delete_ids"]) == len(frames)
    assert len(plan["assignment"]) == len(frames)
    # Every surviving row carries the same consolidated track id.
    assert {cid for cid, _ in plan["assignment"].values()} == {1}


def test_consolidate_keeps_distinct_objects_separate():
    a = [_det(i, i * 8, "car", 0.8, [0.25, 0.25, 0.1, 0.1]) for i in range(5)]
    b = [_det(50 + i, i * 8, "car", 0.8, [0.75, 0.75, 0.1, 0.1]) for i in range(5)]
    plan = fmv_tracker.consolidate(a + b, max_gap_frames=60)
    assert len(plan["tracks"]) == 2
    assert not plan["soft_delete_ids"]


def test_consolidate_empty_input():
    plan = fmv_tracker.consolidate([], max_gap_frames=60)
    assert plan["tracks"] == []
    assert plan["soft_delete_ids"] == []
    assert plan["heartbeat_rows"] == 0


def test_consolidate_deterministic_track_ids():
    dets = [_det(i, i * 8, "car", 0.8, _drifting_box(i)) for i in range(6)]
    first = fmv_tracker.consolidate(dets, max_gap_frames=60)["assignment"]
    second = fmv_tracker.consolidate(dets, max_gap_frames=60)["assignment"]
    assert first == second


def test_consolidate_heartbeat_routed_to_its_spatial_track():
    # Spatial rows carry orig_tid=7; a heartbeat row (empty bbox) with the
    # same lineage must join that track and never be soft-deleted.
    dets = [_det(i, i * 8, "car", 0.8, _drifting_box(i), orig_tid=7) for i in range(4)]
    dets.append(_det(99, 8, "car", 0.0, [], orig_tid=7))  # heartbeat
    plan = fmv_tracker.consolidate(dets, max_gap_frames=60)
    assert len(plan["tracks"]) == 1
    assert plan["heartbeat_rows"] == 1
    assert 99 not in plan["soft_delete_ids"]
    assert 99 in plan["assignment"]


def test_consolidate_heartbeat_only_lineage_forms_its_own_track():
    spatial = [_det(i, i * 8, "car", 0.8, _drifting_box(i), orig_tid=1) for i in range(3)]
    heartbeat_only = [_det(50, 4, "person", 0.0, [], orig_tid=999)]
    plan = fmv_tracker.consolidate(spatial + heartbeat_only, max_gap_frames=60)
    assert len(plan["tracks"]) == 2
    assert 50 not in plan["soft_delete_ids"]
    assert 50 in plan["assignment"]


# --------------------------------------------------------------------------
# Integration — live PostGIS
# --------------------------------------------------------------------------

@pytest.mark.integration
def test_consolidate_fmv_tracks_end_to_end():
    from database import postgis_db
    from platform_schema import ensure_platform_tables

    ensure_platform_tables()
    clip_id = None
    try:
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO fmv_clips (name, file_path, fps, status, metadata) "
                "VALUES (%s, %s, %s, %s, %s::jsonb) RETURNING id",
                ("test_fmv_tracker_clip", "/tmp/test_fmv_tracker.mp4", 30.0, "ready", "{}"),
            )
            clip_id = cur.fetchone()["id"]
            # One object, 3 frames, detected under two prompts -> 6 raw rows.
            for step, frame in enumerate((0, 8, 16)):
                box = [0.30 + 0.01 * step, 0.50, 0.20, 0.20]
                for prompt, tid, conf in (("vehicle", 1, 0.8), ("person", 2, 0.5)):
                    cur.execute(
                        "INSERT INTO fmv_detections (clip_id, frame_index, class, confidence, bbox, metadata) "
                        "VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)",
                        (clip_id, frame, prompt, conf, json.dumps(box),
                         json.dumps({"track_id": tid, "prompt_text": prompt})),
                    )

        result = fmv_tracker.consolidate_fmv_tracks(clip_id, postgis_db=postgis_db)
        assert result["input_rows"] == 6
        assert result["consolidated_tracks"] == 1
        assert result["rows_soft_deleted"] == 3
        assert result["rows_rewritten"] == 3

        with postgis_db.get_cursor() as cur:
            cur.execute(
                "SELECT class, metadata FROM fmv_detections "
                "WHERE clip_id = %s AND deleted_at IS NULL",
                (clip_id,),
            )
            live = [dict(r) for r in cur.fetchall()]
        assert len(live) == 3
        assert {r["class"] for r in live} == {"vehicle"}
        assert {r["metadata"]["track_id"] for r in live} == {1}
        assert all(r["metadata"].get("consolidated") for r in live)
        assert all("original_class" in r["metadata"] for r in live)

        # Idempotent: a second run collapses nothing further.
        again = fmv_tracker.consolidate_fmv_tracks(clip_id, postgis_db=postgis_db)
        assert again["rows_soft_deleted"] == 0
        assert again["class_changes"] == 0
        assert again["consolidated_tracks"] == 1
    finally:
        if clip_id is not None:
            with postgis_db.get_cursor(commit=True) as cur:
                cur.execute("DELETE FROM fmv_detections WHERE clip_id = %s", (clip_id,))
                cur.execute("DELETE FROM fmv_clips WHERE id = %s", (clip_id,))


@pytest.mark.integration
def test_consolidate_fmv_tracks_empty_clip():
    from database import postgis_db
    from platform_schema import ensure_platform_tables

    ensure_platform_tables()
    clip_id = None
    try:
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO fmv_clips (name, file_path, fps, status, metadata) "
                "VALUES (%s, %s, %s, %s, %s::jsonb) RETURNING id",
                ("test_fmv_tracker_empty", "/tmp/test_fmv_empty.mp4", 30.0, "ready", "{}"),
            )
            clip_id = cur.fetchone()["id"]
        result = fmv_tracker.consolidate_fmv_tracks(clip_id, postgis_db=postgis_db)
        assert result["input_rows"] == 0
        assert result["consolidated_tracks"] == 0
    finally:
        if clip_id is not None:
            with postgis_db.get_cursor(commit=True) as cur:
                cur.execute("DELETE FROM fmv_clips WHERE id = %s", (clip_id,))

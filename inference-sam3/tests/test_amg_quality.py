"""Pure-Python unit tests for Phase 3 AMG quality filters and labelling.

Covers the deterministic helpers that don't need the SAM 3 model:
- ``_mask_edge_frac``
- ``_passes_quality_filters`` (score / area / edge gates)
- ``_assign_amg_labels_via_gd`` (Grounding-DINO IoU matching)
- masklet confirmation behaviour (simulated frame loop)
"""
from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture(scope="module")
def runner_module():
    import sam3_runner  # noqa: WPS433
    return sam3_runner


# ---------------------------------------------------------------------------
# Edge-fraction helper
# ---------------------------------------------------------------------------


def test_mask_edge_frac_zero_for_centered_blob(runner_module):
    mask = np.zeros((64, 64), dtype=bool)
    mask[20:44, 20:44] = True  # entirely interior
    assert runner_module._mask_edge_frac(mask) == pytest.approx(0.0, abs=1e-4)


def test_mask_edge_frac_one_for_border_only_mask(runner_module):
    mask = np.zeros((64, 64), dtype=bool)
    mask[0:2, :] = True  # whole top 2-row ring
    assert runner_module._mask_edge_frac(mask, ring_px=2) == pytest.approx(1.0, abs=1e-4)


def test_mask_edge_frac_zero_for_empty_mask(runner_module):
    mask = np.zeros((32, 32), dtype=bool)
    assert runner_module._mask_edge_frac(mask) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Pre-NMS quality gate
# ---------------------------------------------------------------------------


def test_quality_filter_rejects_low_score(runner_module):
    mask = np.zeros((64, 64), dtype=bool)
    mask[10:30, 10:30] = True
    assert not runner_module._passes_quality_filters(
        mask, score=0.10, frame_total_px=64 * 64,
        pred_iou_thresh=0.50, min_area_px=10,
        max_area_frac=0.5, edge_frac_max=0.8,
    )


def test_quality_filter_rejects_small_area(runner_module):
    mask = np.zeros((64, 64), dtype=bool)
    mask[5:7, 5:7] = True  # 4 pixels
    assert not runner_module._passes_quality_filters(
        mask, score=0.9, frame_total_px=64 * 64,
        pred_iou_thresh=0.5, min_area_px=200,
        max_area_frac=0.5, edge_frac_max=0.8,
    )


def test_quality_filter_rejects_oversized_mask(runner_module):
    mask = np.ones((64, 64), dtype=bool)  # covers whole frame
    assert not runner_module._passes_quality_filters(
        mask, score=0.9, frame_total_px=64 * 64,
        pred_iou_thresh=0.5, min_area_px=10,
        max_area_frac=0.5, edge_frac_max=1.0,
    )


def test_quality_filter_rejects_edge_hugger(runner_module):
    mask = np.zeros((64, 64), dtype=bool)
    mask[0:2, :] = True  # 100% edge-hugging
    assert not runner_module._passes_quality_filters(
        mask, score=0.9, frame_total_px=64 * 64,
        pred_iou_thresh=0.5, min_area_px=10,
        max_area_frac=0.9, edge_frac_max=0.8,
    )


def test_quality_filter_accepts_good_candidate(runner_module):
    mask = np.zeros((64, 64), dtype=bool)
    mask[20:40, 20:40] = True
    assert runner_module._passes_quality_filters(
        mask, score=0.85, frame_total_px=64 * 64,
        pred_iou_thresh=0.5, min_area_px=200,
        max_area_frac=0.5, edge_frac_max=0.8,
    )


# ---------------------------------------------------------------------------
# Grounding-DINO label assignment
# ---------------------------------------------------------------------------


def test_label_assignment_disabled_returns_all_none(runner_module, monkeypatch):
    monkeypatch.setattr(runner_module, "SAM3_AMG_LABEL_VIA_GD", False)
    out = runner_module._assign_amg_labels_via_gd(
        {"grounding_dino": {"model": object()}}, np.zeros((64, 64, 3), dtype=np.uint8),
        [[0, 0, 10, 10], [20, 20, 30, 30]],
    )
    assert out == [None, None]


def test_label_assignment_no_gd_bundle_returns_all_none(runner_module):
    out = runner_module._assign_amg_labels_via_gd(
        {"grounding_dino": None}, np.zeros((64, 64, 3), dtype=np.uint8),
        [[0, 0, 10, 10]],
    )
    assert out == [None]


def test_label_assignment_matches_best_iou(runner_module, monkeypatch):
    monkeypatch.setattr(runner_module, "SAM3_AMG_LABEL_VIA_GD", True)
    monkeypatch.setattr(runner_module, "SAM3_AMG_LABEL_IOU_MIN", 0.30)
    # Fake grounding_dino.run: returns one box at (10, 10, 30, 30) labelled "vehicle"
    import grounding_dino
    monkeypatch.setattr(
        grounding_dino, "run",
        lambda bundle, img, prompts, score_threshold: [
            (np.zeros((1, 1), dtype=bool), [10.0, 10.0, 30.0, 30.0], 0.80, "vehicle"),
        ],
    )
    bundle = {"grounding_dino": {"model": object()}}
    out = runner_module._assign_amg_labels_via_gd(
        bundle, np.zeros((64, 64, 3), dtype=np.uint8),
        [
            [10, 10, 30, 30],   # exact overlap → vehicle
            [50, 50, 60, 60],   # no overlap → None
        ],
    )
    assert out[0] == "vehicle"
    assert out[1] is None


def test_label_assignment_gd_failure_returns_all_none(runner_module, monkeypatch):
    monkeypatch.setattr(runner_module, "SAM3_AMG_LABEL_VIA_GD", True)
    import grounding_dino
    def _boom(*a, **kw):
        raise RuntimeError("fake gd failure")
    monkeypatch.setattr(grounding_dino, "run", _boom)
    bundle = {"grounding_dino": {"model": object()}}
    out = runner_module._assign_amg_labels_via_gd(
        bundle, np.zeros((64, 64, 3), dtype=np.uint8), [[0, 0, 10, 10]],
    )
    assert out == [None]


# ---------------------------------------------------------------------------
# Masklet confirmation pattern (simulated state machine)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 4: GD-first detector dispatcher and zero-box behaviour
# ---------------------------------------------------------------------------


def test_amg_sweep_via_gd_returns_empty_when_no_gd_bundle(runner_module):
    out = runner_module._amg_sweep_via_gd(
        {"grounding_dino": None, "sam3_image": {"processor": object()}, "device": "cpu"},
        np.zeros((64, 64, 3), dtype=np.uint8),
        score_threshold=0.25,
        nms_iou=0.5,
    )
    assert out == []


def test_amg_sweep_via_gd_returns_empty_on_gd_zero_boxes(runner_module, monkeypatch):
    """GD vocab gap → no boxes → empty list. No grid fallback (user choice)."""
    import grounding_dino
    monkeypatch.setattr(grounding_dino, "run", lambda *a, **kw: [])
    bundle = {
        "grounding_dino": {"model": object(), "processor": object(), "device": "cpu"},
        "sam3_image": {"processor": object()},
        "device": "cpu",
    }
    out = runner_module._amg_sweep_via_gd(
        bundle, np.zeros((64, 64, 3), dtype=np.uint8),
        score_threshold=0.25, nms_iou=0.5,
    )
    assert out == []


def test_amg_sweep_via_gd_returns_empty_on_gd_exception(runner_module, monkeypatch):
    import grounding_dino
    def _boom(*a, **kw):
        raise RuntimeError("simulated GD failure")
    monkeypatch.setattr(grounding_dino, "run", _boom)
    bundle = {"grounding_dino": {"model": object()}}
    out = runner_module._amg_sweep_via_gd(
        bundle, np.zeros((64, 64, 3), dtype=np.uint8),
        score_threshold=0.25, nms_iou=0.5,
    )
    assert out == []


def test_dispatcher_routes_to_grid_when_env_selects_grid(runner_module, monkeypatch):
    """When SAM3_AMG_DETECTOR=grid, the dispatcher must call the grid path."""
    monkeypatch.setattr(runner_module, "SAM3_AMG_DETECTOR", "grid")
    called = {"grid": False, "gd": False}

    def fake_grid(*a, **kw):
        called["grid"] = True
        return []

    def fake_gd(*a, **kw):
        called["gd"] = True
        return []

    monkeypatch.setattr(runner_module, "_amg_sweep_image_grid", fake_grid)
    monkeypatch.setattr(runner_module, "_amg_sweep_via_gd", fake_gd)
    runner_module._amg_sweep_image(
        bundle={}, image_rgb_uint8=np.zeros((4, 4, 3), dtype=np.uint8),
        grid_size=4, score_threshold=0.5, nms_iou=0.5, point_box_norm=0.02,
    )
    assert called["grid"] is True
    assert called["gd"] is False


def test_dispatcher_routes_to_gd_by_default(runner_module, monkeypatch):
    """When SAM3_AMG_DETECTOR=gd (default), the dispatcher must call the GD path."""
    monkeypatch.setattr(runner_module, "SAM3_AMG_DETECTOR", "gd")
    called = {"grid": False, "gd": False}

    def fake_grid(*a, **kw):
        called["grid"] = True
        return []

    def fake_gd(*a, **kw):
        called["gd"] = True
        return []

    monkeypatch.setattr(runner_module, "_amg_sweep_image_grid", fake_grid)
    monkeypatch.setattr(runner_module, "_amg_sweep_via_gd", fake_gd)
    runner_module._amg_sweep_image(
        bundle={}, image_rgb_uint8=np.zeros((4, 4, 3), dtype=np.uint8),
        grid_size=4, score_threshold=0.5, nms_iou=0.5, point_box_norm=0.02,
    )
    assert called["grid"] is False
    assert called["gd"] is True


def test_dispatcher_bypass_quality_filters_forces_grid(runner_module, monkeypatch):
    """probe_amg passes bypass_quality_filters=True → must route to grid
    so the synthetic 64×64 fixture probe stays deterministic regardless of
    detector mode."""
    monkeypatch.setattr(runner_module, "SAM3_AMG_DETECTOR", "gd")
    called = {"grid": False, "gd": False}
    monkeypatch.setattr(runner_module, "_amg_sweep_image_grid",
                        lambda *a, **kw: called.__setitem__("grid", True) or [])
    monkeypatch.setattr(runner_module, "_amg_sweep_via_gd",
                        lambda *a, **kw: called.__setitem__("gd", True) or [])
    runner_module._amg_sweep_image(
        bundle={}, image_rgb_uint8=np.zeros((4, 4, 3), dtype=np.uint8),
        grid_size=4, score_threshold=0.0, nms_iou=0.7, point_box_norm=0.05,
        bypass_quality_filters=True,
    )
    assert called["grid"] is True
    assert called["gd"] is False


def test_masklet_confirmation_buffers_then_flushes():
    """Reproduce the inline buffer / flush pattern used inside run_video_amg.

    A track must accumulate ``confirm_n`` consecutive observations before
    its buffered entries are emitted; if missed in between, the buffer is
    discarded and the counter resets.
    """
    confirm_n = 2
    state: dict[int, dict[str, object]] = {}
    emitted: list[str] = []
    dropped = 0

    def observe(tid: int, payload: str) -> None:
        nonlocal dropped
        st = state.setdefault(tid, {"confirmed": False, "consecutive": 0, "pending": []})
        if st["confirmed"]:
            emitted.append(payload)
            return
        st["consecutive"] += 1
        st["pending"].append(payload)
        if st["consecutive"] >= confirm_n:
            st["confirmed"] = True
            for p in st["pending"]:
                emitted.append(p)
            st["pending"] = []

    def miss(tid: int) -> None:
        nonlocal dropped
        st = state.get(tid)
        if st is None or st["confirmed"]:
            return
        dropped += len(st["pending"])
        st["pending"] = []
        st["consecutive"] = 0

    # Track 1: seen twice in a row → confirmed → both entries flush.
    observe(1, "f0:t1")
    observe(1, "f1:t1")
    assert emitted == ["f0:t1", "f1:t1"]
    assert state[1]["confirmed"] is True

    # Track 2: seen once, missed, seen once again. Two single-frame
    # observations should NEVER confirm. Final emitted list unchanged from
    # track 1's two entries.
    observe(2, "f0:t2")
    miss(2)
    observe(2, "f2:t2")
    miss(2)
    assert emitted == ["f0:t1", "f1:t1"]
    assert dropped == 2

    # Track 3: post-confirmation observations stream live.
    observe(1, "f2:t1")
    assert emitted[-1] == "f2:t1"


# ---------------------------------------------------------------------------
# Phase 5: drone-HUD detection & overlap filter
# ---------------------------------------------------------------------------


def test_hud_mask_returns_none_when_disabled(runner_module, monkeypatch, tmp_path):
    """When SAM3_AMG_HUD_MASK_ENABLED=0 the detector short-circuits without
    even opening the video, so it returns None regardless of contents."""
    monkeypatch.setattr(runner_module, "SAM3_AMG_HUD_MASK_ENABLED", False)
    out = runner_module._detect_hud_mask(str(tmp_path / "nonexistent.mp4"))
    assert out is None


def test_hud_mask_returns_none_for_missing_video(runner_module, monkeypatch, tmp_path):
    monkeypatch.setattr(runner_module, "SAM3_AMG_HUD_MASK_ENABLED", True)
    out = runner_module._detect_hud_mask(str(tmp_path / "nope.mp4"))
    assert out is None


def test_hud_mask_detects_static_top_band(runner_module, monkeypatch, tmp_path):
    """Synthetic 8-frame 64×64 clip where the top 16 rows are a fixed colour
    and the bottom 48 rows are random — _detect_hud_mask must flag the top
    band as HUD."""
    import cv2
    monkeypatch.setattr(runner_module, "SAM3_AMG_HUD_MASK_ENABLED", True)
    monkeypatch.setattr(runner_module, "SAM3_AMG_HUD_STD_THRESH", 3.0)
    monkeypatch.setattr(runner_module, "SAM3_AMG_HUD_SAMPLES", 4)
    video_path = tmp_path / "synthetic_hud.mp4"
    writer = cv2.VideoWriter(
        str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), 8.0, (64, 64),
    )
    rng = np.random.default_rng(seed=42)
    try:
        for i in range(8):
            frame = np.zeros((64, 64, 3), dtype=np.uint8)
            frame[0:16, :, :] = 200  # static HUD strip
            frame[16:, :, :] = rng.integers(0, 256, size=(48, 64, 3), dtype=np.uint8)
            writer.write(frame)
    finally:
        writer.release()
    mask = runner_module._detect_hud_mask(str(video_path))
    assert mask is not None
    # Top band should be majority HUD.
    assert mask[0:16, :].mean() > 0.5, "top band not detected as HUD"
    # Bottom band should be mostly NOT HUD (random pixels have high std).
    assert mask[20:, :].mean() < 0.5, "bottom band incorrectly flagged as HUD"


def test_bbox_overlap_with_hud_thresholds(runner_module):
    """A bbox fully inside HUD returns 1.0; outside returns 0.0; partial
    returns the area fraction."""
    hud = np.zeros((100, 100), dtype=bool)
    hud[0:30, :] = True  # HUD = top 30 rows
    # Fully inside HUD
    assert runner_module._bbox_overlap_with_hud([10, 5, 50, 25], hud) == pytest.approx(1.0)
    # Fully outside HUD (rows 40-90)
    assert runner_module._bbox_overlap_with_hud([10, 40, 50, 90], hud) == pytest.approx(0.0)
    # Half inside (bbox spans rows 15-45 — rows 15-29 are HUD = 15/30 = 0.5)
    overlap = runner_module._bbox_overlap_with_hud([10, 15, 50, 45], hud)
    assert 0.45 <= overlap <= 0.55


def test_bbox_overlap_returns_zero_when_mask_is_none(runner_module):
    assert runner_module._bbox_overlap_with_hud([0, 0, 10, 10], None) == 0.0


def test_filter_candidates_by_hud_drops_overlapping(runner_module, monkeypatch):
    monkeypatch.setattr(runner_module, "SAM3_AMG_HUD_OVERLAP_MAX", 0.5)
    hud = np.zeros((100, 100), dtype=bool)
    hud[0:30, :] = True
    # 3-tuple grid-mode candidates
    inside_hud = (np.zeros((100, 100), dtype=bool), [10, 0, 50, 20], 0.9)
    outside_hud = (np.zeros((100, 100), dtype=bool), [10, 50, 50, 90], 0.8)
    partial = (np.zeros((100, 100), dtype=bool), [10, 20, 50, 30], 0.7)  # 10/10 = 100% in HUD
    out = runner_module._filter_candidates_by_hud(
        [inside_hud, outside_hud, partial], hud,
    )
    # Only outside_hud survives
    assert len(out) == 1
    assert out[0] is outside_hud


def test_filter_candidates_by_hud_no_mask_is_passthrough(runner_module):
    inside_like = (np.zeros((100, 100), dtype=bool), [10, 0, 50, 20], 0.9)
    out = runner_module._filter_candidates_by_hud([inside_like], None)
    assert out == [inside_like]


def test_filter_candidates_handles_4_tuples(runner_module, monkeypatch):
    """GD-first mode emits 4-tuples (mask, bbox, score, label); filter
    must preserve shape."""
    monkeypatch.setattr(runner_module, "SAM3_AMG_HUD_OVERLAP_MAX", 0.5)
    hud = np.zeros((100, 100), dtype=bool)
    hud[0:30, :] = True
    inside = (np.zeros((100, 100), dtype=bool), [10, 0, 50, 20], 0.9, "sign")
    outside = (np.zeros((100, 100), dtype=bool), [10, 50, 50, 90], 0.8, "vehicle")
    out = runner_module._filter_candidates_by_hud([inside, outside], hud)
    assert len(out) == 1
    assert len(out[0]) == 4
    assert out[0][3] == "vehicle"

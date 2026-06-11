"""Tests for backend/tracker.py association gating.

Offline unit tests for the spatial gate in ``_compute_cost`` and the
category mapping in ``_tracker_category``. Regression guard for the
cross-continent mis-association bug: two static ``tennis_court``
detections in different cities were stitched into one track because the
Kalman gate radius (~0.5·σ_a·dt²) grew unbounded with the inter-pass time
gap and exceeded Earth's circumference.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import tracker  # noqa: E402


# Abu Dhabi tennis court (the surviving cluster) and a Vienna tennis court.
_ABU_DHABI = (24.4571, 54.5337)
_VIENNA = (48.2114, 16.3992)
_DT_TWO_PASSES = 6000.0  # 1h40m, the real gap between the two passes


def _static_track(lat, lon, *, category, primary_class):
    """A freshly-seeded single-observation stationary track."""
    return {
        "lat": lat,
        "lon": lon,
        "category": category,
        "primary_class": primary_class,
        "obs_count": 1,
        "last_velocity": {"vx_mps": 0.0, "vy_mps": 0.0, "speed_mps": 0.0},
        "position_sigma_m": 5.0,
        "velocity_sigma_mps": 0.0,
    }


def _det(lat, lon, cls, conf=0.8):
    return {"lat": lat, "lon": lon, "class": cls, "confidence": conf}


def test_tracker_category_maps_static_buckets_to_infrastructure(monkeypatch):
    # category_for_class resolves the parent bucket via the ontology DB; here
    # we drive _tracker_category directly. Static buckets (recreation: sport
    # courts/fields; nature: terrain/water/vegetation) must map to the
    # stationary "infrastructure" V_MAX bucket, not the mobile "default" one.
    def fake(cls):
        return {"tennis_court": "recreation", "lake": "nature",
                "truck": "ground", "widget": "object"}[cls]

    monkeypatch.setattr(tracker, "category_for_class", fake)
    assert tracker._tracker_category("tennis_court") == "infrastructure"
    assert tracker._tracker_category("lake") == "infrastructure"
    # Genuine V_MAX buckets and the unknown catch-all are unchanged.
    assert tracker._tracker_category("truck") == "ground"
    assert tracker._tracker_category("widget") == "default"


def test_tracker_category_pins_ontology_unknown_static_names(monkeypatch):
    # Open-vocab / DOTA labels the ontology can't categorise come back as
    # "object". The class-NAME fallback must still pin clearly immobile site
    # classes — otherwise they ride the mobile 16 m/s default and a 2-day
    # inter-pass gap gates in same-class detections thousands of km away
    # (the San Diego → Texas tennis-court streak fan).
    monkeypatch.setattr(tracker, "category_for_class", lambda cls: "object")
    for cls in ("tennis_court", "parking_lot", "solar_panel_array",
                "basketball_court", "baseball_diamond", "swimming_pool",
                "Tennis Court", "soccer-ball-field"):
        assert tracker._tracker_category(cls) == "infrastructure", cls
    # Mobile unknowns stay in the default bucket — "tank" is NOT a static
    # token (battle tanks move); only the exact "storage_tank" is pinned.
    for cls in ("truck", "tank", "excavator", "container_ship"):
        assert tracker._tracker_category(cls) == "default", cls
    assert tracker._tracker_category("storage_tank") == "infrastructure"


def test_gate_rejects_cross_continent_jump_for_static_class():
    # A tennis court in Abu Dhabi cannot be the same object as one in Vienna.
    track = _static_track(*_ABU_DHABI, category="infrastructure",
                          primary_class="tennis_court")
    det = _det(*_VIENNA, "tennis_court")
    assert tracker._compute_cost(track, det, _DT_TWO_PASSES) == np.inf


def test_gate_rejects_cross_continent_jump_for_mobile_default():
    # Even a generic mobile "default" object cannot teleport 4,200 km in
    # 1h40m: that needs ~700 m/s, far above any ground-object top speed.
    track = _static_track(*_ABU_DHABI, category="default", primary_class="object")
    det = _det(*_VIENNA, "object")
    assert tracker._compute_cost(track, det, _DT_TWO_PASSES) == np.inf


def test_gate_admits_plausible_same_spot_redetection():
    # Same tennis court re-detected ~12 m away (GSD jitter) one pass later
    # must still associate — the ceiling must not over-tighten static gates.
    track = _static_track(*_ABU_DHABI, category="infrastructure",
                          primary_class="tennis_court")
    near_lat = _ABU_DHABI[0] + 0.0001  # ~11 m north
    det = _det(near_lat, _ABU_DHABI[1], "tennis_court")
    assert tracker._compute_cost(track, det, _DT_TWO_PASSES) < np.inf


def test_gate_admits_plausible_vehicle_move():
    # A ground vehicle covering ~90 km in 1h40m (~15 m/s) stays in gate.
    start = (24.4571, 54.5337)
    track = _static_track(*start, category="ground", primary_class="truck")
    track["last_velocity"] = {"vx_mps": 0.0, "vy_mps": 15.0, "speed_mps": 15.0}
    track["obs_count"] = 2
    moved_lat = start[0] + 0.8  # ~89 km north
    det = _det(moved_lat, start[1], "truck")
    assert tracker._compute_cost(track, det, _DT_TWO_PASSES) < np.inf

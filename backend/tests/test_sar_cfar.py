"""Offline unit tests for backend/sar_cfar.py.

Covers the 2026-06-12 audit fix: the VH cross-pol gate now uses the same
guard-excluded clutter statistics as the VV path, so a bright target's own
energy no longer leaks into the VH clutter mean/σ and depress its Z-score.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sar_cfar import detect_ships_cfar  # noqa: E402


def _scene(rng, target_db_vv, target_db_vh):
    vv = rng.normal(-20.0, 1.0, (128, 128)).astype(np.float32)
    vh = rng.normal(-26.0, 1.0, (128, 128)).astype(np.float32)
    vv[60:66, 60:66] = target_db_vv
    vh[60:66, 60:66] = target_db_vh
    return vv, vh

def test_cross_pol_target_detected_with_guard_excluded_stats():
    rng = np.random.default_rng(42)
    vv, vh = _scene(rng, target_db_vv=-5.0, target_db_vh=-14.0)
    detections = detect_ships_cfar(vv, vh_db=vh)
    assert detections
    hit = detections[0]["pixel_bbox"]
    assert hit[0] <= 63 <= hit[2] and hit[1] <= 63 <= hit[3]


def test_cross_pol_gate_rejects_vv_only_speckle():
    rng = np.random.default_rng(7)
    vv, vh = _scene(rng, target_db_vv=-5.0, target_db_vh=-26.0)
    # Bright only in VV (land-edge / sea-state speckle): the VH consistency
    # check must reject it.
    detections = detect_ships_cfar(vv, vh_db=vh)
    assert detections == []

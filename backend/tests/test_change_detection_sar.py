"""Unit tests for the SAR log-ratio change preset in change_detection.py.

Offline, no DB, no rasterio I/O: we drive the pure change-map producers
(`_change_map_optical`, `_change_map_sar`) and the shared `_polygonize_mask`
directly with synthetic arrays, asserting the dB log-ratio fires on a known
backscatter jump and stays quiet on an unchanged scene.
"""

from __future__ import annotations

import numpy as np

import change_detection as cd


def _band(value: float, shape=(40, 40)) -> np.ndarray:
    """A single-band (1, H, W) constant array at ``value`` (linear backscatter)."""
    return np.full((1,) + shape, value, dtype=np.float32)


def test_sar_brightening_fires():
    # after is ~4x brighter than before over the whole frame → ~6 dB, well above 3 dB.
    before = _band(0.05)
    after = _band(0.20)
    diff_norm, mask, peak_db = cd._change_map_sar(before, after)
    assert peak_db > cd.CHANGE_DET_SAR_THRESHOLD_DB
    assert mask.sum() > 0
    assert mask.shape == (40, 40)
    # 10*log10(0.20/0.05) ≈ 6.02 dB
    assert 5.5 < peak_db < 6.5


def test_sar_darkening_flood_fires():
    # Flooding makes a smooth water surface that reflects radar away → big drop.
    before = _band(0.30)
    after = _band(0.03)
    _diff_norm, mask, peak_db = cd._change_map_sar(before, after)
    # |10*log10(0.03/0.30)| = 10 dB
    assert peak_db > 9.0
    assert mask.sum() > 0


def test_sar_quiet_scene_no_change():
    # Tiny 0.2 dB wobble — below the 3 dB floor → no mask.
    before = _band(0.10)
    after = _band(0.105)
    _diff_norm, mask, peak_db = cd._change_map_sar(before, after)
    assert peak_db < cd.CHANGE_DET_SAR_THRESHOLD_DB
    assert mask.sum() == 0


def test_sar_partial_change_localised():
    # Only the top half brightens; the despeckle must not erase a large region.
    before = _band(0.05, (40, 40))
    after = _band(0.05, (40, 40)).copy()
    after[0, :20, :] = 0.20  # top half jumps ~6 dB
    _diff_norm, mask, _peak = cd._change_map_sar(before, after)
    assert mask[:20, :].sum() > mask[20:, :].sum()
    assert mask[20:, :].sum() == 0  # quiet half stays unflagged


def test_polygonize_mask_emits_features():
    # A solid changed block in geographic bounds should polygonise to >=1 feature.
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[10:30, 10:30] = 1
    diff_norm = mask.astype(np.float32)
    bounds = (0.0, 0.0, 0.4, 0.4)  # 0.01 deg/px
    feats = cd._polygonize_mask(mask, diff_norm, bounds, 40, 40, label="sar_change")
    assert len(feats) >= 1
    assert feats[0]["properties"]["label"] == "sar_change"
    assert feats[0]["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_optical_diff_still_works():
    # Regression: the refactor must not break the optical path.
    before = np.zeros((3, 30, 30), dtype=np.float32)
    after = np.zeros((3, 30, 30), dtype=np.float32)
    after[:, :15, :] = 1.0
    diff_norm, mask, peak = cd._change_map_optical(before, after)
    assert peak > 0
    assert mask[:15, :].sum() > 0
    assert mask[15:, :].sum() == 0

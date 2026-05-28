"""Tests for cross-detector Weighted Boxes Fusion (Task 2.8).

NOTE: requires the ``ensemble-boxes`` PyPI package (>=1.0.9). The inference
container installs it via ``inference-sam3/requirements.txt``. For local
development outside the container::

    pip install ensemble-boxes

The fallback test below intentionally monkeypatches the import so the
fallback-to-NMS path is exercised even when the package *is* installed.
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import fusion  # noqa: E402


def _det(x1, y1, x2, y2, conf, label, source_layer, *, image_size=(64, 64)):
    """Build a minimal detection dict with mask_rle + xyxy bbox."""
    width, height = image_size
    mask = np.zeros((height, width), dtype=bool)
    xi1, yi1 = int(max(0, x1)), int(max(0, y1))
    xi2, yi2 = int(min(width, x2)), int(min(height, y2))
    mask[yi1:yi2, xi1:xi2] = True
    det = fusion.candidate_to_detection(
        mask, [x1, y1, x2, y2], conf, label, image_size=image_size, modality="rgb",
    )
    det["source_layer"] = source_layer
    return det


def test_wbf_fuses_overlapping_boxes_from_two_sources():
    """Two highly overlapping detections from sam3 + dota_obb get fused."""
    a = _det(10, 10, 30, 30, 0.7, "ship", "sam3")
    b = _det(11, 11, 31, 31, 0.9, "ship", "dota_obb")

    fused = fusion.wbf_fusion([a, b], 64, 64, agnostic=False)

    assert len(fused) == 1
    out = fused[0]
    # Confidence is averaged with weights (sam3=0.5, dota_obb=1.0).
    # WBF conf_type="avg" returns the score average of contributing boxes.
    assert 0.5 < out["confidence"] < 1.0
    assert out["wbf_member_count"] == 2
    assert out["wbf_member_sources"] == sorted(["dota_obb", "sam3"])


def test_wbf_disjoint_boxes_kept_separate():
    a = _det(2, 2, 12, 12, 0.8, "ship", "sam3")
    b = _det(40, 40, 60, 60, 0.7, "ship", "dota_obb")

    fused = fusion.wbf_fusion([a, b], 64, 64, agnostic=False)

    assert len(fused) == 2
    for out in fused:
        assert out["wbf_member_count"] == 1


def test_wbf_class_aware_doesnt_merge_different_classes():
    """Overlapping boxes with different class labels survive separately."""
    a = _det(10, 10, 30, 30, 0.8, "ship", "sam3")
    b = _det(10, 10, 30, 30, 0.7, "boat", "dota_obb")

    fused = fusion.wbf_fusion([a, b], 64, 64, agnostic=False)

    assert len(fused) == 2


def test_wbf_agnostic_merges_different_classes():
    """Same overlapping pair with agnostic=True collapses to a single output."""
    a = _det(10, 10, 30, 30, 0.8, "ship", "sam3")
    b = _det(10, 10, 30, 30, 0.7, "boat", "dota_obb")

    fused = fusion.wbf_fusion([a, b], 64, 64, agnostic=True)

    assert len(fused) == 1
    assert fused[0]["wbf_member_count"] == 2


def test_wbf_skip_box_threshold_filters_low_confidence():
    """Detection at conf 0.02 (below default 0.05) is dropped by WBF."""
    low = _det(10, 10, 30, 30, 0.02, "ship", "sam3")
    keep = _det(40, 40, 60, 60, 0.7, "ship", "dota_obb")

    fused = fusion.wbf_fusion([low, keep], 64, 64, agnostic=False)

    # The 0.7 detection survives; the 0.02 one is filtered out by
    # SAM3_WBF_SKIP_THRESHOLD before fusion happens. Only one detection
    # comes back, and it was contributed by exactly one input (the keeper).
    assert len(fused) == 1
    assert fused[0]["wbf_member_count"] == 1
    # The survivor's WBF score is rescaled by ratio of contributing weights
    # to the total weight sum across sources (standard WBF behaviour);
    # what matters here is that the 0.02 detection did NOT contribute.
    assert fused[0]["wbf_member_sources"] == ["dota_obb"]


def test_wbf_falls_back_to_nms_when_ensemble_boxes_missing(monkeypatch):
    """Force the ensemble_boxes import to fail; assert NMS fallback runs."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "ensemble_boxes" or name.startswith("ensemble_boxes."):
            raise ImportError("simulated absence of ensemble_boxes")
        return real_import(name, *args, **kwargs)

    a = _det(10, 10, 30, 30, 0.7, "ship", "sam3")
    b = _det(11, 11, 31, 31, 0.9, "ship", "dota_obb")

    monkeypatch.setattr(builtins, "__import__", fake_import)
    fused = fusion.wbf_fusion([a, b], 64, 64, agnostic=True)

    # NMS keeps exactly one (highest-conf survivor for the overlap pair).
    assert len(fused) == 1
    # NMS does NOT stamp wbf_member_count — so its absence proves we
    # actually fell back to mask_aware_nms.
    assert "wbf_member_count" not in fused[0]


def test_fuse_detections_mode_nms_uses_existing_nms(monkeypatch):
    """SAM3_FUSION_MODE=nms routes fuse_detections through mask_aware_nms."""
    monkeypatch.setenv("SAM3_FUSION_MODE", "nms")
    a = _det(10, 10, 30, 30, 0.7, "ship", "sam3")
    b = _det(11, 11, 31, 31, 0.9, "ship", "dota_obb")

    with patch.object(fusion, "mask_aware_nms", wraps=fusion.mask_aware_nms) as spy_nms, \
         patch.object(fusion, "wbf_fusion", wraps=fusion.wbf_fusion) as spy_wbf:
        out = fusion.fuse_detections([a, b], image_w=64, image_h=64, agnostic=True)
        assert spy_nms.call_count == 1
        assert spy_wbf.call_count == 0
    assert len(out) == 1


def test_fuse_detections_mode_wbf_is_default(monkeypatch):
    """When SAM3_FUSION_MODE is unset, the WBF path is selected."""
    monkeypatch.delenv("SAM3_FUSION_MODE", raising=False)
    a = _det(10, 10, 30, 30, 0.7, "ship", "sam3")
    b = _det(11, 11, 31, 31, 0.9, "ship", "dota_obb")

    with patch.object(fusion, "wbf_fusion", wraps=fusion.wbf_fusion) as spy_wbf:
        out = fusion.fuse_detections([a, b], image_w=64, image_h=64, agnostic=True)
        assert spy_wbf.call_count == 1
    assert len(out) == 1
    # WBF stamps the member fields.
    assert out[0]["wbf_member_count"] == 2


def test_wbf_per_model_weights_env_override(monkeypatch):
    """SAM3_WBF_WEIGHTS JSON overrides merge on top of defaults."""
    monkeypatch.setenv("SAM3_WBF_WEIGHTS", '{"grounding_dino": 0.9, "novel_layer": 0.42}')

    merged = fusion._wbf_weights()
    assert merged["grounding_dino"] == pytest.approx(0.9)
    assert merged["novel_layer"] == pytest.approx(0.42)
    # Defaults for un-overridden layers stay intact.
    assert merged["dota_obb"] == pytest.approx(1.0)
    assert merged["sam3"] == pytest.approx(0.5)


def test_wbf_per_model_weights_env_invalid_json_falls_back(monkeypatch):
    monkeypatch.setenv("SAM3_WBF_WEIGHTS", "{not valid json}")
    merged = fusion._wbf_weights()
    # Returns clean default copy when JSON is malformed.
    assert merged == fusion._DEFAULT_WBF_WEIGHTS

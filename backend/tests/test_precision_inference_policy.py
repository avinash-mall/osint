from __future__ import annotations

import detection_policy
from worker import _calibration_tag_for_detection


def test_precision_policy_has_nonzero_default_floor(monkeypatch):
    monkeypatch.delenv("GLOBAL_CONFIDENCE_FLOOR", raising=False)
    monkeypatch.delenv("DETECTION_THRESHOLD_PROFILE", raising=False)
    monkeypatch.setattr(detection_policy, "_load_db_overrides", lambda: ({}, None, None))
    detection_policy.invalidate_policy_cache()

    policy = detection_policy.active_detection_policy()

    assert policy["threshold_profile"] == "defence_precision"
    assert policy["global_confidence_floor"] > 0.0

    detection_policy.invalidate_policy_cache()


def test_calibration_tag_uses_source_layer_not_model_version():
    det = {
        "source_layer": "dota_obb",
        "model_version": "sam3-image+sam3-video",
    }

    assert _calibration_tag_for_detection(det) == "dota_obb"
    assert _calibration_tag_for_detection({"model_version": "sam3"}) == ""


def test_dota_obb_generic_plane_resolves_to_generic_display_label():
    """End-to-end check for Task 1.2: a DOTA-OBB generic ``plane`` detection
    that the ontology tie-broke to ``Fighter Aircraft`` must surface as
    ``Aircraft (generic)`` instead of the fabricated specific label.

    See docs/decisions/why-generic-labels-when-unverified.md.
    """
    from dataclasses import dataclass

    @dataclass
    class _Norm:
        canonical_label: str
        parent_class: str

    det = {
        "source_layer": "dota_obb",
        "original_class": "plane",
        "parent_class": "aircraft",
        "semantic_margin": 0.0,
    }
    ont = _Norm(canonical_label="Fighter Aircraft", parent_class="aircraft")
    label, quality = detection_policy.display_label_for(det, ont)
    assert quality == "generic"
    assert label == "Aircraft (generic)"
    assert "Fighter" not in label  # the fabrication MUST be suppressed

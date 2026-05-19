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

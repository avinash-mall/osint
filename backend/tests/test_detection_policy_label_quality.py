"""Unit tests for the Task 1.2 label-quality helpers.

These cover the three branches of ``label_quality_for`` /
``display_label_for`` plus the ``LABEL_VERIFIER_MARGIN_FLOOR`` env override
and the DOTA-OBB generic-class normalisation table.

See docs/decisions/why-generic-labels-when-unverified.md.
"""
from __future__ import annotations

from dataclasses import dataclass

import detection_policy


@dataclass
class _FakeNormalized:
    """Minimal stand-in for ``backend.ontology.NormalizedLabel``."""
    canonical_label: str = ""
    parent_class: str = ""


# ---------------------------------------------------------------------------
# DOTA_OBB_GENERIC_CLASSES — normalisation table
# ---------------------------------------------------------------------------
def test_dota_obb_generic_classes_contains_all_18_labels() -> None:
    expected = {
        "plane", "ship", "storage_tank", "baseball_diamond", "tennis_court",
        "basketball_court", "ground_track_field", "harbor", "bridge",
        "large_vehicle", "small_vehicle", "helicopter", "roundabout",
        "soccer_ball_field", "swimming_pool", "container_crane", "airport",
        "helipad",
    }
    assert detection_policy.DOTA_OBB_GENERIC_CLASSES == frozenset(expected)


def test_dota_obb_generic_classes_normalisation_is_case_and_space_insensitive() -> None:
    # The frozenset itself is normalised; the consumer uses normalize_label()
    # on the detection input so "Large Vehicle" or "LARGE  VEHICLE" both hit.
    assert detection_policy.normalize_label("Large Vehicle") in detection_policy.DOTA_OBB_GENERIC_CLASSES
    assert detection_policy.normalize_label("LARGE  VEHICLE") in detection_policy.DOTA_OBB_GENERIC_CLASSES
    assert detection_policy.normalize_label("storage-tank") in detection_policy.DOTA_OBB_GENERIC_CLASSES


# ---------------------------------------------------------------------------
# label_quality_for — three branches + env override
# ---------------------------------------------------------------------------
def test_label_quality_for_verified_when_semantic_margin_above_floor(monkeypatch) -> None:
    monkeypatch.delenv("LABEL_VERIFIER_MARGIN_FLOOR", raising=False)
    det = {"source_layer": "dota_obb", "original_class": "plane", "semantic_margin": 0.42}
    assert detection_policy.label_quality_for(det) == "verified"


def test_label_quality_for_generic_when_dota_obb_generic_and_unverified() -> None:
    det = {"source_layer": "dota_obb", "original_class": "plane", "semantic_margin": 0.0}
    assert detection_policy.label_quality_for(det) == "generic"


def test_label_quality_for_inferred_when_sam3_text_prompt_unverified() -> None:
    det = {"source_layer": "sam3", "original_class": "fighter aircraft", "semantic_margin": 0.0}
    assert detection_policy.label_quality_for(det) == "inferred"


def test_label_quality_for_inferred_when_dota_obb_label_not_in_generic_set() -> None:
    # DOTA-OBB layer but a custom label (shouldn't happen in practice, but
    # the helper must not assume every dota_obb row is generic).
    det = {"source_layer": "dota_obb", "original_class": "su27_flanker", "semantic_margin": 0.0}
    assert detection_policy.label_quality_for(det) == "inferred"


def test_label_quality_for_env_override_changes_verifier_floor(monkeypatch) -> None:
    monkeypatch.setenv("LABEL_VERIFIER_MARGIN_FLOOR", "0.50")
    # 0.40 is now BELOW the floor → no longer verified.
    det = {"source_layer": "dota_obb", "original_class": "plane", "semantic_margin": 0.40}
    assert detection_policy.label_quality_for(det) == "generic"
    # 0.60 still verified.
    det = {"source_layer": "dota_obb", "original_class": "plane", "semantic_margin": 0.60}
    assert detection_policy.label_quality_for(det) == "verified"


def test_label_quality_for_safe_with_missing_fields() -> None:
    assert detection_policy.label_quality_for({}) == "inferred"
    # Non-dict inputs degrade safely.
    assert detection_policy.label_quality_for(None) == "inferred"  # type: ignore[arg-type]


def test_label_quality_for_handles_non_numeric_semantic_margin() -> None:
    det = {"source_layer": "dota_obb", "original_class": "plane", "semantic_margin": "n/a"}
    assert detection_policy.label_quality_for(det) == "generic"


def test_label_quality_for_case_insensitive_source_layer() -> None:
    det = {"source_layer": "DOTA_OBB", "original_class": "plane", "semantic_margin": 0.0}
    assert detection_policy.label_quality_for(det) == "generic"


# ---------------------------------------------------------------------------
# display_label_for — three branches
# ---------------------------------------------------------------------------
def test_display_label_for_verified_uses_canonical_label() -> None:
    det = {"source_layer": "dota_obb", "original_class": "plane", "semantic_margin": 0.42}
    ont = _FakeNormalized(canonical_label="Fighter Aircraft", parent_class="aircraft")
    label, quality = detection_policy.display_label_for(det, ont)
    assert quality == "verified"
    assert label == "Fighter Aircraft"


def test_display_label_for_verified_falls_back_to_original_when_canonical_missing() -> None:
    det = {"source_layer": "dota_obb", "original_class": "plane", "semantic_margin": 0.42}
    ont = _FakeNormalized(canonical_label="", parent_class="aircraft")
    label, quality = detection_policy.display_label_for(det, ont)
    assert quality == "verified"
    assert label == "plane"


def test_display_label_for_generic_renders_parent_with_generic_suffix() -> None:
    det = {
        "source_layer": "dota_obb",
        "original_class": "plane",
        "parent_class": "aircraft",
        "semantic_margin": 0.0,
    }
    ont = _FakeNormalized(canonical_label="Fighter Aircraft", parent_class="aircraft")
    label, quality = detection_policy.display_label_for(det, ont)
    assert quality == "generic"
    # MUST NOT promote to "Fighter Aircraft" — that's the fabrication we kill.
    assert label == "Aircraft (generic)"
    assert "Fighter" not in label


def test_display_label_for_generic_vehicle_label() -> None:
    det = {
        "source_layer": "dota_obb",
        "original_class": "large vehicle",
        "parent_class": "vehicle",
        "semantic_margin": 0.0,
    }
    ont = _FakeNormalized(canonical_label="Main Battle Tank", parent_class="vehicle")
    label, quality = detection_policy.display_label_for(det, ont)
    assert quality == "generic"
    assert label == "Vehicle (generic)"


def test_display_label_for_generic_falls_back_to_original_when_no_parent() -> None:
    det = {"source_layer": "dota_obb", "original_class": "swimming pool", "semantic_margin": 0.0}
    ont = _FakeNormalized(canonical_label="", parent_class="")
    label, quality = detection_policy.display_label_for(det, ont)
    assert quality == "generic"
    assert label == "Swimming Pool"


def test_display_label_for_inferred_prefers_canonical_label() -> None:
    det = {"source_layer": "sam3", "original_class": "fighter_aircraft", "semantic_margin": 0.0}
    ont = _FakeNormalized(canonical_label="Fighter Aircraft", parent_class="aircraft")
    label, quality = detection_policy.display_label_for(det, ont)
    assert quality == "inferred"
    assert label == "Fighter Aircraft"


def test_display_label_for_inferred_falls_back_to_parent_then_original() -> None:
    det = {"source_layer": "sam3", "original_class": "foo_bar", "semantic_margin": 0.0}
    ont = _FakeNormalized(canonical_label="", parent_class="other")
    label, quality = detection_policy.display_label_for(det, ont)
    assert quality == "inferred"
    assert label == "Other"

    det2 = {"source_layer": "sam3", "original_class": "foo_bar", "semantic_margin": 0.0}
    ont2 = _FakeNormalized(canonical_label="", parent_class="")
    label2, quality2 = detection_policy.display_label_for(det2, ont2)
    assert quality2 == "inferred"
    assert label2 == "Foo Bar"


def test_display_label_for_safe_with_none_normalized() -> None:
    det = {"source_layer": "sam3", "original_class": "tank", "semantic_margin": 0.0}
    label, quality = detection_policy.display_label_for(det, None)
    assert quality == "inferred"
    assert label == "Tank"

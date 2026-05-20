from __future__ import annotations

from detection_evidence import apply_evidence_ranking, validate_physics


def _det(**extra):
    return {
        "class": "ship",
        "original_class": "ship",
        "parent_class": "vessel",
        "confidence": 0.86,
        "source_layer": "dota_obb",
        "pixel_bbox": [0, 0, 80, 20],
        "pixel_obb": [0, 0, 80, 0, 80, 20, 0, 20],
        "area": 1200,
        "chip_valid_fraction": 0.95,
        "size_estimate": {"length_m": 60.0, "width_m": 15.0, "area_m2": 900.0},
        **extra,
    }


def test_specialist_valid_detection_can_confirm():
    det = apply_evidence_ranking(_det())
    assert det["evidence_tier"] == "confirmed"
    assert det["review_status"] == "high_confidence"
    assert det["validator_results"]["passed"] is True
    assert det["member_sources"] == ["dota_obb"]


def test_unknown_open_vocab_single_source_stays_discovery():
    det = apply_evidence_ranking(
        _det(
            **{"class": "unknown"},
            original_class="camouflaged_launcher",
            parent_class="camouflaged_launcher",
            source_layer="sam3",
            confidence=0.88,
        ),
        ontology_unknown=True,
    )
    assert det["evidence_tier"] == "discovery"
    assert det["review_status"] == "review_candidate"


def test_multi_source_and_verifier_promote_open_vocab_candidate():
    det = apply_evidence_ranking(
        _det(
            source_layer="sam3",
            wbf_member_sources=["sam3", "grounding_dino"],
            semantic_verifier={"enabled": True, "passed": True, "semantic_margin": 0.2},
        ),
        ontology_unknown=True,
    )
    assert det["evidence_tier"] == "confirmed"
    assert det["semantic_margin"] == 0.2
    assert det["member_sources"] == ["grounding_dino", "sam3"]


def test_sar_proxy_cannot_confirm_without_cfar():
    det = apply_evidence_ranking(
        _det(modality="sar", source_layer="sam3", sar_proxy=True, confidence=0.9)
    )
    assert det["evidence_tier"] in {"candidate", "discovery"}
    assert det["evidence_tier"] != "confirmed"
    assert "sar_synthetic_proxy" in det["validator_results"]["warnings"]


def test_physical_validator_flags_implausible_size():
    result = validate_physics(_det(size_estimate={"length_m": 2000.0, "width_m": 300.0}))
    assert result["passed"] is False
    assert "too_large_for_class" in result["failures"]

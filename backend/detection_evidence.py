"""Evidence ranking and physical sanity checks for imagery detections."""
from __future__ import annotations

import math
import os
from typing import Any


SPECIALIST_SOURCES = {"dota_obb", "sar_cfar"}
OPEN_VOCAB_SOURCES = {"sam3", "yoloe"}

DEFAULT_SIZE_LIMITS_M: dict[str, tuple[float, float]] = {
    "aircraft": (3.0, 90.0),
    "plane": (3.0, 90.0),
    "helicopter": (2.0, 40.0),
    "vessel": (2.0, 450.0),
    "ship": (2.0, 450.0),
    "vehicle": (1.0, 40.0),
    "small_vehicle": (1.0, 12.0),
    "large_vehicle": (4.0, 40.0),
    "storage_tank": (3.0, 120.0),
    "bridge": (5.0, 5000.0),
    "building": (2.0, 1000.0),
}


def apply_evidence_ranking(det: dict[str, Any], *, ontology_unknown: bool = False) -> dict[str, Any]:
    """Mutate and return ``det`` with evidence fields.

    The function preserves every detection. Weak or novel rows are demoted to
    ``candidate`` / ``discovery`` tiers instead of being deleted, which keeps
    the open-vocabulary workflow intact while making confirmed detections
    harder to earn.
    """
    validators = validate_physics(det)
    member_sources = _member_sources(det)
    semantic_margin = _semantic_margin(det)
    verifier_passed = _verifier_passed(det)
    confidence = _float(det.get("confidence"), 0.0)
    source = str(det.get("source_layer") or "").strip().lower()
    modality = str(det.get("modality") or "").strip().lower()
    sar_proxy = bool(det.get("sar_proxy")) or (modality == "sar" and source != "sar_cfar")

    score = 0.45 * confidence
    if source in SPECIALIST_SOURCES:
        score += 0.25
    elif source in OPEN_VOCAB_SOURCES:
        score += 0.06
    if len(member_sources) >= 2:
        score += 0.18
    if verifier_passed:
        score += 0.16
    if semantic_margin is not None:
        score += min(0.12, max(0.0, semantic_margin))
    if validators["passed"]:
        score += 0.10
    else:
        score -= min(0.25, 0.08 * len(validators["failures"]))
    if bool(det.get("edge_truncated")):
        score -= 0.05
    if sar_proxy:
        score -= 0.15

    score = max(0.0, min(1.0, score))
    tier = _tier_for(
        score=score,
        source=source,
        member_sources=member_sources,
        ontology_unknown=ontology_unknown,
        verifier_passed=verifier_passed,
        validators_passed=validators["passed"],
        sar_proxy=sar_proxy,
    )

    det["evidence_score"] = round(score, 4)
    det["evidence_tier"] = tier
    det["member_sources"] = member_sources
    det["semantic_margin"] = semantic_margin
    det["validator_results"] = validators
    det["reject_reasons"] = list(validators["failures"])
    if tier == "confirmed":
        det["review_status"] = "high_confidence"
    elif tier == "discovery":
        det["review_status"] = "review_candidate"
    return det


def validate_physics(det: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []

    bbox = det.get("pixel_bbox") or []
    if len(bbox) >= 4:
        x1, y1, x2, y2 = [_float(value, 0.0) for value in bbox[:4]]
        bw = max(0.0, x2 - x1)
        bh = max(0.0, y2 - y1)
        if bw <= 0 or bh <= 0:
            failures.append("empty_pixel_bbox")
        else:
            aspect = max(bw / max(bh, 1e-6), bh / max(bw, 1e-6))
            if aspect > float(os.getenv("EVIDENCE_MAX_ASPECT_RATIO", "35")):
                failures.append("extreme_aspect_ratio")
            mask_area = _float(det.get("area"), 0.0)
            if mask_area > 0:
                compactness = mask_area / max(1.0, bw * bh)
                if compactness < float(os.getenv("EVIDENCE_MIN_MASK_COMPACTNESS", "0.015")):
                    warnings.append("low_mask_compactness")
    else:
        warnings.append("missing_pixel_bbox")

    valid_fraction = det.get("chip_valid_fraction")
    if valid_fraction is not None and _float(valid_fraction, 1.0) < float(os.getenv("EVIDENCE_MIN_VALID_FRACTION", "0.20")):
        failures.append("low_valid_fraction")

    size = det.get("size_estimate") or {}
    label_keys = _label_keys(det)
    length = _float(size.get("length_m"), 0.0)
    width = _float(size.get("width_m"), 0.0)
    if length > 0 or width > 0:
        longest = max(length, width)
        limits = _size_limits_for(label_keys)
        if limits is not None:
            min_m, max_m = limits
            if longest < min_m:
                failures.append("too_small_for_class")
            if longest > max_m:
                failures.append("too_large_for_class")

    if bool(det.get("edge_truncated")):
        warnings.append("edge_truncated")
    if str(det.get("modality") or "").lower() == "sar" and det.get("source_layer") != "sar_cfar":
        warnings.append("sar_synthetic_proxy")

    return {"passed": not failures, "failures": failures, "warnings": warnings}


def _tier_for(
    *,
    score: float,
    source: str,
    member_sources: list[str],
    ontology_unknown: bool,
    verifier_passed: bool,
    validators_passed: bool,
    sar_proxy: bool,
) -> str:
    if sar_proxy:
        return "candidate" if score >= 0.45 and validators_passed else "discovery"
    if ontology_unknown and source in OPEN_VOCAB_SOURCES and len(member_sources) < 2 and not verifier_passed:
        return "discovery"
    if score >= 0.72 and validators_passed:
        return "confirmed"
    if score >= 0.42:
        return "candidate"
    return "discovery"


def _member_sources(det: dict[str, Any]) -> list[str]:
    raw = det.get("wbf_member_sources") or det.get("member_sources")
    sources: list[str] = []
    if isinstance(raw, list):
        sources.extend(str(item).strip().lower() for item in raw if str(item).strip())
    source = str(det.get("source_layer") or "").strip().lower()
    if source:
        sources.append(source)
    return sorted(set(sources))


def _semantic_margin(det: dict[str, Any]) -> float | None:
    direct = det.get("semantic_margin")
    if direct is not None:
        return _float(direct, 0.0)
    verifier = det.get("semantic_verifier") or {}
    if isinstance(verifier, dict) and verifier.get("semantic_margin") is not None:
        return _float(verifier.get("semantic_margin"), 0.0)
    return None


def _verifier_passed(det: dict[str, Any]) -> bool:
    verifier = det.get("semantic_verifier") or {}
    return isinstance(verifier, dict) and bool(verifier.get("enabled")) and bool(verifier.get("passed"))


def _label_keys(det: dict[str, Any]) -> tuple[str, ...]:
    labels = (
        det.get("parent_class"),
        det.get("class"),
        det.get("original_class"),
    )
    return tuple(_norm(label) for label in labels if _norm(label))


def _size_limits_for(labels: tuple[str, ...]) -> tuple[float, float] | None:
    for label in labels:
        if label in DEFAULT_SIZE_LIMITS_M:
            return DEFAULT_SIZE_LIMITS_M[label]
        for key, value in DEFAULT_SIZE_LIMITS_M.items():
            if key in label:
                return value
    return None


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _float(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default

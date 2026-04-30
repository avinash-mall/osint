from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from detection_policy import parent_class_for_label


THREAT_LEVELS = {"low", "medium", "high", "critical"}

CRITICAL_TERMS = (
    "tank",
    "artillery",
    "missile",
    "launcher",
    "rocket",
    "sam",
    "air defense",
    "air-defence",
    "air defence",
    "destroyer",
    "battleship",
    "warship",
    "frigate",
    "corvette",
)

HIGH_TERMS = (
    "fighter",
    "bomber",
    "military aircraft",
    "helicopter",
    "submarine",
    "aircraft carrier",
    "naval vessel",
    "runway",
    "airstrip",
    "airfield",
    "military",
    "radar",
)

MEDIUM_TERMS = (
    "convoy",
    "armored",
    "armoured",
    "command",
    "depot",
    "hangar",
    "bunker",
    "checkpoint",
    "storage tank",
    "oil facility",
    "border",
)

MARITIME_TERMS = ("ship", "vessel", "harbor", "harbour", "port", "dry dock", "maritime")
AIR_TERMS = ("aircraft", "plane", "helicopter", "fighter", "airport", "runway", "airstrip", "airfield")
GROUND_TERMS = ("vehicle", "truck", "car", "van", "bus", "convoy", "armored", "armoured")
INFRA_TERMS = ("facility", "building", "storage", "depot", "plant", "hangar", "bridge", "checkpoint")


@dataclass(frozen=True)
class ThreatAssessment:
    threat_level: str
    threat_confidence: float
    assessment_status: str
    evidence: list[str]
    category: str


def clean_detection_class(det_class: str) -> str:
    label = (det_class or "Unknown").replace("_", " ").replace("-", " ").strip()
    prefixes = ("xview ", "dota ", "fair1m ", "fmow ", "rareplanes ", "dior ", "sodaa ", "hrsc ")
    lower = label.lower()
    for prefix in prefixes:
        if lower.startswith(prefix):
            label = label[len(prefix):]
            break
    return " ".join(part.capitalize() for part in label.split()) or "Unknown"


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def category_for_class(det_class: str) -> str:
    parent_class = parent_class_for_label(det_class)
    if parent_class == "aircraft":
        return "air"
    if parent_class in {"ship", "harbor"}:
        return "maritime"
    if parent_class in {"vehicle", "military_vehicle"}:
        return "combat" if parent_class == "military_vehicle" else "ground"
    if parent_class in {"storage_tank", "building", "bridge", "airfield", "infrastructure"}:
        return "infrastructure"
    text = clean_detection_class(det_class).lower()
    if _contains_any(text, CRITICAL_TERMS):
        return "combat"
    if _contains_any(text, AIR_TERMS):
        return "air"
    if _contains_any(text, MARITIME_TERMS):
        return "maritime"
    if _contains_any(text, GROUND_TERMS):
        return "ground"
    if _contains_any(text, INFRA_TERMS):
        return "infrastructure"
    return "unknown"


def assess_detection_threat(
    det_class: str,
    confidence: float | int | None = 0,
    allegiance: str | None = None,
    recurrence_count: int = 1,
) -> dict[str, Any]:
    """Conservative deterministic threat assessment for defence workflows.

    This intentionally treats generic classes as low until stronger evidence is
    available. LLM-generated fields must not override this result.
    """
    label = clean_detection_class(det_class)
    text = label.lower()
    try:
        conf = max(0.0, min(1.0, float(confidence or 0)))
    except (TypeError, ValueError):
        conf = 0.0

    category = category_for_class(det_class)
    evidence = [f"class={label}", f"confidence={conf:.2f}"]
    status = "rule_assessed" if conf >= 0.35 else "unconfirmed"
    level = "low"
    score = 0.2 if conf >= 0.35 else 0.1

    hostile_tag = str(allegiance or "").lower() == "hostile"
    if hostile_tag:
        evidence.append("analyst_tag=hostile")

    if _contains_any(text, CRITICAL_TERMS):
        evidence.append("critical_defence_class")
        if conf >= 0.65:
            level, score = "critical", 0.9
        elif conf >= 0.35:
            level, score = "medium", 0.55
            evidence.append("low_confidence_combat_downgrade")
        else:
            evidence.append("insufficient_confidence")
    elif _contains_any(text, HIGH_TERMS):
        evidence.append("high_interest_defence_class")
        if conf >= 0.7:
            level, score = "high", 0.75
        elif conf >= 0.45:
            level, score = "medium", 0.5
        else:
            evidence.append("insufficient_confidence")
    elif _contains_any(text, MEDIUM_TERMS):
        evidence.append("medium_interest_context")
        if conf >= 0.6:
            level, score = "medium", 0.48
        else:
            evidence.append("insufficient_confidence")
    else:
        evidence.append("generic_or_uncorroborated_class")

    if hostile_tag and conf >= 0.7:
        if level == "critical":
            score = max(score, 0.95)
        elif _contains_any(text, CRITICAL_TERMS + HIGH_TERMS):
            level, score = "high", max(score, 0.8)
        else:
            level, score = "medium", max(score, 0.6)

    if recurrence_count >= 3 and conf >= 0.55 and level == "low":
        level, score = "medium", max(score, 0.5)
        evidence.append(f"recurrence_count={recurrence_count}")

    assessment = ThreatAssessment(
        threat_level=level,
        threat_confidence=round(score, 3),
        assessment_status=status,
        evidence=evidence[:8],
        category=category,
    )
    return asdict(assessment)


def conservative_detection_ontology(
    det_class: str,
    confidence: float | int | None = 0,
    allegiance: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    label = clean_detection_class(det_class)
    assessment = assess_detection_threat(det_class, confidence=confidence, allegiance=allegiance)
    return {
        "label": label,
        "domain": "GEOINT",
        "category": assessment["category"],
        "threat_level": assessment["threat_level"],
        "threat_confidence": assessment["threat_confidence"],
        "assessment_status": assessment["assessment_status"],
        "evidence": assessment["evidence"],
        "description": description or "Deterministic defence threat assessment; LLM text is advisory only.",
        "recommended_filter": label,
        "generated_by": "deterministic-threat-rules",
        "status": "rule_assessed",
    }

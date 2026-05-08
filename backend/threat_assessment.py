"""Object-class assessment helpers (open-vocabulary, neutral framing).

This module previously implemented a defence-oriented threat assessor that
flagged tanks/missiles/warships at HIGH/CRITICAL severity. The project is now
open-vocabulary across civilian, commercial, and natural object classes —
threat scoring is no longer the right framing.

The module preserves its public API (``assess_detection_threat``,
``conservative_detection_ontology``, ``category_for_class``,
``clean_detection_class``) so existing callers in ``backend/worker.py``,
``backend/main.py`` and ``backend/tracker.py`` keep working without edits.

What the functions return now:

* ``category_for_class`` — coarse semantic bucket (``air``, ``maritime``,
  ``ground``, ``infrastructure``, ``person``, ``animal``, ``vegetation``,
  ``water``, ``object``). No "combat" bucket.
* ``assess_detection_threat`` — always returns ``threat_level="unrated"``
  and ``threat_confidence=0.0``. The ``evidence`` array still records the
  cleaned label and confidence so audit trails stay intact.
* ``conservative_detection_ontology`` — returns the same shape as before
  with a neutral ``description``.

If a downstream operator genuinely needs a defence-oriented severity score
they can re-implement that as a separate, opt-in policy outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from detection_policy import parent_class_for_label


THREAT_LEVELS = {"unrated", "low", "medium", "high", "critical"}


# ── Coarse semantic buckets used by category_for_class ─────────────────────
_AIR_PARENTS         = {"aircraft", "airfield"}
_MARITIME_PARENTS    = {"vessel", "harbor"}
_GROUND_PARENTS      = {"vehicle", "train"}
_INFRA_PARENTS       = {
    "storage_tank", "building", "bridge", "infrastructure",
}
_NATURE_PARENTS = {"vegetation", "water", "plant"}
_PEOPLE_PARENTS = {"person"}
_ANIMAL_PARENTS = {"animal"}


@dataclass(frozen=True)
class ThreatAssessment:
    threat_level: str
    threat_confidence: float
    assessment_status: str
    evidence: list[str]
    category: str


def clean_detection_class(det_class: str) -> str:
    label = (det_class or "Object").replace("_", " ").replace("-", " ").strip()
    prefixes = (
        "xview ", "dota ", "fair1m ", "fmow ", "rareplanes ",
        "dior ", "sodaa ", "hrsc ", "lvis ", "coco ", "objects365 ",
    )
    lower = label.lower()
    for prefix in prefixes:
        if lower.startswith(prefix):
            label = label[len(prefix):]
            break
    return " ".join(part.capitalize() for part in label.split()) or "Object"


def category_for_class(det_class: str) -> str:
    """Map a detection class to a coarse semantic bucket — no military bucket."""
    parent = parent_class_for_label(det_class)
    if parent in _AIR_PARENTS:      return "air"
    if parent in _MARITIME_PARENTS: return "maritime"
    if parent in _GROUND_PARENTS:   return "ground"
    if parent in _INFRA_PARENTS:    return "infrastructure"
    if parent in _NATURE_PARENTS:   return "nature"
    if parent in _PEOPLE_PARENTS:   return "person"
    if parent in _ANIMAL_PARENTS:   return "animal"
    if parent == "recreation":      return "recreation"
    if parent in {"segment", "track"}: return parent
    return "object"


def assess_detection_threat(
    det_class: str,
    confidence: float | int | None = 0,
    allegiance: str | None = None,
    recurrence_count: int = 1,
) -> dict[str, Any]:
    """Open-vocab assessment: every detection is ``unrated``.

    The function preserves the legacy return shape so callers that read
    ``threat_level`` / ``threat_confidence`` / ``evidence`` / ``category``
    still work. ``allegiance`` and ``recurrence_count`` are recorded in the
    evidence trail for audit but no longer raise the level.
    """
    label = clean_detection_class(det_class)
    try:
        conf = max(0.0, min(1.0, float(confidence or 0)))
    except (TypeError, ValueError):
        conf = 0.0

    evidence = [f"class={label}", f"confidence={conf:.2f}"]
    if allegiance:
        evidence.append(f"allegiance={str(allegiance).lower()}")
    if recurrence_count and recurrence_count > 1:
        evidence.append(f"recurrence_count={int(recurrence_count)}")

    assessment = ThreatAssessment(
        threat_level="unrated",
        threat_confidence=0.0,
        assessment_status="unrated",
        evidence=evidence[:8],
        category=category_for_class(det_class),
    )
    return asdict(assessment)


def conservative_detection_ontology(
    det_class: str,
    confidence: float | int | None = 0,
    allegiance: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Compatibility shim — returns the previous shape with neutral defaults."""
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
        "description": description or "Open-vocabulary detection; threat scoring is unrated.",
        "recommended_filter": label,
        "generated_by": "open-vocab-rules",
        "status": "unrated",
    }

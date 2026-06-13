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

import logging
import threading
import time
from dataclasses import dataclass, asdict
from typing import Any

from detection_policy import parent_class_for_label


logger = logging.getLogger(__name__)

THREAT_LEVELS = {"unrated", "low", "medium", "high", "critical"}

# ── Thread-safe TTL cache for threat-rule lookups ───────────────────────────
# A single worker pass can run thousands of lookups; a per-row DB round-trip
# is wasteful when the rule table changes on the order of minutes. Cache hits
# expire after _TTL_SECONDS and on-demand via clear_threat_rule_cache().
_TTL_SECONDS = 60.0
_cache_lock = threading.Lock()
_cache: dict[tuple[str | None, str | None, str | None], tuple[float, dict[str, Any] | None]] = {}


def clear_threat_rule_cache() -> None:
    """Invalidate the in-process threat-rule cache (call after rule edits)."""
    with _cache_lock:
        _cache.clear()


def _lookup_threat_rule(
    det_class: str,
    category: str,
    allegiance: str | None,
) -> dict[str, Any] | None:
    key = (
        (det_class or "").strip().lower() or None,
        (category or "").strip().lower() or None,
        (allegiance or "").strip().lower() or None,
    )
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < _TTL_SECONDS:
            return hit[1]
    result = _lookup_threat_rule_uncached(*key)
    with _cache_lock:
        _cache[key] = (now, result)
    return result


def _lookup_threat_rule_uncached(
    det_class: str | None,
    category: str | None,
    allegiance: str | None,
) -> dict[str, Any] | None:
    """Phase 6.25: query the ``threat_rules`` table for a match.

    Match precedence: class+allegiance > class > category+allegiance >
    category > allegiance-only. Returns the matching row's outcome
    (``threat_level``, ``threat_confidence``, ``rationale``) or ``None``
    when no rule matches / the DB is unreachable / the table predates this
    install.

    The table is intentionally optional — defence operators opt-in by
    populating it; everyone else stays on the open-vocab "unrated" default.

    Inputs are already lower-cased / None-normalised by the caching wrapper.
    """
    cleaned_class = det_class
    cleaned_cat = category
    cleaned_alle = allegiance
    try:
        from database import postgis_db
        with postgis_db.get_cursor() as cur:
            # Score each candidate rule by specificity, pick the highest.
            cur.execute(
                """
                SELECT class, category, allegiance, threat_level, threat_confidence, rationale,
                       (CASE WHEN lower(class) = %s THEN 4 ELSE 0 END
                        + CASE WHEN lower(category) = %s THEN 2 ELSE 0 END
                        + CASE WHEN lower(allegiance) = %s THEN 1 ELSE 0 END) AS specificity
                FROM threat_rules
                WHERE enabled = TRUE
                  AND (class IS NULL OR lower(class) = %s)
                  AND (category IS NULL OR lower(category) = %s)
                  AND (allegiance IS NULL OR lower(allegiance) = %s)
                ORDER BY specificity DESC, updated_at DESC
                LIMIT 1
                """,
                (cleaned_class, cleaned_cat, cleaned_alle,
                 cleaned_class, cleaned_cat, cleaned_alle),
            )
            row = cur.fetchone()
    except Exception as exc:
        logger.debug("threat_rules lookup unavailable: %s", exc)
        return None
    if not row:
        return None
    return dict(row) if not isinstance(row, dict) else row


# ── Coarse semantic buckets used by category_for_class ─────────────────────
# Primary mapping: ontology branch_id → category. The runtime parent_class is
# the object's own canonical label ("destroyer", "boeing_737", …), so matching
# on parent strings alone misses almost every seeded object; the branch the
# normalizer resolved is the stable categorical signal. Keys are the lowercased
# branch ids from backend/scripts/seeds/defenceOntology.seed.json. Branches not
# listed here (mixed or ambiguous ones) are deliberately unmapped and fall
# through to the parent-string sets below.
_BRANCH_CATEGORIES = {
    "airfield_aviation": "air",
    "naval_maritime": "maritime",
    "military_forces": "ground",
    "armored_vehicles": "ground",
    "artillery": "ground",
    "tactical_vehicles": "ground",
    "air_defense_ew": "ground",
    "sam_system": "ground",
    "logistics": "ground",
    "radar": "infrastructure",
    "electronic_warfare": "infrastructure",
    "missile_strategic": "infrastructure",
    "military_installations": "infrastructure",
    "fortifications_obstacles": "infrastructure",
    "activity_change": "infrastructure",
    "industrial_dual_use": "infrastructure",
    "transportation_terrain": "infrastructure",
    "urban_tactical": "infrastructure",
}

_AIR_PARENTS         = {"aircraft", "airfield"}
_MARITIME_PARENTS    = {"vessel", "ship", "harbor"}
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
    branch_id = ""
    try:
        from ontology import normalize as _normalize
        normalized = _normalize(det_class or "")
        parent = normalized.parent_class
        branch_id = (normalized.branch_id or "").strip().lower()
    except Exception:
        parent = parent_class_for_label(det_class)
    category = _BRANCH_CATEGORIES.get(branch_id)
    if category:
        return category
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

    category = category_for_class(det_class)

    # Phase 6.25: consult the configurable threat_rules table. The default
    # is still "unrated" (open-vocab), but a defence operator can populate
    # the table to elevate specific (class, category, allegiance) tuples
    # without redeploying code.
    rule = _lookup_threat_rule(det_class, category, allegiance)
    if rule and rule.get("threat_level") in THREAT_LEVELS:
        threat_level = str(rule["threat_level"])
        try:
            threat_conf = max(0.0, min(1.0, float(rule.get("threat_confidence") or 0.0)))
        except (TypeError, ValueError):
            threat_conf = 0.0
        if rule.get("rationale"):
            evidence.append(f"threat_rule={str(rule['rationale'])[:120]}")
        else:
            rule_keys = ",".join(
                f"{k}={rule.get(k)}" for k in ("class", "category", "allegiance")
                if rule.get(k) is not None
            )
            evidence.append(f"threat_rule_matched={rule_keys or 'wildcard'}")
        assessment = ThreatAssessment(
            threat_level=threat_level,
            threat_confidence=threat_conf,
            assessment_status="rule_matched" if threat_level != "unrated" else "unrated",
            evidence=evidence[:8],
            category=category,
        )
        return asdict(assessment)

    assessment = ThreatAssessment(
        threat_level="unrated",
        threat_confidence=0.0,
        assessment_status="unrated",
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


# Backwards-compatible alias. The thin wrappers that used to live in main.py
# and worker.py just forwarded a single positional argument here.
detection_ontology = conservative_detection_ontology

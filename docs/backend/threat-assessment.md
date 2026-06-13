# `backend/threat_assessment.py` — Open-Vocab Categorical Binning

**Path:** [backend/threat_assessment.py](../../backend/threat_assessment.py)
**Lines:** ~310
**Depends on:** [backend/database.py](../../backend/database.py), [backend/ontology.py](../../backend/ontology.py)

## Purpose

Bin a detection's class into one of a small category set (`air`, `maritime`, `ground`, `infrastructure`, `person`, `animal`, `vegetation`, `water`, `object`) and return a (currently neutral) threat level. Drives map UI icon + color choice.

## Why this design

- **Open-vocab neutral** — `threat_level` defaults to `"unrated"` for every detection. Automated threat assignment is out of scope without explicit operator-loaded rules. See [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md).
- **TTL-cached rule lookup** — `_lookup_threat_rule` reads from `threat_rules` (PostGIS), caches by `(class, sensor)` for 60 s. Without cache, every detection-emit call re-reads.
- **Category, not threat, is load-bearing** — UI uses category to pick an icon style; optional threat rules layer on top. The tracker also keys its V_MAX gates / Kalman process noise off the category, so a wrong bucket has kinematic consequences.
- **Branch-first category mapping** — `category_for_class` resolves via `ontology.normalize().branch_id` against `_BRANCH_CATEGORIES` (seed branch ids → category: `Naval_Maritime`→maritime, `Airfield_Aviation`→air, force/logistics branches→ground, installations/industrial/transport branches→infrastructure). The runtime `parent_class` is the object's own canonical label ("destroyer", "boeing_737"), so the legacy parent-string sets matched almost nothing; they remain only as a fallback for branches not present in `_BRANCH_CATEGORIES`. See [decisions/audit-fixes-backend-core-2026-06-11.md](../decisions/audit-fixes-backend-core-2026-06-11.md). (The former `Auxiliary`→nature mapping and the `Battle_Damage` fallback note were dropped when both branches were removed from the ontology — see [decisions/removed-battle-damage-and-aux-ontology-branches.md](../decisions/removed-battle-damage-and-aux-ontology-branches.md).)

## Key symbols

- [`clear_threat_rule_cache`](../../backend/threat_assessment.py#L52).
- [`_lookup_threat_rule`](../../backend/threat_assessment.py#L58) (cached) wraps [`_lookup_threat_rule_uncached`](../../backend/threat_assessment.py#L79).
- [`_BRANCH_CATEGORIES`](../../backend/threat_assessment.py#L138-L157) — lowercased seed branch id → category map.
- [`ThreatAssessment`](../../backend/threat_assessment.py#L170) — return dataclass.
- [`clean_detection_class`](../../backend/threat_assessment.py#L179) — strip/lower normalization.
- [`category_for_class`](../../backend/threat_assessment.py#L193) — branch-first bucket assignment with parent-string fallback.
- [`assess_detection_threat`](../../backend/threat_assessment.py#L218) — main entry.
- [`conservative_detection_ontology`](../../backend/threat_assessment.py#L283) — exposes the closed category set for the UI.

## Cross-references

- [backend/detection-policy.md](detection-policy.md) — `parent_class_for_label` complements `category_for_class`
- [backend/tracker-satellite.md](tracker-satellite.md) — `_tracker_category` consumes the buckets
- [decisions/audit-fixes-backend-core-2026-06-11.md](../decisions/audit-fixes-backend-core-2026-06-11.md)
- [decisions/removed-battle-damage-and-aux-ontology-branches.md](../decisions/removed-battle-damage-and-aux-ontology-branches.md) — `auxiliary`/`Battle_Damage` mappings dropped here
- Tests: [backend/tests/test_threat_category.py](../../backend/tests/test_threat_category.py)
- [frontend/utils-ontology-and-icons.md](../frontend/utils-ontology-and-icons.md)

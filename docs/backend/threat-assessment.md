# `backend/threat_assessment.py` — Open-Vocab Categorical Binning

**Path:** [backend/threat_assessment.py](../../backend/threat_assessment.py)
**Lines:** ~271
**Depends on:** [backend/database.py](../../backend/database.py), [backend/ontology.py](../../backend/ontology.py)

## Purpose

Bin a detection's class into one of a small category set (`air`, `maritime`, `ground`, `infrastructure`, `person`, `animal`, `vegetation`, `water`, `object`) and return a (currently neutral) threat level. Drives map UI icon + color choice.

## Why this design

- **Open-vocab neutral** — `threat_level` defaults to `"unrated"` for every detection. Automated threat assignment is out of scope without explicit operator-loaded rules. See [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md).
- **TTL-cached rule lookup** — `_lookup_threat_rule` reads from `threat_rules` (PostGIS), caches by `(class, sensor)` for 60 s. Without cache, every detection-emit call re-reads.
- **Category, not threat, is load-bearing** — UI uses category to pick an icon style; optional threat rules layer on top.

## Key symbols

- [`clear_threat_rule_cache`](../../backend/threat_assessment.py#L52).
- [`_lookup_threat_rule`](../../backend/threat_assessment.py#L58) (cached) wraps [`_lookup_threat_rule_uncached`](../../backend/threat_assessment.py#L79).
- [`ThreatAssessment`](../../backend/threat_assessment.py#L143) — return dataclass.
- [`clean_detection_class`](../../backend/threat_assessment.py#L151) — strip/lower normalization.
- [`category_for_class`](../../backend/threat_assessment.py#L165) — bucket assignment.
- [`assess_detection_threat`](../../backend/threat_assessment.py#L180) — main entry.
- [`conservative_detection_ontology`](../../backend/threat_assessment.py#L245) — exposes the closed category set for the UI.

## Cross-references

- [backend/detection-policy.md](detection-policy.md) — `parent_class_for_label` complements `category_for_class`
- [frontend/utils-ontology-and-icons.md](../frontend/utils-ontology-and-icons.md)

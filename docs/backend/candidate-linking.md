# `backend/candidate_linking.py` — Detection→Target Scoring

**Path:** [backend/candidate_linking.py](../../backend/candidate_linking.py)
**Lines:** ~135
**Depends on:** Pure Python; no DB.

## Purpose

Score a detection against an existing Neo4j Target. Used by `GET /api/detections/{id}/candidate-links` (in main.py) to produce a ranked list that operators approve/reject.

## Why this design

- **Pure scoring, no DB.** Targets are passed in as dicts of properties. This module is easy to unit-test ([backend/tests/test_debias_units.py](../../backend/tests/test_debias_units.py)) and reusable from scripts ([scripts/eval_candidate_links.py](../scripts/eval-runners.md)).
- **Composite score** combines spatial proximity, class compatibility, detection confidence, and a small history-anchor term (does this target have observations near this location lately?). Default weights: 30 / 30 / 30 / 10.
- **Deterministic.** No LLM in the loop today — the doc-string and the README explicitly flag "LLM-assisted ranking" as a roadmap item.

## Key symbols

- [`target_distance_m`](../../backend/candidate_linking.py#L16) — haversine in meters.
- [`_clean_detection_class`](../../backend/candidate_linking.py#L25), [`_category_hints`](../../backend/candidate_linking.py#L29).
- [`target_class_compatibility`](../../backend/candidate_linking.py#L43) — `(score, reason)` tuple.
- [`score_candidate_link`](../../backend/candidate_linking.py#L57) — main scorer.
- [`rank_candidate_links`](../../backend/candidate_linking.py#L102) — returns sorted candidates.

## Cross-references

- [operations/candidate-link-approval.md](../operations/candidate-link-approval.md)
- [scripts/eval-runners.md](../scripts/eval-runners.md)
- Tests: [backend/tests/test_debias_units.py](../../backend/tests/test_debias_units.py)

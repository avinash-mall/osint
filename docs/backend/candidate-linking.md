# `backend/candidate_linking.py` — Detection→Target Scoring

**Path:** [backend/candidate_linking.py](../../backend/candidate_linking.py)
**Lines:** ~135
**Depends on:** Pure Python; no DB.

## Purpose

Score a detection against an existing Neo4j Target. Used by `GET /api/detections/{id}/candidate-links` (main.py) → ranked list operators approve/reject.

## Why this design

- **Pure scoring, no DB** — Targets passed in as property dicts. Easy to unit-test ([backend/tests/test_debias_units.py](../../backend/tests/test_debias_units.py)), reusable from scripts ([scripts/eval_candidate_links.py](../scripts/eval-runners.md)).
- **Composite score** = spatial proximity + class compatibility + detection confidence + small history-anchor term (recent observations near this location?). Default weights 30 / 30 / 30 / 10.
- **Deterministic** — no LLM in the loop today; "LLM-assisted ranking" flagged as roadmap.

## Key symbols

- [`target_distance_m`](../../backend/candidate_linking.py#L16) — haversine, meters.
- [`_clean_detection_class`](../../backend/candidate_linking.py#L25), [`_category_hints`](../../backend/candidate_linking.py#L29).
- [`target_class_compatibility`](../../backend/candidate_linking.py#L43) — `(score, reason)` tuple.
- [`score_candidate_link`](../../backend/candidate_linking.py#L57) — main scorer.
- [`rank_candidate_links`](../../backend/candidate_linking.py#L102) — sorted candidates.

## Cross-references

- [operations/candidate-link-approval.md](../operations/candidate-link-approval.md)
- [scripts/eval-runners.md](../scripts/eval-runners.md)
- Tests: [backend/tests/test_debias_units.py](../../backend/tests/test_debias_units.py)

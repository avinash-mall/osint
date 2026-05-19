# Operations — Detection → Target Candidate Links

## What it does

For a detection in PostGIS, propose a ranked list of existing Neo4j Targets that this detection might be an observation of. The operator approves or rejects each candidate.

## Endpoints

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/api/detections/{id}/candidate-links` | Returns the ranked list |
| `POST` | `/api/detection-target-candidates/{id}/approve` | Creates Neo4j `(:Observation)-[:OBSERVED]->(:Target)` edge |
| `POST` | `/api/detection-target-candidates/{id}/reject` | Marks the candidate dismissed |

## Scoring

[backend/candidate-linking.md](../backend/candidate-linking.md) — deterministic, no LLM. Composite of:

- 30% spatial proximity (haversine in meters)
- 30% class compatibility (does this detection's `parent_class` match the Target's `class`?)
- 30% confidence (the detection's confidence)
- 10% history anchor (does this Target have nearby observations recently?)

## Why deterministic

Linking detections to Targets is consequential — it creates a graph edge that downstream automations may read. We want it explainable. An LLM ranking layer is a roadmap item but not the current default.

## Approval flow

1. Operator opens the **Selection panel → Actions tab** on a detection.
2. UI fetches candidate-links, shows top 5 with score breakdown.
3. Operator clicks "Approve" on the right one (or "Reject all" → "Create new Target" if none fit).
4. Backend writes the Neo4j edge and a WORKFLOW timeline event.

## Cross-references

- [backend/candidate-linking.md](../backend/candidate-linking.md)
- [frontend/map-selection-panel.md](../frontend/map-selection-panel.md)
- [scripts/eval-runners.md](../scripts/eval-runners.md) — `eval_candidate_links.py`

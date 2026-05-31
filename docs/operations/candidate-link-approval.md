# Operations — Detection → Target Candidate Links

## What it does

For a detection in PostGIS, propose a ranked list of existing Neo4j Targets this detection might be an observation of. Operator approves or rejects each candidate.

## Endpoints

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/api/detections/{id}/candidate-links` | Returns the ranked list |
| `POST` | `/api/detection-target-candidates/{id}/approve` | Approves a pending row, records `reviewed_by` from the session, and creates the graph edge |
| `POST` | `/api/detection-target-candidates/{id}/reject` | Rejects a pending row and records `reviewed_by` from the session |

## Scoring

[backend/candidate-linking.md](../backend/candidate-linking.md) — deterministic, no LLM. Composite of:

- 30% spatial proximity (haversine, meters)
- 30% class compatibility (detection's `parent_class` vs Target's `class`)
- 30% confidence (the detection's confidence)
- 10% history anchor (does this Target have nearby observations recently?)

## Why deterministic

Linking detections to Targets is consequential — creates a graph edge downstream automations may read. Must be explainable. An LLM ranking layer is a roadmap item, not the current default.

## Approval flow

1. Operator opens **Selection panel → Actions tab** on a detection.
2. UI fetches candidate-links, shows top 5 with score breakdown.
3. Operator clicks "Approve" on the right one (or "Reject all" → "Create new Target" if none fit).
4. Backend accepts only pending rows, writes the signed session username into `reviewed_by`, and returns 409 if another analyst already reviewed the candidate.
5. Approval writes the Neo4j edge; rejection removes the pending candidate edge if present.

## Cross-references

- [backend/candidate-linking.md](../backend/candidate-linking.md)
- [frontend/map-selection-panel.md](../frontend/map-selection-panel.md)
- [backend/main-app-entrypoint.md](../backend/main-app-entrypoint.md)
- [backend-routers/graph-router.md](../backend-routers/graph-router.md)
- [decisions/why-analyst-username-from-session.md](../decisions/why-analyst-username-from-session.md)
- [decisions/why-reject-double-review-with-409.md](../decisions/why-reject-double-review-with-409.md)
- [scripts/eval-runners.md](../scripts/eval-runners.md) — `eval_candidate_links.py`

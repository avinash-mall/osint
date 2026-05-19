# `backend/reports.py` — Target Packages + Collection Requirements

**Path:** [backend/reports.py](../../backend/reports.py)
**Lines:** ~117
**Depends on:** [backend/database.py](../../backend/database.py), [backend/events.py](../../backend/events.py)

## Purpose

Snapshot a Target's recent observations + timeline into a persisted `reports` row, or open a new collection requirement for satellite tasking. Both are operator-driven workflows surfaced in the **Selection panel** of the map.

## Key symbols

- [`_latest_observations`](../../backend/reports.py#L18) — top-N observation rows for a target.
- [`_latest_timeline`](../../backend/reports.py#L35) — top-N timeline events.
- [`create_target_package`](../../backend/reports.py#L50) — joins both into `reports.payload` JSON and publishes a WORKFLOW timeline event.
- [`create_collection_requirement`](../../backend/reports.py#L90) — opens a row in `collection_tasks` (the satellite tasking queue).

## Why this design

- **Snapshot in `payload` JSON** so the report is immutable even if the underlying observations are later edited.
- **Both functions publish to the timeline** so the analyst feed reflects the action without each caller having to remember.

## Cross-references

- `/api/collection/tasks` (in [backend/main.py](../../backend/main.py))
- [frontend/map-selection-panel.md](../frontend/map-selection-panel.md)
- [backend/events-and-timeline.md](events-and-timeline.md)

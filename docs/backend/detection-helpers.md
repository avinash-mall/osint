# `backend/detection_helpers.py` — Object-Details Helpers

**Path:** [backend/detection_helpers.py](../../backend/detection_helpers.py)
**Lines:** ~111
**Depends on:** [backend/database.py](../../backend/database.py)

## Purpose

Shared code used by both [detections-router.md](../backend-routers/detections-router.md) and [fmv-router.md](../backend-routers/fmv-router.md) for the `object_details` table. Validates threat-level and affiliation enums and exposes read + upsert primitives.

## Why this design

- **Hoisted to avoid circular imports.** Both routers need the same validators, and putting them in `schemas.py` would make `schemas.py` import database, which it must not. A thin helper module breaks the cycle.
- **Threat-level/affiliation are validated centrally.** Free-form notes are accepted, but the enums need to match a closed set to keep the UI's color/icon rendering consistent.
- **Upsert, not insert/update branching.** A detection may or may not have an `object_details` row yet — the helper writes whichever case applies in one SQL statement.

## Key symbols

- [`_normalize_threat`](../../backend/detection_helpers.py#L21) — accepts `"unrated|low|medium|high|critical"` (case-insensitive).
- [`_normalize_affiliation`](../../backend/detection_helpers.py#L35) — accepts `"unknown|friendly|hostile|neutral"` (case-insensitive).
- [`_read_object_details`](../../backend/detection_helpers.py#L51) — `(source, source_id) -> {threat, affiliation, notes, updated_at}`.
- [`_upsert_object_details`](../../backend/detection_helpers.py#L69) — single-statement upsert into `object_details`.

## Failure modes

- Invalid enum → returns `None` → router returns 422.
- DB unavailable → router catches and returns 503.

## Cross-references

- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
- [backend-routers/fmv-router.md](../backend-routers/fmv-router.md)
- Tests: [backend/tests/test_object_details.py](../../backend/tests/test_object_details.py)

# `backend/detection_helpers.py` — Object-Details Helpers

**Path:** [backend/detection_helpers.py](../../backend/detection_helpers.py)
**Lines:** ~124
**Depends on:** [backend/database.py](../../backend/database.py)

## Purpose

Shared code for the `object_details` table, used by [detections-router.md](../backend-routers/detections-router.md) and [fmv-router.md](../backend-routers/fmv-router.md). Validates threat-level + affiliation enums; exposes read + upsert primitives.

## Why this design

- **Hoisted to avoid circular imports** — both routers need the same validators; putting them in `schemas.py` would force `schemas.py` to import `database`, which it must not. Thin helper module breaks the cycle.
- **Threat-level/affiliation validated centrally** — free-form notes accepted, but enums must match a closed set to keep UI color/icon rendering consistent.
- **Upsert, not insert/update branching** — a detection may or may not have an `object_details` row; helper writes whichever case in one SQL statement.

## Key symbols

- [`_normalize_threat`](../../backend/detection_helpers.py#L21) — `"unrated|low|medium|high|critical"` (case-insensitive).
- [`_normalize_affiliation`](../../backend/detection_helpers.py#L35) — `"unknown|friendly|hostile|neutral"` (case-insensitive).
- [`_read_object_details`](../../backend/detection_helpers.py#L51) — `(source, source_id) -> {threat, affiliation, notes, updated_at}`.
- [`_upsert_object_details`](../../backend/detection_helpers.py#L69) — single-statement upsert into `object_details`. Plan C: also writes the four optional `ObjectDetailsBody` fields `platform_name` / `platform_family` / `platform_confidence` / `platform_source`; the COALESCE-on-conflict SET preserves prior values when the request omits them. The reference-DB auto-identify path bypasses this helper and UPSERTs the four columns directly (plain `EXCLUDED.X`, no COALESCE) — see [reference-platform-db.md](reference-platform-db.md) `attach_identification_candidates`.

## Failure modes

- Invalid enum → `None` → router 422.
- DB unavailable → router catches → 503.

## Cross-references

- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
- [backend-routers/fmv-router.md](../backend-routers/fmv-router.md)
- Tests: [backend/tests/test_object_details.py](../../backend/tests/test_object_details.py)

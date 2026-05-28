**Path:** [backend/routers/ontology.py#L529-L552](../../backend/routers/ontology.py#L529-L552)
**Lines:** ~25
**Depends on:** `prompt_profiles` table; `PromptProfileBody.make_current`

## Purpose

`POST /api/ontology/prompt-profiles` upserts a (sensor, version) prompt profile. The `make_current` flag in the body controls whether this row should be the active profile for the sensor.

## Why this design

The original `ON CONFLICT (sensor, version) DO UPDATE` clause merged the boolean as:

```sql
current = EXCLUDED.current OR prompt_profiles.current
```

That made `current` sticky-on-TRUE: an UPSERT that re-uploaded an existing profile with `make_current=FALSE` could never demote a previously-current row. The OR kept the existing TRUE value, and the demotion was silently ignored — no error to the operator, the activate endpoint stayed the only way to flip the bit, and partial workflows that *thought* they demoted left stale current profiles in place.

The clause now uses `current = EXCLUDED.current`, so the caller's intent wins. The sibling-demotion `UPDATE prompt_profiles SET current=FALSE WHERE sensor=%s` continues to run only when `make_current=TRUE` (it has no work to do otherwise). Net behavior:

- `make_current=TRUE`, no conflict: demote siblings → INSERT with current=TRUE.
- `make_current=TRUE`, conflict: demote siblings → UPSERT sets current=TRUE.
- `make_current=FALSE`, no conflict: INSERT with current=FALSE.
- `make_current=FALSE`, conflict (row was TRUE): UPSERT sets current=FALSE. This row is demoted; the sensor now has zero current profiles. Callers that want to swap should call `PUT /prompt-profiles/{id}/activate` instead.

## Key symbols

- `create_prompt_profile` — [backend/routers/ontology.py#L529-L552](../../backend/routers/ontology.py#L529-L552)
- `activate_prompt_profile` — [backend/routers/ontology.py#L555-L570](../../backend/routers/ontology.py#L555-L570) (the dedicated swap endpoint, unchanged)

## Inputs / Outputs

Input: `PromptProfileBody {sensor, name, version, prompts, make_current, notes}`. Output: the upserted row including the new `current` value.

## Failure modes

A client that previously relied on UPSERT being a no-op when `make_current=FALSE` and the row was already current will see the row demoted. Migrate those callers to either omit `current` from the workflow or call `/activate` explicitly.

## Cross-references

- Bug found in audit 2026-05-27; refer to plan at `~/.claude/plans/find-all-logical-issues-snappy-glade.md` (item C1).
- Related: [why-admin-mutators-require-admin.md](why-admin-mutators-require-admin.md) — this endpoint is admin-only.

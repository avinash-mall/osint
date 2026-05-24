# Adding a New Admin-Editable Config Table

Phase 5 introduced [`repeat_detector_thresholds`](../backend-routers/admin-thresholds-router.md)
as the per-class config for `worker.tick_near_builder` +
`worker.tick_repeat_detector`. Its shape mirrors `prompt_profiles`
(per-sensor LLM prompts) closely enough that future admin-editable
configs should follow the same pattern.

This convention applies whenever you're adding an admin-tunable config
where:
- Multiple versions of the same logical setting can exist (history /
  rollback / A-B comparison).
- Exactly one row per "scope key" (sensor, kind, AOI, …) is the active
  one for runtime consumers.
- Worker tasks read the active row at run time with a small helper that
  falls back to env-var defaults.

## Recipe

### 1. PostGIS table shape

```sql
CREATE TABLE IF NOT EXISTS <thing>_profiles (
    id          BIGSERIAL PRIMARY KEY,
    <scope_key> VARCHAR(40) NOT NULL,
    <value-1>   <type> NOT NULL,
    …
    current     BOOLEAN NOT NULL DEFAULT FALSE,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by  VARCHAR(100),
    CHECK (<scope_key> IN ('value-a', 'value-b', …))
);

-- Exactly one current row per scope_key. Partial index lets the rest of
-- the rows live alongside as history.
CREATE UNIQUE INDEX IF NOT EXISTS uq_<thing>_scope_current
    ON <thing>_profiles(<scope_key>) WHERE current = TRUE;

CREATE INDEX IF NOT EXISTS idx_<thing>_scope
    ON <thing>_profiles(<scope_key>);
```

Add to [backend/platform_schema.py](../../backend/platform_schema.py)
`ensure_platform_tables` (idempotent — `CREATE IF NOT EXISTS`).

### 2. CRUD router

Mirror [backend/routers/admin_thresholds.py](../../backend/routers/admin_thresholds.py):

- `GET /api/admin/<thing>` — list, filter by `<scope_key>`.
- `POST /api/admin/<thing>` — upsert; `make_current=true` flips other rows
  for the same scope to `current=false` then INSERTs the new one current.
- `PUT /api/admin/<thing>/{id}/activate` — atomic activation (clear other
  current rows for the scope, then SET current=true on the chosen id).
- `DELETE /api/admin/<thing>/{id}` — physical delete; history is the
  rows themselves.

Add a worker-side helper `get_current_<thing>(<scope_key>) -> dict | None`
in the same router module. Returns the active row or `None` so callers
can fall back to env defaults.

Register in [backend/main.py](../../backend/main.py) `app.include_router(...)`.

### 3. Worker consumer

The worker reads the active row at the start of each task:

```python
try:
    from routers.<thing> import get_current_<thing>
    row = get_current_<thing>(scope_key)
    if row and row.get("<value-1>"):
        value_1 = <type>(row["<value-1>"])
except Exception:
    logger.debug("threshold lookup failed", exc_info=True)
# Fall back to env default if row missing.
value_1 = value_1 or env_<type>("<THING>_<VALUE_1>", <default>)
```

This pattern keeps env-var-only deployments working (no config table
populated → env defaults) while letting analysts tune live.

### 4. Frontend admin tab

Model on [frontend/src/components/admin/RepeatThresholdsView.tsx](../../frontend/src/components/admin/RepeatThresholdsView.tsx):

- Two-column layout: grouped list of rows on the left, "Add new" form on
  the right.
- Per-row badges: `CURRENT` tag for the active row; otherwise an
  "Activate" button.
- Delete button per row.
- "Defaults" hint row showing the env defaults when no override exists.

Register a new tab key in [AdminScreen.tsx](../../frontend/src/components/AdminScreen.tsx)
`AdminTab` union + `NAV` array.

### 5. Tests

Offline unit tests with stubbed cursor (no PostGIS needed):

- Create: returns the inserted row.
- Create rejects invalid scope_key (400).
- List filters by scope_key + rejects invalid (400).
- Activate marks one current per scope; 404 when missing.
- Delete: 404 when missing.
- Helper: returns row or None.

See [backend/tests/test_admin_thresholds.py](../../backend/tests/test_admin_thresholds.py)
for the canonical shape.

### 6. Documentation

- Module doc at `docs/backend-routers/<thing>-router.md`.
- Update [docs/INDEX.txt](../INDEX.txt) with the new route doc.
- If the config is load-bearing for an existing worker task, add a line
  to that task's module doc pointing at the new router.

## Cross-references

- [backend-routers/admin-thresholds-router.md](../backend-routers/admin-thresholds-router.md) — the canonical implementation.
- [backend/ontology-system.md](../backend/ontology-system.md) — `prompt_profiles` (the original pattern).

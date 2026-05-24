# Admin Thresholds Router (`/api/admin/repeat-thresholds`)

**Path:** [backend/routers/admin_thresholds.py](../../backend/routers/admin_thresholds.py)
**Lines:** ~150
**Depends on:** [backend/database.py](../../backend/database.py) (`postgis_db`), [backend/platform_schema.py](../../backend/platform_schema.py)

## Purpose

CRUD for the per-class `repeat_detector_thresholds` table that drives
[worker.tick_near_builder](../backend/worker-package-facade.md) +
`worker.tick_repeat_detector`. Modelled on `prompt_profiles`
([conventions/adding-a-new-admin-config-table.md](../conventions/adding-a-new-admin-config-table.md)):
multiple versions per `kind`, exactly one `current=TRUE` per kind.

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET`  | `/api/admin/repeat-thresholds` | [admin_thresholds.py#L40](../../backend/routers/admin_thresholds.py#L40) | List rows; optional `?kind=` filter (base/launchpoint/facility) |
| `POST` | `/api/admin/repeat-thresholds` | [admin_thresholds.py#L66](../../backend/routers/admin_thresholds.py#L66) | Insert a new row; `make_current=true` (default) auto-activates |
| `PUT`  | `/api/admin/repeat-thresholds/{id}/activate` | [admin_thresholds.py#L91](../../backend/routers/admin_thresholds.py#L91) | Atomic activation (clears other `current=true` rows for the same kind) |
| `DELETE` | `/api/admin/repeat-thresholds/{id}` | [admin_thresholds.py#L113](../../backend/routers/admin_thresholds.py#L113) | Physical delete |

Worker-side helper [`get_current_threshold(kind)`](../../backend/routers/admin_thresholds.py#L127) returns the active row dict or `None`; callers fall back to env-var defaults (`_NEAR_RADIUS_M` for radius, `REPEAT_DETECTOR_*` for window/min_count).

## Why this design

- **History as rows.** Every prior threshold stays in the table; analysts can revert by re-activating an older row. No separate audit table.
- **Partial UNIQUE index** (`(kind) WHERE current = TRUE`) enforces "one current per kind" at the DB level — no application-side race.
- **Env fallback** keeps env-only deployments working until an analyst populates a row.

## Inputs / Outputs

Local Pydantic body model `ThresholdBody` (in the router file; not in [schemas.py](../../backend/schemas.py) — only this router uses it). Responses return the affected row dict.

## Failure modes

- Invalid `kind` (not in {base, launchpoint, facility}) → 400.
- Activate/delete on missing id → 404.

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — Phase 5.B.
- [conventions/adding-a-new-admin-config-table.md](../conventions/adding-a-new-admin-config-table.md) — the recipe this implements.
- [backend/worker-package-facade.md](../backend/worker-package-facade.md) — the worker consumers (`tick_near_builder`, `tick_repeat_detector`).
- [frontend/admin-ui.md](../frontend/admin-ui.md) — the AdminScreen "NEAR thresholds" tab.

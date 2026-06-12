# Ontology Router (`/api/ontology/*`)

**Path:** [backend/routers/ontology.py](../../backend/routers/ontology.py)
**Lines:** ~614 (largest router after ingest)
**Depends on:** [backend/ontology.py](../../backend/ontology.py), [backend/auth.py](../../backend/auth.py) (`require_admin`), [backend/schemas.py](../../backend/schemas.py)

Router declared with `prefix="/api/ontology"` — endpoints below relative to that.

## Purpose

CRUD over the ontology (branches, objects, prompts) + the prompt-profile system, version-history audit log, proposed ontology updates log, and unknown-label triage workflow that LLM-emitted labels feed.

## Endpoints

| Method | Path | Full path | Source | Behavior |
|---|---|---|---|---|
| `GET` | `""` | `/api/ontology` | [ontology.py#L66](../../backend/routers/ontology.py#L66) | Branches + objects; filter by `?sensor=` |
| `GET` | `/version` | `/api/ontology/version` | [ontology.py#L123](../../backend/routers/ontology.py#L123) | Current version cursor (clients invalidate cache) |
| `GET` | `/default-prompts` | `/api/ontology/default-prompts` | [ontology.py#L128](../../backend/routers/ontology.py#L128) | DB-backed prompt list (inference reads this); `?sensor=` and/or `?branch=` scope it — `branch` returns that branch + its descendants for a smaller, scene-relevant vocabulary |
| `GET` | `/unknown-labels` | `/api/ontology/unknown-labels` | [ontology.py#L141](../../backend/routers/ontology.py#L141) | LLM-emitted labels awaiting triage; malformed `?since=` → 400 |
| `POST` | `/unknown-labels/{label}/assign` | | [ontology.py#L178](../../backend/routers/ontology.py#L178) | Map a label to an object or create one (admin) |
| `POST` | `/branches` | | [ontology.py#L270](../../backend/routers/ontology.py#L270) | Create a branch (admin) |
| `PATCH` | `/branches/{id}` | | [ontology.py#L303](../../backend/routers/ontology.py#L303) | Update branch (admin) |
| `DELETE` | `/branches/{id}` | | [ontology.py#L346](../../backend/routers/ontology.py#L346) | Delete branch (admin) |
| `POST` | `/objects` | | [ontology.py#L427](../../backend/routers/ontology.py#L427) | Create an object (admin) |
| `PATCH` | `/objects/{id}` | | [ontology.py#L463](../../backend/routers/ontology.py#L463) | Update object (admin) |
| `DELETE` | `/objects/{id}` | | [ontology.py#L501](../../backend/routers/ontology.py#L501) | Delete object (admin) |
| `GET` | `/prompt-profiles` | | [ontology.py#L514](../../backend/routers/ontology.py#L514) | List profiles |
| `POST` | `/prompt-profiles` | | [ontology.py#L530](../../backend/routers/ontology.py#L530) | Create profile (admin) |
| `PUT` | `/prompt-profiles/{id}/activate` | | [ontology.py#L556](../../backend/routers/ontology.py#L556) | Make this profile active (admin) |
| `DELETE` | `/prompt-profiles/{id}` | | [ontology.py#L574](../../backend/routers/ontology.py#L574) | Delete profile (admin) |
| `GET` | `/version-history` | | [ontology.py#L586](../../backend/routers/ontology.py#L586) | Audit log of every edit |
| `GET` | `/updates` | `/api/ontology/updates` | [ontology.py#L604](../../backend/routers/ontology.py#L604) | List proposed ontology updates |

## Why this design

- **Every edit bumps a version** — [`ontology_bump_version`](../../backend/ontology.py) updates the cursor read by `/version`. Inference checks the cursor every 30 s with a TTL cache; SIGHUP forces immediate refresh.
- **Unknown-label triage admin-gated, not auto-assign** — an LLM can suggest "loitering munition" as a new label; an administrator decides: new object, merge, or discard. Auto-assign would let the ontology drift uncontrollably.
- **Prompt profiles** = named bundles of `{sensor: [prompts]}`. Activating one swaps the default prompts wholesale — useful for switching maritime / urban / infrastructure surveillance modes.

## Failure modes

- Missing/expired session on mutation → 401; non-admin session on ontology or prompt-profile mutation → 403.
- Branch/object conflicts and dangling-delete attempts return 400/404 without bumping the version cursor.

## Cross-references

- [backend/ontology-system.md](../backend/ontology-system.md)
- [operations/ontology-edit-workflow.md](../operations/ontology-edit-workflow.md)
- [operations/unknown-label-triage.md](../operations/unknown-label-triage.md)
- [frontend/ontology-admin-ui.md](../frontend/ontology-admin-ui.md)
- [decisions/why-admin-mutators-require-admin.md](../decisions/why-admin-mutators-require-admin.md)
- [decisions/audit-fixes-api-layer-2026-06-11.md](../decisions/audit-fixes-api-layer-2026-06-11.md) — the 2026-06-11 API-layer audit batch

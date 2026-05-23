# Ontology Router (`/api/ontology/*`)

**Path:** [backend/routers/ontology.py](../../backend/routers/ontology.py)
**Lines:** ~612 (largest router after ingest)
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
| `GET` | `/unknown-labels` | `/api/ontology/unknown-labels` | [ontology.py#L135](../../backend/routers/ontology.py#L135) | LLM-emitted labels awaiting triage |
| `POST` | `/unknown-labels/{label}/assign` | | [ontology.py#L172](../../backend/routers/ontology.py#L172) | Map a label to an object or create one |
| `POST` | `/branches` | | [ontology.py#L264](../../backend/routers/ontology.py#L264) | Create a branch (admin) |
| `PATCH` | `/branches/{id}` | | [ontology.py#L297](../../backend/routers/ontology.py#L297) | Update branch |
| `DELETE` | `/branches/{id}` | | [ontology.py#L340](../../backend/routers/ontology.py#L340) | Delete branch |
| `POST` | `/objects` | | [ontology.py#L421](../../backend/routers/ontology.py#L421) | Create an object |
| `PATCH` | `/objects/{id}` | | [ontology.py#L457](../../backend/routers/ontology.py#L457) | Update object |
| `DELETE` | `/objects/{id}` | | [ontology.py#L495](../../backend/routers/ontology.py#L495) | Delete object |
| `GET` | `/prompt-profiles` | | [ontology.py#L508](../../backend/routers/ontology.py#L508) | List profiles |
| `POST` | `/prompt-profiles` | | [ontology.py#L524](../../backend/routers/ontology.py#L524) | Create profile |
| `PUT` | `/prompt-profiles/{id}/activate` | | [ontology.py#L550](../../backend/routers/ontology.py#L550) | Make this profile active |
| `DELETE` | `/prompt-profiles/{id}` | | [ontology.py#L568](../../backend/routers/ontology.py#L568) | Delete profile |
| `GET` | `/version-history` | | [ontology.py#L580](../../backend/routers/ontology.py#L580) | Audit log of every edit |
| `GET` | `/updates` | `/api/ontology/updates` | [ontology.py#L597](../../backend/routers/ontology.py#L597) | List proposed ontology updates |

## Why this design

- **Every edit bumps a version** — [`ontology_bump_version`](../../backend/ontology.py) updates the cursor read by `/version`. Inference checks the cursor every 30 s with a TTL cache; SIGHUP forces immediate refresh.
- **Unknown-label triage operator-gated, not auto-assign** — an LLM can suggest "loitering munition" as a new label; operator decides: new object, merge, or discard. Auto-assign would let the ontology drift uncontrollably.
- **Prompt profiles** = named bundles of `{sensor: [prompts]}`. Activating one swaps the default prompts wholesale — useful for switching maritime / urban / infrastructure surveillance modes.

## Cross-references

- [backend/ontology-system.md](../backend/ontology-system.md)
- [operations/ontology-edit-workflow.md](../operations/ontology-edit-workflow.md)
- [operations/unknown-label-triage.md](../operations/unknown-label-triage.md)
- [frontend/ontology-admin-ui.md](../frontend/ontology-admin-ui.md)

# Ontology Admin UI — `OntologyAdmin.tsx`

**Path:** [frontend/src/components/OntologyAdmin.tsx](../../frontend/src/components/OntologyAdmin.tsx)
**Lines:** ~57267 characters (~1500 lines TSX)

## Purpose

The CRUD UI for the DB-canonical ontology: branches (top-level groupings), objects (individual classes with sensor-default prompts and an icon), prompt profiles, and the unknown-label triage queue. Every edit bumps a version that inference picks up on its next cache cycle.

## Sections

1. **Tree view** — `Branch → [Object]` rendered as a collapsible tree. Each branch and object has inline edit / delete actions.
2. **Object editor** — name, parent branch, sensor toggles (optical / multispectral / sar), default prompts per sensor, icon key.
3. **Prompt profiles** — named bundles of `{sensor: [prompts]}`. Switching the active profile swaps default prompts wholesale.
4. **Unknown labels** — triage queue of LLM-emitted labels; each can be assigned to an existing object or used to create a new one.
5. **Version history** — audit log of every edit with timestamp and user.

## Data sources

- `GET /api/ontology` (+ `?sensor=` filter)
- `POST` / `PATCH` / `DELETE` on `/api/ontology/branches[/{id}]` and `/api/ontology/objects[/{id}]`
- `GET` / `POST` / `PUT /activate` / `DELETE` on `/api/ontology/prompt-profiles`
- `GET /api/ontology/unknown-labels` + `POST /api/ontology/unknown-labels/{label}/assign`
- `GET /api/ontology/version-history`
- WebSocket: `ontology_updated`

## Cross-references

- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [backend/ontology-system.md](../backend/ontology-system.md)
- [operations/ontology-edit-workflow.md](../operations/ontology-edit-workflow.md)
- [operations/unknown-label-triage.md](../operations/unknown-label-triage.md)
- [conventions/adding-a-new-ontology-object.md](../conventions/adding-a-new-ontology-object.md)

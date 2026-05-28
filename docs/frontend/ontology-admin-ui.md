# Ontology Admin UI — `OntologyAdmin.tsx`

**Path:** [frontend/src/components/OntologyAdmin.tsx](../../frontend/src/components/OntologyAdmin.tsx)
**Lines:** ~1617
**Depends on:** [frontend/src/utils/useOntology.ts](../../frontend/src/utils/useOntology.ts), [frontend/src/utils/ontologyApi.ts](../../frontend/src/utils/ontologyApi.ts), [frontend/src/utils/iconLibrary.tsx](../../frontend/src/utils/iconLibrary.tsx), `axios`, backend `/api/ontology`, backend `/api/detections`

## Purpose

CRUD UI for the DB-canonical ontology: branches (top-level groupings), objects (classes with sensor-default prompts + an icon), prompt profiles, unknown-label triage queue. Every edit bumps a version inference picks up on its next cache cycle. The workspace is mounted only for admin sessions and its backend mutations require `require_admin`.

## Sections

1. **Tree view** — `Branch → [Object]` as a collapsible tree. Each branch/object has inline edit/delete actions.
2. **Object editor** — name, parent branch, sensor toggles (optical / multispectral / sar), default prompts per sensor, icon key.
3. **Prompt profiles** — named bundles of `{sensor: [prompts]}`. Switching the active profile swaps default prompts wholesale.
4. **Unknown labels** — triage queue of LLM-emitted labels; each assignable to an existing object or used to create a new one.
5. **Version history** — audit log of every edit with timestamp + user.

## Data sources

- `GET /api/ontology` (+ `?sensor=` filter)
- `POST` / `PATCH` / `DELETE` on `/api/ontology/branches[/{id}]` and `/api/ontology/objects[/{id}]`
- `GET` / `POST` / `PUT /activate` / `DELETE` on `/api/ontology/prompt-profiles`
- `GET /api/ontology/unknown-labels` + `POST /api/ontology/unknown-labels/{label}/assign`
- `GET /api/ontology/version-history`
- `GET /api/detections` for recent instances; uses `VITE_API_URL` at [OntologyAdmin.tsx#L1534](../../frontend/src/components/OntologyAdmin.tsx#L1534)
- WebSocket: `ontology_updated`

## Failure modes

- Analyst sessions do not see the Admin workspace; direct mutation attempts receive 403 from the backend.
- Ontology edit failures leave local form state intact and rely on the existing toast/error path.

## Cross-references

- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [backend/ontology-system.md](../backend/ontology-system.md)
- [operations/ontology-edit-workflow.md](../operations/ontology-edit-workflow.md)
- [operations/unknown-label-triage.md](../operations/unknown-label-triage.md)
- [conventions/adding-a-new-ontology-object.md](../conventions/adding-a-new-ontology-object.md)
- [decisions/why-admin-mutators-require-admin.md](../decisions/why-admin-mutators-require-admin.md)

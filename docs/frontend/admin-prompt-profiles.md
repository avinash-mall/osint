# Admin — Prompt Profiles

**Path:** [frontend/src/components/admin/PromptProfilesView.tsx](../../frontend/src/components/admin/PromptProfilesView.tsx)
**Lines:** ~14396 characters

## Purpose

CRUD over named prompt profiles. A profile = a bundle of `{sensor: [prompts]}` the inference layer reads as default prompts when a request doesn't override.

Activating a profile swaps the active set — useful for switching modes between deployments (maritime, urban, infrastructure-watch).

All CRUD error states pass through [`apiErrorMessage`](../../frontend/src/utils/apiError.ts) so FastAPI 422 `detail` arrays render as text instead of crashing React.

## Data sources

- `GET /api/ontology/prompt-profiles`
- `POST /api/ontology/prompt-profiles`
- `PUT /api/ontology/prompt-profiles/{id}/activate`
- `DELETE /api/ontology/prompt-profiles/{id}`

## Cross-references

- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [backend/ontology-system.md](../backend/ontology-system.md)
- [ontology-admin-ui.md](ontology-admin-ui.md)

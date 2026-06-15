# Recipe — Add a New Ontology Object

## When this applies

Operator wants to add a new tracked class (e.g. "loitering munition") or a new branch grouping. The platform is open-vocab — but adding an object gives it sensor-default prompts, an icon, and a parent branch for grouping.

## Two paths

### 1. Live edit (preferred)

Through the UI:

1. **Admin → Ontology** tab.
2. Pick the branch (or create a new one), click "Add object".
3. Fill in: name, sensor toggles, per-sensor default prompts, icon key.
4. Save.

Backend: `POST /api/ontology/objects` → updates DB → bumps version → publishes `ontology_updated`.

Inference picks up the new prompts on its next 30 s cache cycle (or immediately on SIGHUP). See [operations/ontology-edit-workflow.md](../operations/ontology-edit-workflow.md).

### 2. Bulk edit via seed JSON

For a green-field deployment or a wholesale taxonomy revision:

1. Update [backend/scripts/seeds/](../../backend/scripts/seeds/) JSON files.
2. On a clean target: `python backend/scripts/seed_ontology.py`.
3. On a populated target (destructive): `python backend/scripts/seed_ontology.py --force` — overwrites the live ontology.

## What an object record carries

- `name` — operator-facing label
- `branch_id` — parent branch
- `aliases` — labels that should normalize to this object
- `default_prompts.{optical, multispectral, sar}` — sensor-keyed prompt lists for inference
- `icon_key` — referenced by [frontend/utils-ontology-and-icons.md](../frontend/utils-ontology-and-icons.md)

## Verifying it works

After saving:

1. Check `GET /api/ontology/default-prompts?sensor=optical` includes the new prompts.
2. Wait 30 s (or SIGHUP inference) so the service refreshes its cached default-prompt vocabulary with the new object.
3. Run a `/detect` with no `text_prompts` in `metadata` — inference uses the ontology default and the new prompts should fire.

## Cross-references

- [backend/ontology-system.md](../backend/ontology-system.md)
- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [operations/ontology-edit-workflow.md](../operations/ontology-edit-workflow.md)
- [frontend/ontology-admin-ui.md](../frontend/ontology-admin-ui.md)
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)

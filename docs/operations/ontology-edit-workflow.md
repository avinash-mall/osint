# Operations — Editing the Ontology

## Where edits happen

**Admin → Ontology** tab in the UI ([frontend/ontology-admin-ui.md](../frontend/ontology-admin-ui.md)).

## What an edit does

1. UI calls one of the CRUD endpoints in [backend-routers/ontology-router.md](../backend-routers/ontology-router.md).
2. The endpoint writes the row in PostGIS (`ontology_branches`, `ontology_objects`, `ontology_prompt_profiles`).
3. The endpoint bumps the `ontology_version` cursor.
4. Backend publishes `ontology_updated` on Redis; the WS router forwards it to all clients.
5. Inference's prompt cache checks the version on its next 30 s poll (or immediately on SIGHUP) and refreshes.
6. All frontend consumers of `useOntology` refetch.

## When changes go live

- **Frontend:** immediately on WS receipt (~1 s).
- **Inference:** within 30 s, or immediately if you `docker compose exec inference-sam3 kill -HUP 1`.

## Seeding new branches/objects from scratch

The bootstrap seed lives at [backend/scripts/seed_ontology.py](../../backend/scripts/seed_ontology.py). It runs **once** at first boot (when the tables are empty — see [backend/platform-schema-migrations.md](../backend/platform-schema-migrations.md#L537)). After bootstrap the DB is canonical.

To re-seed manually (destructive — overwrites the live ontology with the seed JSON):

```bash
python backend/scripts/seed_ontology.py --force
```

## Cross-references

- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [backend/ontology-system.md](../backend/ontology-system.md)
- [frontend/ontology-admin-ui.md](../frontend/ontology-admin-ui.md)
- [conventions/adding-a-new-ontology-object.md](../conventions/adding-a-new-ontology-object.md)

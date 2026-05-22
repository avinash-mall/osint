# Implemented: GET /api/ontology/updates Endpoint

## Status

Implemented in v0.11. Resolves 404 API error on the Link Graph bottom strip.

## What it is

A read endpoint `GET /api/ontology/updates` returning the history of LLM-proposed ontology updates (proposed entities/relationships) awaiting analyst review in the database.

## Why it was implemented

The Link Graph workspace (`GraphExplorer.tsx`) has a bottom operational strip displaying proposed ontology updates for analyst review. On load it queries `/api/ontology/updates?limit=8`. Previously the route was unregistered → 404 Not Found → UI permanently showed "No ontology proposals yet." even with pending proposals in the DB from OSINT/document analysis.

Fix:
1. Implemented `GET /api/ontology/updates` in `/nvme/osint/backend/routers/ontology.py`.
2. Endpoint queries the PostGIS `ontology_updates` table, returns rows ordered `id DESC` up to a `limit` param (default 8).
3. Verified the JSON response structure fits the React state (`updatesResponse.data.updates || []`) expected by the frontend graph explorer.

## Migration

None — the `ontology_updates` table was already declared in the schema (`backend/platform_schema.py`) and populated by LLM background analysis tasks (`run_ontology_update`). Adding the endpoint simply exposes the data to the UI.

## Cross-references

- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)

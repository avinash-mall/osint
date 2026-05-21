# Implemented: GET /api/ontology/updates Endpoint

## Status

Implemented in v0.11. Resolves 404 API error on the Link Graph bottom strip.

## What it is

A read endpoint `GET /api/ontology/updates` that returns the history of LLM-proposed ontology updates (such as proposed entities and relationships) awaiting analyst review in the database.

## Why it was implemented

The Link Graph workspace (`GraphExplorer.tsx`) features a bottom operational strip that displays proposed ontology updates for analyst visibility and review. When loading, it queries `/api/ontology/updates?limit=8`.
Previously, this route was entirely unregistered in the backend, resulting in a 404 Not Found error and causing the UI to permanently display "No ontology proposals yet." even if the DB contains pending proposals generated via OSINT or documents analysis.

To solve this:
1. We implemented `GET /api/ontology/updates` inside `/nvme/osint/backend/routers/ontology.py`.
2. The endpoint queries the PostGIS database's `ontology_updates` table and returns them ordered by `id DESC` up to a requested `limit` parameter (default 8).
3. We verified that the JSON response structure fits the React state (`updatesResponse.data.updates || []`) expected by the frontend graph explorer.

## Migration

No migration actions are needed as the database table `ontology_updates` was already declared in the schema (`backend/platform_schema.py`) and populated by LLM background analysis tasks (`run_ontology_update`). Adding this endpoint simply exposes the data to the user interface.

## Cross-references

- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)

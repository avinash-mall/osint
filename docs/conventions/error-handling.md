# Error Handling

## HTTP status code map

| Code | When |
|---|---|
| `200` | Success |
| `201` | Resource created (operator-drawn detection, new ontology object) |
| `204` | Successful delete |
| `400` | Malformed request body / bbox / params; client's fault |
| `401` | No valid session cookie (returned by middleware) |
| `403` | Session valid but lacks `is_admin` |
| `404` | Resource not found |
| `409` | Conflict (profile already loaded, duplicate ontology key) |
| `415` | Unsupported media type (ingest rejects unknown extensions) |
| `422` | Pydantic validation failure |
| `429` | Rate limit (currently unused; reserved) |
| `503` | Downstream dependency down — LLM, DB, inference |
| `507` | Disk full on shared volume (ingest only) |

## Fixture fallback pattern

When a feature requires optional infrastructure (DEM, routing graph), the endpoint returns a 200 with `mode: "fixture_no_*"` rather than 5xx. This is intentional so:

- Demos without the full data set still work.
- The frontend can render "DEM not configured" rather than a generic error.

Examples:

- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md): `dem_available()` → `mode: "fixture_no_dem"`
- [backend/routing-graph-osmnx.md](../backend/routing-graph-osmnx.md): `graph_available()` → `mode: "fixture_no_graph"`

## Internal trust boundary

- **Validate at the HTTP boundary** (Pydantic models, query parsers, bbox parser).
- **Trust internal calls** — module A calling module B doesn't need to re-validate B's preconditions.

This keeps the call paths readable and concentrates validation at one layer.

## Graceful LLM degradation

[backend/ai.py](../../backend/ai.py) raises `AIUnavailable` when the LLM is not reachable. Every AI-backed route catches it and returns 503 with a stable shape. The frontend hides Ava features automatically. See [operations/llm-ava-configuration.md](../operations/llm-ava-configuration.md).

## Don't swallow errors silently

The only place we swallow errors is [backend/ontology.py](../../backend/ontology.py) (logging unknown labels must not break the pipeline) and [backend/events.py](../../backend/events.py) (event publishing is best-effort). Both are documented exceptions, not the norm.

## Cross-references

- [backend/main-app-entrypoint.md](../backend/main-app-entrypoint.md) — the middleware that returns 401 on mutating verbs
- [backend/pydantic-schemas.md](../backend/pydantic-schemas.md)

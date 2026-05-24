# Operational-Entities Router (`/api/operational-entities`, `/api/operational-entity-candidates`)

**Path:** [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py)
**Lines:** ~430
**Depends on:** [backend/database.py](../../backend/database.py), [backend/graph_writes.py](../../backend/graph_writes.py), [backend/platform_schema.py](../../backend/platform_schema.py)

## Purpose

CRUD for the six operational entity kinds (Vessel / Aircraft / Vehicle / Facility / Unit / Asset) plus the analyst review surface for `entity_candidates` rows produced by [worker.tick_propose_entities](../backend/worker-package-facade.md). Every write also projects the matching Neo4j mirror with the secondary `:Asset` label for Vessel/Aircraft/Vehicle so generic `MATCH (a:Asset)` queries hit them.

## Why this design

The redesign needs operational entities as first-class graph entities so workflows 3 (site composition) and 6 (transitive Cypher) have things to traverse beyond Detection/SatellitePass. Per [decisions/why-llm-proposed-entities.md](../decisions/why-llm-proposed-entities.md), entities are analyst-asserted by default — the proposer task drops candidates into the review queue but never auto-mints. Identity is the analyst-friendly `id` (auto-slugged from `name` if not supplied) so analysts can write Cypher in terms they recognise.

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET`  | `/api/operational-entities` | [operational_entities.py#L114](../../backend/routers/operational_entities.py#L114) | List (filterable by `kind`) |
| `GET`  | `/api/operational-entities/{id}` | [#L139](../../backend/routers/operational_entities.py#L139) | One entity |
| `POST` | `/api/operational-entities` | [#L156](../../backend/routers/operational_entities.py#L156) | Create + Neo4j project |
| `PATCH`| `/api/operational-entities/{id}` | [#L188](../../backend/routers/operational_entities.py#L188) | Partial update + re-project |
| `DELETE` | `/api/operational-entities/{id}` | [#L223](../../backend/routers/operational_entities.py#L223) | Cascade delete (PostGIS + Neo4j) |
| `POST` | `/api/operational-entities/{id}/attach-observation` | [#L241](../../backend/routers/operational_entities.py#L241) | MERGE `OBSERVED_AT` from asset to existing Observation node |
| `POST` | `/api/operational-entities/{id}/operates-from/{base_id}` | [#L251](../../backend/routers/operational_entities.py#L251) | MERGE `OPERATES_FROM` + update PostGIS column |
| `POST` | `/api/operational-entities/{id}/part-of/{unit_id}` | [#L267](../../backend/routers/operational_entities.py#L267) | MERGE `PART_OF` + update PostGIS column |
| `POST` | `/api/operational-entities/{id}/same-as/{other_id}` | [#L283](../../backend/routers/operational_entities.py#L283) | Analyst-approved `SAME_AS` (deletes matching `POSSIBLY_SAME_AS`) |
| `GET`  | `/api/operational-entity-candidates` | [#L304](../../backend/routers/operational_entities.py#L304) | List pending/approved/rejected proposer rows |
| `POST` | `/api/operational-entity-candidates/{id}/approve` | [#L334](../../backend/routers/operational_entities.py#L334) | Mint the entity + mark candidate approved |
| `POST` | `/api/operational-entity-candidates/{id}/reject` | [#L376](../../backend/routers/operational_entities.py#L376) | Mark dismissed |

## Inputs / Outputs

Local Pydantic models (not in [schemas.py](../../backend/schemas.py) since they're only used here): `OperationalEntityCreate`, `OperationalEntityUpdate`, `SameAsRequest`, `AttachObservationRequest`. Responses wrap rows in `{success, entity|candidate, ...}`.

## Failure modes

- Invalid `kind` (not in {vessel, aircraft, vehicle, facility, unit, asset}) → 400.
- Missing entity on GET/PATCH/DELETE → 404.
- INSERT conflicts (duplicate id) → 409.
- Neo4j unreachable during projection → logged via [graph_writes](../backend/graph-writes.md); the PostGIS write still succeeds.
- Approve of an already-approved/rejected candidate → 404 ("pending candidate not found").

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — Phase 4.
- [decisions/why-llm-proposed-entities.md](../decisions/why-llm-proposed-entities.md) — the proposer + review rationale.
- [backend/graph-writes.md](../backend/graph-writes.md) — operational-entity helpers.
- [backend/platform-schema-migrations.md](../backend/platform-schema-migrations.md) — `operational_entities`, `entity_candidates`, `near_builder_state` table defs.

# Operational-Entities Router (`/api/operational-entities`, `/api/operational-entity-candidates`)

**Path:** [backend/routers/operational_entities.py](../../backend/routers/operational_entities.py)
**Lines:** ~693
**Depends on:** [backend/auth.py](../../backend/auth.py), [backend/database.py](../../backend/database.py), [backend/graph_writes.py](../../backend/graph_writes.py), [backend/platform_schema.py](../../backend/platform_schema.py)

## Purpose

CRUD for the six operational entity kinds (Vessel / Aircraft / Vehicle / Facility / Unit / Asset) plus the analyst review surface for `entity_candidates` rows produced by [worker.tick_propose_entities](../backend/worker-package-facade.md). Every write also projects the matching Neo4j mirror with the secondary `:Asset` label for Vessel/Aircraft/Vehicle so generic `MATCH (a:Asset)` queries hit them.

## Why this design

The redesign needs operational entities as first-class graph entities so workflows 3 (site composition) and 6 (transitive Cypher) have things to traverse beyond Detection/SatellitePass. Per [decisions/why-llm-proposed-entities.md](../decisions/why-llm-proposed-entities.md), entities are analyst-asserted by default — the proposer task drops candidates into the review queue but never auto-mints. Identity is the analyst-friendly `id` (auto-slugged from `name` if not supplied) so analysts can write Cypher in terms they recognise. Analyst attribution comes from the signed session cookie, not request-body identity fields.

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET`  | `/api/operational-entities` | [operational_entities.py#L129](../../backend/routers/operational_entities.py#L129) | List (filterable by `kind`) |
| `GET`  | `/api/operational-entities/{id}` | [#L171](../../backend/routers/operational_entities.py#L171) | One entity |
| `POST` | `/api/operational-entities` | [#L189](../../backend/routers/operational_entities.py#L189) | Create + Neo4j project; `created_by` comes from `SessionUser.username` |
| `PATCH`| `/api/operational-entities/{id}` | [#L228](../../backend/routers/operational_entities.py#L228) | Partial update + re-project |
| `DELETE` | `/api/operational-entities/{id}` | [#L265](../../backend/routers/operational_entities.py#L265) | Cascade delete (PostGIS + Neo4j) |
| `POST` | `/api/operational-entities/{id}/attach-observation` | [#L338](../../backend/routers/operational_entities.py#L338) | MERGE `OBSERVED_AT` from asset to existing Observation node |
| `POST` | `/api/operational-entities/{id}/operates-from/{base_id}` | [#L349](../../backend/routers/operational_entities.py#L349) | MERGE `OPERATES_FROM` + update PostGIS column |
| `POST` | `/api/operational-entities/{id}/part-of/{unit_id}` | [#L366](../../backend/routers/operational_entities.py#L366) | MERGE `PART_OF` + update PostGIS column |
| `POST` | `/api/operational-entities/{id}/same-as/{other_id}` | [#L653](../../backend/routers/operational_entities.py#L653) | Analyst-approved `SAME_AS` using session identity (deletes matching `POSSIBLY_SAME_AS`) |
| `GET`  | `/api/operational-entity-candidates` | [#L456](../../backend/routers/operational_entities.py#L456) | List pending/approved/rejected proposer rows |
| `POST` | `/api/operational-entity-candidates/{id}/approve` | [#L487](../../backend/routers/operational_entities.py#L487) | Mint the entity + mark candidate approved with session reviewer |
| `POST` | `/api/operational-entity-candidates/{id}/reject` | [#L546](../../backend/routers/operational_entities.py#L546) | Mark dismissed with session reviewer |

### Phase 5 — SAME_AS review + merge + embedding tracks

| Method | Path | Behavior |
|---|---|---|
| `GET`  | `/api/operational-entities/pending-same-as` | List pending `POSSIBLY_SAME_AS` edges with both entities' headline properties + score + source. |
| `POST` | `/api/operational-entities/pending-same-as/reject` | Body `{a_id, b_id}` — delete the pending edge. |
| `POST` | `/api/operational-entities/{a_id}/merge-into/{b_id}` | Body `{resolutions: {column: "a"|"b"}}` for six mergeable columns. UPDATEs B, re-homes A's references in the same transaction (`operational_entity_tracks` attachments move to B with `ON CONFLICT DO NOTHING`; `unit_id` / `operates_from_base_id` soft references and `entity_candidates.approved_entity_id` are repointed to B), then DELETEs A + removes A's Neo4j mirror; response analyst is from session. |
| `POST` | `/api/operational-entities/{id}/attach-track/{track_id}` | Phase 5.J — analyst links a detection_track for re-ID centroid aggregation; `attached_by` is from session. Idempotent. |
| `GET`  | `/api/operational-entities/{id}/tracks` | List attached detection_tracks for an entity. |
| `DELETE` | `/api/operational-entities/{id}/tracks/{track_id}` | Detach. |

## Inputs / Outputs

Local Pydantic models (not in [schemas.py](../../backend/schemas.py) since they're only used here): `OperationalEntityCreate`, `OperationalEntityUpdate`, `MergeIntoRequest`, `AttachObservationRequest`. Responses wrap rows in `{success, entity|candidate, ...}`.

## Failure modes

- Invalid `kind` (not in {vessel, aircraft, vehicle, facility, unit, asset}) → 400.
- Missing or invalid session on mutating routes → 401.
- Missing entity on GET/PATCH/DELETE → 404.
- INSERT conflicts (duplicate id) → 409.
- Neo4j unreachable during projection → logged via [graph_writes](../backend/graph-writes.md); the PostGIS write still succeeds.
- Approve of an already-approved/rejected candidate → 404 ("pending candidate not found").

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — Phase 4.
- [decisions/why-llm-proposed-entities.md](../decisions/why-llm-proposed-entities.md) — the proposer + review rationale.
- [decisions/why-analyst-username-from-session.md](../decisions/why-analyst-username-from-session.md) — session-derived analyst attribution.
- [backend/graph-writes.md](../backend/graph-writes.md) — operational-entity helpers.
- [backend/platform-schema-migrations.md](../backend/platform-schema-migrations.md) — `operational_entities`, `entity_candidates`, `near_builder_state` table defs.
- [decisions/audit-fixes-api-layer-2026-06-11.md](../decisions/audit-fixes-api-layer-2026-06-11.md) — the 2026-06-11 API-layer audit batch

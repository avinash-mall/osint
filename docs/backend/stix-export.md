# `backend/stix_export.py` — STIX 2.1 export of the operational-entity graph

**Path:** [backend/stix_export.py](../../backend/stix_export.py)
**Lines:** ~135
**Depends on:** `uuid`, `datetime` (stdlib). Pure serialization — no DB, no network in the builder (the router supplies rows).

## Purpose

Serialize Sentinel's operational entities + their relationships into a STIX 2.1
bundle that CTI / SIEM platforms (OpenCTI, Splunk ES, MS Sentinel, IBM QRadar)
can ingest. Backs `GET /api/graph/export/stix` (read-only). Standards-based
interchange for a defence analyst handing GEOINT entities to a fusion cell.

## Why this design

- **Clean-room from the OASIS STIX 2.1 spec** — no ShadowBroker source copied
  (its `stix_exporter.py` is MIT but we reimplemented to map *our* domain).
- **GEOINT, not cyber-CTI mapping.** Our `operational_entities.kind` maps to
  STIX `infrastructure` (vessel/aircraft/vehicle/asset — controllable physical
  assets) or `identity` (unit = organization, facility = system), **not**
  threat-actor/malware. Sentinel fields ride as `x_sentinel_*` custom properties
  (permitted by STIX 2.1).
- **Relationships resolve or drop.** `build_bundle` maps internal ids → STIX ids
  and emits a `relationship` SRO only when **both** endpoints are in the bundle —
  never a half-resolved dangling edge.
- **Offline.** The router derives relationships from entity FK columns
  (`operates_from_base_id`, `unit_id`) in PostGIS — no Neo4j round-trip, no
  network (Hard rule #8).

## Key symbols

- `entity_to_stix(entity)` — one row → SDO (`infrastructure`/`identity`).
- `relation_to_stix(rel, src_ref, tgt_ref)` — edge → `relationship` SRO (type
  normalised `_`→`-`).
- `build_bundle(entities, relations)` — full bundle with id resolution.

## Inputs / Outputs

- **In:** iterables of entity dicts (`id, kind, name, callsign, hull,
  entity_class, unit_id, operates_from_base_id`) and relation dicts
  (`source_id, target_id, relation_type`).
- **Out:** a STIX 2.1 bundle dict (`type:"bundle"`, `spec_version:"2.1"`,
  `objects:[...]`) ready for `json` serialization.

## Failure modes

- Unknown `kind` → defaults to `infrastructure` (never raises).
- Relation to a non-exported entity → silently dropped (no dangling SRO).

## Cross-references

- Router: [backend-routers/graph-router.md](../backend-routers/graph-router.md) (`/api/graph/export/stix`)
- Source table: `operational_entities` ([backend-routers/operational-entities-router.md](../backend-routers/operational-entities-router.md))
- Decision: [decisions/why-stix-21-export.md](../decisions/why-stix-21-export.md)
- Tests: [backend/tests/test_stix_export.py](../../backend/tests/test_stix_export.py)

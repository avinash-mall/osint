# Decision — export the entity graph as STIX 2.1, mapped to GEOINT SDOs

## Context

Sentinel has a rich operational-entity graph but no standards-based export, so
handing entities to a fusion cell / SIEM meant ad-hoc JSON. ShadowBroker emits
STIX 2.1 (MIT-licensed module) for OpenCTI/Splunk/Sentinel/QRadar. STIX export
is pure offline serialization and genuinely useful for defence interop.

## Decision

Add `backend/stix_export.py` + `GET /api/graph/export/stix` producing a valid
STIX 2.1 bundle. **Clean-room from the OASIS spec** (not a copy of ShadowBroker's
MIT module) so the mapping fits our domain:

- `vessel / aircraft / vehicle / asset` → STIX **`infrastructure`** (controllable
  physical assets).
- `unit` → **`identity`** (`identity_class: organization`); `facility` →
  **`identity`** (`identity_class: system`).
- Relationships from entity FK columns (`operates_from_base_id` → `operates-from`,
  `unit_id` → `assigned-to`) → STIX **`relationship`** SROs.
- Sentinel-only fields as `x_sentinel_*` custom properties.

## Why

- **GEOINT, not cyber-CTI.** ShadowBroker maps to threat-actor/malware; that's
  wrong for us. Identity/infrastructure/relationship is the faithful GEOINT
  mapping, and custom `x_` props keep our provenance without breaking validators.
- **Resolve-or-drop relationships.** Emitting a `relationship` only when both
  endpoints are in the bundle avoids dangling refs that break importers.
- **Offline + read-only.** Sourced from PostGIS FK columns; no Neo4j round-trip,
  no network. No new mutation surface.

## Consequences

- Relationship richness is currently the two FK-derived edges; the Neo4j link
  graph has more edge types. If analysts need those, extend the router to read
  Neo4j relationships and pass them into the same `build_bundle` — the builder
  already handles arbitrary `relation_type`.
- Bundle export capped at 5000 entities per call (pagination can be added later).

## Cross-references

- [backend/stix-export.md](../backend/stix-export.md)
- [backend-routers/graph-router.md](../backend-routers/graph-router.md)
- [backend-routers/operational-entities-router.md](../backend-routers/operational-entities-router.md)

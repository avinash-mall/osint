# Decision — TLEs are imported, never fetched at runtime

## Context

Satellite overpass prediction (adapted in concept from ShadowBroker's online
satellite layer) needs Two-Line Element sets. ShadowBroker pulls TLEs live from
CelesTrak. Sentinel must run air-gapped (Hard rule #8) — no runtime internet.

## Decision

Sentinel **stores analyst-supplied TLEs** in PostGIS (`satellite_tles`) via
`POST /api/satellites/tle`, and **never fetches them at runtime**. Operators
refresh elements by importing a TLE file captured externally (an air-gap
transfer). Propagation (`satellite_overpass.py`) is pure SGP4 maths and needs no
network.

## Why

- **Air-gap compliance.** The only internet-dependent part of the upstream
  feature was TLE freshness; turning that into a manual import removes the last
  online dependency while keeping the analytical value.
- **No writes to runtime data dirs.** Storing in PostGIS (not a baked/read-only
  data dir) respects Hard rule #1 and reuses the platform-schema migration path.
- **Provenance over recency.** `epoch` and `source` are stored and surfaced so
  analysts can judge staleness; we deliberately do **not** auto-expire or
  auto-refresh — there is no authority to refresh from, offline.

## Consequences

- Accuracy degrades as elements age; this is an operator responsibility,
  signalled by the displayed epoch, not enforced by the system.
- Clean-room implementation — no ShadowBroker (AGPL) source was copied; only the
  public SGP4 model and TLE format are used.

## Cross-references

- [backend/satellite-overpass.md](../backend/satellite-overpass.md)
- [backend-routers/satellites-router.md](../backend-routers/satellites-router.md)
- [deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md)

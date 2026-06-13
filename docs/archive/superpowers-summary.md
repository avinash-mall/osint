# Superpowers Plan Archive Summary

**Path:** [docs/archive/superpowers-summary.md](superpowers-summary.md)
**Lines:** ~64
**Depends on:** [backend/reference-platform-db.md](../backend/reference-platform-db.md), [backend/reference-platform-baker.md](../backend/reference-platform-baker.md), [backend-routers/reference-platforms-router.md](../backend-routers/reference-platforms-router.md), [frontend/identification-panel.md](../frontend/identification-panel.md), [deployment/docker-compose-services.md](../deployment/docker-compose-services.md)

## Purpose

Condense the old `docs/superpowers/*` implementation plans into one indexed
archive note so agents keep the context without reading hundreds of kilobytes of
stale planning text.

## Why this Design

The removed plan files were useful while the reference-platform database and
runtime asset bakers were being built, but they drifted after implementation and
carried many broken relative links. The live source of truth is now the module
docs and decisions linked below. Keeping one summary preserves intent while
reducing startup context.

## Key Symbols

- **Plan A: pgvector schema** — implemented as `reference_platforms`,
  `reference_chips`, `platform_identification_candidates`, and `object_details`
  `platform_*` columns. Current docs:
  [backend/reference-platform-db.md](../backend/reference-platform-db.md) and
  [decisions/why-pgvector-for-reference-db.md](../decisions/why-pgvector-for-reference-db.md).
- **Plan B: bake pipeline** — implemented as reference-corpora bake/seed flow and
  backend helpers that call `/embed`. Current docs:
  [backend/reference-platform-baker.md](../backend/reference-platform-baker.md),
  [operations/reference-corpora-bake.md](../operations/reference-corpora-bake.md),
  and [inference/embed-endpoint.md](../inference/embed-endpoint.md).
- **Plan C: auto-identify** — implemented by backend-side candidate generation,
  `REFERENCE_ID_AUTO_THRESHOLD`, and auto-apply behavior. Current docs:
  [decisions/why-auto-identify-in-backend-not-inference.md](../decisions/why-auto-identify-in-backend-not-inference.md)
  and [decisions/why-auto-write-with-threshold.md](../decisions/why-auto-write-with-threshold.md).
- **Plan D: backend API** — implemented by `/api/reference-platforms`,
  detection identification/candidate endpoints, chip-image reads, and admin
  seed. Current docs:
  [backend-routers/reference-platforms-router.md](../backend-routers/reference-platforms-router.md).
- **Plan E: frontend** — implemented by the selection-panel identification
  subsection and admin reference-platform tab. Current docs:
  [frontend/identification-panel.md](../frontend/identification-panel.md) and
  [frontend/admin-reference-platforms.md](../frontend/admin-reference-platforms.md).
- **Plan F: websocket sync** — candidate write/review events now ride the normal
  Redis/WebSocket channel family. Current docs:
  [operations/websocket-event-channels.md](../operations/websocket-event-channels.md).
- **Runtime asset bakers** — superseded by runtime bake-profile services for
  basemap, terrain, DEM, and OSRM assets. Current docs:
  [deployment/docker-compose-services.md](../deployment/docker-compose-services.md),
  [deployment/baker-dem-dockerfile.md](../deployment/baker-dem-dockerfile.md),
  [deployment/baker-osrm-dockerfile.md](../deployment/baker-osrm-dockerfile.md),
  and [deployment/baker-tiles-dockerfile.md](../deployment/baker-tiles-dockerfile.md).

## Inputs / Outputs

Input: historical references to `docs/superpowers/*`. Output: pointers to the
current module docs that replaced those plans.

## Failure Modes

If an old plan and a current module doc disagree, the current module doc and code
win. Add a new decision doc for any new architecture change rather than reviving
the old plan files.

## Cross-References

- [agent-entry.md](../agent-entry.md)
- [conventions/documentation-workflow.md](../conventions/documentation-workflow.md)

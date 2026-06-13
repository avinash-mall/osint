# Why the Selection panel went from 6 tabs to 4

**Status:** shipped (2026-06-13). `SelectionPanel` primary tabs: **Details /
Analytics / Similar / Active Tracks**. Provenance folded into Details;
Satellites gated to a header affordance.

## Context

The right Selection panel had six primary tabs: Details, Analytics, Sat,
Similar, Prov, Tracks. Two were rarely used and crowded the tab row:
- **Provenance** — read-only lineage audit (source raster, model/sensor,
  calibrated vs raw confidence, detector ensemble, taxonomy version).
- **Satellites** — offline overpass / collection planning, only relevant when
  scheduling collection, not during per-detection triage.

A six-way tab row at ~320px wide left each tab cramped and pushed the primary
triage surface (Details) to one-sixth of the bar.

## Decision

- **Provenance → a collapsed-by-default `<details>` disclosure at the bottom of
  the Details tab** (`data-tour="details-provenance"`), rendering the existing
  `<ProvenancePanel>`. Lineage is audit info you consult occasionally, not a
  first-class triage view — it belongs adjacent to the detection facts, one
  expand away.
- **Satellites → a secondary header affordance** (a satellite-icon toggle in the
  panel header, `data-tour="tab-satellites"`) rather than a primary tab. It
  still drives `rightTab === 'satellites'` and renders the same
  `satellitesSlot`; the header shows "Satellites / OVERPASS" when active. Per
  the user's choice ("gated secondary tab").
- `'provenance'` removed from the `SelectionRightTab` union, the header switch
  case, and the standalone render branch; `'satellites'` kept in the union.
- Product tour: `tab-provenance` step retargeted to `details-provenance` (and
  `onStepChange` opens Details + the right panel for it); `tab-satellites`
  anchor preserved on the header button so its step still resolves.

### Why

- 4 evenly-weighted primary tabs read cleaner and give Details room.
- Folding (not deleting) Provenance keeps the audit trail reachable without a
  dedicated tab; gating (not deleting) Satellites keeps collection-planning
  available without spending a primary slot on a rarely-used mode.

## Consequences

- `Database` icon import dropped from `SelectionPanel` (only the removed
  Provenance header case used it).
- Selecting a detection now shows a "Provenance / lineage" disclosure at the
  bottom of Details; Satellites is reached via the header satellite icon.
- The product-tour step count net unchanged (tab-provenance → details-provenance;
  tab-satellites retained).

## Cross-references

- [frontend/map-selection-panel.md](../frontend/map-selection-panel.md)
- [frontend/map-satellites-panel.md](../frontend/map-satellites-panel.md)
- [decisions/completed-stubbed-graph-ui.md](completed-stubbed-graph-ui.md)

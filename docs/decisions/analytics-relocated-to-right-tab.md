# Why the locked Analytics subgroup left the left sidebar

**Status:** shipped (2026-06-13). The "Analytics tools" subgroup (Viewshed /
Line of Sight / Routes) was removed from the `LayerPanel` Overlays section.

## Context

The left operating-picture rail showed three analytics-overlay toggles
(Viewshed / LOS / Routes) under an "Analytics tools" subhead. They were
**padlocked (disabled) until the analyst ran the tool** from the right-panel
ANALYTICS tab — a lock glyph, a "Run the tool first" tooltip, and no metric.
They were dead weight in the default view (nothing to toggle until a run
exists) and duplicated controls the ANALYTICS tab already owns.

## Decision

Remove the subgroup entirely from `LayerPanel`. Analytics overlay visibility is
driven solely from the right-panel `AnalyticsToolsPanel`, which already owns
`setActiveLayers` (each tool flips its own overlay on when it produces a
result). Cleanups traceable to this change:

- Dropped the `analyticsToolRows` array, the `analyticsCounts` prop (and the
  `analyticsResults`-derived object GaiaMap passed for it), and the `Lock`
  import.
- Simplified `OverlayRow` — its `disabled`/lock branch had no remaining caller
  (the live layer rows are never disabled), so the param, the lock glyph, and
  the "run first" tooltip were removed.
- Removed the `analytics-tools` product-tour step.

Also in the same pass: **OBB promoted to the default box mode** in the "Detection
box" segmented control — OBB is listed first and titled "Oriented bounding box
— default" (the `bboxMode` default was already `obb`).

### Why

- The left rail is the "what's on the map" surface; a control that does nothing
  until acted on elsewhere is clutter. The right ANALYTICS tab is where a run is
  configured and is the natural home for its overlay toggle.
- Fewer disabled/locked affordances = a more intuitive default view (the
  analyst's stated complaint).

## Consequences

- One fewer subsection in the left rail; no locked rows in the default view.
- `OverlayRow` is simpler (no disabled state). The now-unused
  `.layer-panel-overlay-dot.is-lock` / `.is-disabled` CSS is dead and can be
  pruned in a polish pass.
- Cross-ref [why-layerpanel-dot-toggle.md](why-layerpanel-dot-toggle.md), which
  documented the lock glyph — that behaviour no longer exists.

## Cross-references

- [frontend/workspace-geoint-gaiamap.md](../frontend/workspace-geoint-gaiamap.md)
- [decisions/why-layerpanel-dot-toggle.md](why-layerpanel-dot-toggle.md)
- [decisions/centralized-filter-surface.md](centralized-filter-surface.md)

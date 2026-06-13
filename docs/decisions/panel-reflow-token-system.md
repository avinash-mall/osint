# Why floating-chrome reflow via shared reserve bands

**Status:** shipped (2026-06-13). Map workspace floating chrome now reflows
around the side panels and the bottom temporal dock; the map stays full-bleed
and never resizes.

## Context

The Map workspace is a full-bleed Leaflet map (`MapStage` `position:absolute;
inset:0`) with floating overlay panels: `LayerPanel` (left), `SelectionPanel`
(right), and the bottom temporal dock — all `position:absolute; z-index:500`
pinned to the workspace edges. Only the bottom dock reflowed (it already inset
itself past the side panels via `--map-timeline-start/end`). Every other
floating control was pinned to a raw edge and ignored panel width:

- top-center action bar (`left-1/2 -translate-x-1/2`),
- the detection-count badge and the two corner labels,
- the cursor readout (`left-3 bottom-4`) and scale bar (`right-3 bottom-4`),
- the right-edge zoom / recenter / focus / visual cluster (`right:4`, which by
  design *overlapped* the right panel).

So expanding a panel occluded the chrome — the "broken layout shift" the analyst
reported. Requirement: **panels stay floating over a full-bleed map; the chrome
reflows so no panel ever overlaps another control. The map does not resize.**

## Decision

A single source of truth for the space each panel reserves from the workspace
edge, fed to every floating control:

- **`GaiaMap`** computes two CSS length strings from `leftOpen`/`rightOpen`:
  `reserveLeftCss` / `reserveRightCss` = `calc(min(<panel-width>, calc(100cqi -
  1.75rem)) + 1.75rem)` when open, else `4rem` (the 36px collapsed rail +
  gutters). These are the *same* expressions the bottom dock already used, now
  hoisted to one place and passed to BOTH the dock inset
  (`--map-timeline-start/end`) and `MapStage`.
- **`MapStage`** writes `--reserve-left` / `--reserve-right` / `--reserve-bottom`
  onto its inner container (a descendant of `.map-workspace`, so the `cqi` unit
  resolves against the workspace container). All floating chrome positions
  against these vars (`left: var(--reserve-left)`, `bottom:
  var(--reserve-bottom)`, …); band-spanning items (`left`+`right` set) center
  with `width:fit-content; margin-inline:auto` or flex `justify-center`.
- **`--reserve-bottom`** is the measured bottom-dock height + gutters: `GaiaMap`
  attaches a `ResizeObserver` (callback ref `setBottomDockNode`) to whichever
  bottom element is mounted (open timeline OR collapsed pill), so bottom-corner
  chrome always clears the variable-height dock (scrubber, suppression chips,
  restored-hidden banner).
- A `.map-reflow` class adds a 0.18s `left/right/bottom` transition so the
  reflow reads as intentional. Leaflet's attribution is inset via
  `.leaflet-bottom.leaflet-right` margins from the same vars.

### Why this and not the alternatives

- **Not a CSS grid / docked layout.** The analyst explicitly wanted the map to
  stay full-bleed with panels floating over it — only the panels/chrome reflow,
  not the map. A docked grid would resize the map.
- **Not CSS custom props on `.map-workspace` itself.** Container-query length
  units (`cqi`) resolve against an element's *ancestor* container, not itself —
  so `cqi`-based values must live on a descendant. `MapStage`'s inner div is
  that descendant; the bottom dock (a separate descendant) carries its own copy
  of the same strings (single-sourced from `GaiaMap`).
- **Measured `--reserve-bottom`, not a constant.** The dock height varies; a
  fixed reserve would either waste space or let chrome overlap the dock.

## Consequences

- Collapsing/expanding any panel slides the chrome into the freed space; nothing
  is ever occluded; the Leaflet canvas is untouched (still `inset:0`).
- The two side panels keep their existing CSS widths and responsive
  `@container` rules; only their *open/closed* state drives the reserves. At
  <640px both panels auto-collapse (existing behaviour), so reserves shrink to
  rails and the chrome spans the full width.
- Accepted edge: in the narrow 640–672px band (panels open but the 42rem
  breakpoint stacks them) the chrome reserves the wide value — a small extra
  gap, not an overlap. Refine in a later polish pass if needed.

## Cross-references

- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [frontend/workspace-geoint-gaiamap.md](../frontend/workspace-geoint-gaiamap.md)
- [decisions/why-detection-mvt-tiles.md](why-detection-mvt-tiles.md)

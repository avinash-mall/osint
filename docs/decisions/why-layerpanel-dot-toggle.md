# Why the LayerPanel eye-toggle column was removed

**Date:** 2026-05-22
**Status:** Accepted

## Context

The live `LayerPanel.tsx` rendered each overlay row as a 3-column grid:
`[22px eye] [label] [count]`. With eight overlays + the same pattern repeated
inside the Detection Classes tree, the eye column alone consumed ~22×16 px of
horizontal × vertical, and the row height (36 px each) made the overlay
section ~290 px tall before Detection Classes could render.

The colored eye glyph also competed with the category-coloured row text and
counts — operators reported difficulty parsing "what's on" vs "what category
this is" at a glance.

## Decision

The eye / eye-off icon column is removed. A single 10 px coloured dot on
the left of each row is now the visibility indicator:

- **Filled coloured dot** — layer ON, data present.
- **Hollow ring (`var(--ink-3)`)** — layer OFF.
- **Lock glyph** — disabled analytics tool (not run yet).

The full row is still the click target; the dot is the *signal*, not the
*affordance*.

Analytics-only layers (`viewshed`, `los`, `routes`) move into a separate
"Analytics tools" subgroup below the live overlays, so the lock glyph reads
as "needs to be run" rather than "you hid this".

The BASE / SAT / TERRAIN segmented control is replaced in the same pass by a
three-tile thumbnail gallery (`BasemapThumb` in `_icons.tsx`) — hand-painted
SVG previews so the analyst sees the destination before clicking. The active
tile carries a `var(--accent-cool)` outline + check chip.

## Why this design

- **Pattern is conventional in GIS tools.** ArcGIS / Mapbox Studio /
  QGIS use the layer's own icon or a coloured marker as the toggle, and a
  basemap gallery rather than a text segmented control.
- **Removes a colour-on-colour conflict.** Category colour now appears once
  per row instead of twice.
- **Reclaims ~64 px of vertical space** for the Detection Classes tree
  below — currently the most overflow-prone section (overlay row height
  36 → 28 px, stack 288 → 224 px).
- **Disabled-tool semantics survive the change.** A locked row visibly
  differs from a hidden row, so the operator never tries to toggle a
  viewshed that doesn't exist yet.
- **SVG thumbnails keep the air-gap rule.** Painted gradients/paths, no
  tile fetch or image asset at runtime.

## Why a thumbnail gallery, not real tiles

A future enhancement could snapshot the actual basemap and cache it as the
preview, but that needs a render-and-cache path. The hand-painted SVGs are
good enough, have zero runtime cost, and never touch the network.

## Cross-references

- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [decisions/ux-audit-001.md](ux-audit-001.md) — same audit thread
- [decisions/why-bbox-toggle-removed.md](why-bbox-toggle-removed.md) —
  precedent for removing redundant on/off controls

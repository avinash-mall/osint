# LayerPanel: "Tactical Grid" split into Borders and Graticule

## What changed

The LayerPanel toggle previously labeled `Tactical Grid` (state key `grid`) actually drew administrative/country GeoJSON outlines — it had nothing to do with a coordinate grid. It is now split into two distinct toggles:

| Old | New |
|---|---|
| `grid` (label "Tactical Grid") | `borders` (label "Borders") — admin/country GeoJSON, unchanged behaviour |
| — | `graticule` (label "Graticule") — true WGS84/MGRS coordinate grid |

## Why

In a military GEOINT workstation, "Grid" refers to a coordinate graticule (degree lines, MGRS bands). Mislabeling country borders as "Grid" causes operators to look for spatial reference lines and not find them; conversely, anyone wanting to turn off political boundaries can't find the right toggle. Both layers are valuable — the fix is to give each one its own name and a separate toggle.

## Implementation

- `ActiveLayerMap`: `grid: boolean` removed; `borders: boolean` + `graticule: boolean` added.
- [frontend/src/components/map/LayerPanel.tsx](../../frontend/src/components/map/LayerPanel.tsx): the legacy row replaced with two rows.
- [frontend/src/components/map/MapStage.tsx](../../frontend/src/components/map/MapStage.tsx): borders block now keys on `activeLayers.borders`; new block renders `<MgrsGraticule />` when `activeLayers.graticule` is true.
- New: [frontend/src/components/map/MgrsGraticule.tsx](../../frontend/src/components/map/MgrsGraticule.tsx) — pure react-leaflet + the existing `mgrs` package. No new dependency was added (offline-safe per CLAUDE.md hard rule #8).
- [frontend/src/components/GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx): default state `borders: true, graticule: false`.

## Why no `leaflet-simple-graticule` plugin

The original plan considered a plugin. We chose a custom in-house component instead: keeps total dep count down, avoids a transitive `leaflet@x.y` mismatch risk, and lets us re-use the already-vendored `mgrs` package for accurate band drawing at high zoom.

## Cross-references

- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [decisions/why-layerpanel-dot-toggle.md](why-layerpanel-dot-toggle.md)

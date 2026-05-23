# Temporal swipe comparator

## What changed

A draggable side-by-side imagery comparator now overlays the map when the operator pins a second pass via the TimeMachineBar (`Compare` button, or alt/shift-click on a pass diamond). The compare layer renders into a dedicated Leaflet pane (`sentinel-compare`) and is clipped horizontally with CSS `clip-path: inset(0 0 0 N%)` driven by a pointer-dragged divider chip.

## Why

Operators previously had to flip back and forth between TimeMachineBar ticks to spot change between two passes — taxing for human reconnaissance. A spatial swipe lets a single glance compare imagery at the same lat/lon at two timestamps (before/after airbase strikes, vehicle movement, construction).

## Why no plugin (`leaflet-side-by-side`)

The plan called for the standard `leaflet-side-by-side` plugin. We dropped it because:

- The behaviour required is simple: clip a single Leaflet pane along a vertical line and let the operator drag the line.
- Implementing it as a 100-line React component (`SwipeControl.tsx`) avoids a new transitive dep, lets us match the dark workstation theme (orange accent + ⇆ chip), and lets the divider live on the React side where it composes cleanly with the rest of the floating chrome.
- One fewer build-time download in an air-gapped build.

## Implementation

- New file: [frontend/src/components/map/SwipeControl.tsx](../../frontend/src/components/map/SwipeControl.tsx). Creates a custom Leaflet pane on mount, mounts a `<TileLayer pane="sentinel-compare">` into it, and maintains `frac` ∈ [0,1] for the clip-path inset. Cleanup removes the clip.
- [frontend/src/components/map/TimeMachineBar.tsx](../../frontend/src/components/map/TimeMachineBar.tsx): added `Compare` chip + button; alt/shift-click on a diamond toggles compare pin.
- [frontend/src/components/GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx): `compareImageryId` lives here and is passed both to `MapStage` (as the imagery object via `imagery.find`) and to `TimeMachineBar`.

## Cross-references

- [frontend/map-time-machine.md](../frontend/map-time-machine.md)
- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)

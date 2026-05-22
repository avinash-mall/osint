# Basemap Composition — Imagery Is Ground Truth, BASE/TERRAIN Are Overlays

## Decision

In `MapStage.tsx` the SAT / BASE / TERRAIN basemap modes now compose as an
**ordered, zIndex-explicit stack**:

1. **SAT imagery** — the COG `TileLayer`, `zIndex={200}`, full opacity. Renders
   whenever an imagery scene is loaded, in *every* mode.
2. **Cartographic fallback** — Carto or Terrain at `zIndex={100}`, full opacity,
   rendered **only when no imagery is loaded** so the stage is never empty.
3. **Reference overlay** — Carto (`base`) or Terrain (`terrain`) at
   `zIndex={300}`, *above* the imagery, rendered only in BASE/TERRAIN mode with
   imagery present. The LayerPanel opacity slider fades this overlay.

**Removed:** the `lastNonSatBaseRef` `useRef` + `useEffect` + `effectiveBase`
machinery in `MapStage.tsx`.

## Why

The previous logic was inverted relative to the analyst's mental model:

- **SAT mode** kept a cartographic basemap rendered *underneath* the imagery
  (via `lastNonSatBaseRef`, which "remembered" the last non-SAT base). The
  analyst asked for bare imagery; they got two layers.
- **BASE / TERRAIN mode** rendered the cartographic basemap *alone* — the SAT
  `TileLayer` was gated by `activeBaseLayer === 'sat'`, so picking BASE hid the
  imagery entirely.

`lastNonSatBaseRef` was a patch for a symptom: someone noticed SAT-alone was
empty when no imagery was loaded and kept a basemap underneath instead of
fixing the composition. It also carried hidden state — switching modes twice
could change what rendered under SAT — a "why does this look different now?"
bug class.

The intended model matches Google Maps "Satellite + Labels" / ArcGIS reference
layers: **imagery is the ground truth at the bottom, the cartographic basemap
is a reference overlay on top, the opacity slider fades the reference.**

## Resulting behaviour

| Mode    | Imagery loaded                  | No imagery loaded          |
| ------- | ------------------------------- | -------------------------- |
| SAT     | imagery only                    | cartographic fallback 100% |
| BASE    | imagery + Carto overlay on top  | Carto fallback 100%        |
| TERRAIN | imagery + Terrain overlay on top| Terrain fallback 100%      |

The opacity slider drives the overlay in BASE/TERRAIN; in SAT mode it is
disabled (imagery always renders at 100%) — see the slider label in
`LayerPanel.tsx` (`IMAGERY` vs `<MODE> OVERLAY`).

Detection / track / analytics layers live in Leaflet's `overlayPane` (above
`tilePane`), so they stay visible above the zIndex-300 reference overlay.

## Notes

- `layerOpacities` no longer carries a `sat` key — SAT imagery always renders
  at full opacity, so there is nothing to store. The slider keys on
  `'base' | 'terrain'` (`opacityLayer` in `LayerPanel.tsx`) and is disabled in
  SAT mode.

## Cross-references

- [map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [why-sat-tiles-cap-at-native-zoom.md](why-sat-tiles-cap-at-native-zoom.md)
- [why-layerpanel-dot-toggle.md](why-layerpanel-dot-toggle.md)

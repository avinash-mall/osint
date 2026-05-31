# `SatellitesPanel.tsx` — overpass planning UI

**Path:** [frontend/src/components/map/SatellitesPanel.tsx](../../frontend/src/components/map/SatellitesPanel.tsx)
**Lines:** ~210
**Depends on:** [services/satellites.ts](../../frontend/src/services/satellites.ts), `lucide-react`; hosted by [SelectionPanel.tsx](../../frontend/src/components/map/SelectionPanel.tsx) (the "Sat" tab) and orchestrated by [GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx).

## Purpose

The "Sat" tab in the right selection panel: import TLEs (air-gap), pick an
observer on the map, predict upcoming overpasses (AOS/LOS/max-elevation), and
draw a satellite's sub-satellite ground track on the map. Backed by the offline
SGP4 service — see [satellites-router.md](../backend-routers/satellites-router.md).

## Why this design

- **Panel is decoupled from the host.** `SelectionPanel` renders it via a
  `satellitesSlot` React node supplied by `GaiaMap`, so `SelectionPanel` never
  imports the satellites service. `GaiaMap` owns the cross-cutting state
  (observer point, pick mode, ground track) because the map (`MapStage`) must
  render the track and the pick handler.
- **Reuses the analytics pick channel pattern.** Observer selection rides the
  same `AnalyticsPickHandler` mechanism as viewshed/LOS (a second instance in
  `MapStage`, gated by `satPickActive`), rather than inventing a new click path.
- **Ground track stored as `[lon, lat]`** (GeoJSON order) and flipped to
  `[lat, lon]` only at the Leaflet `<Polyline>` boundary in `MapStage`.

## Key symbols

- `SatellitesPanel({ observer, onRequestPick, pickActive, onGroundTrack })`.
- TLE import textarea → `importTle`; `PREDICT` → `predictPasses`; per-satellite
  `TRACK` → `getGroundTrack` → `onGroundTrack`.
- Host wiring in `GaiaMap`: `satObserver` / `satPickActive` / `satGroundTrack`
  state; `onSatPick` (MapStage) sets the observer and clears pick mode.

## Inputs / Outputs

- **In:** picked observer `{lat, lon}`, pick-active flag.
- **Out:** calls `onGroundTrack([lon,lat][])` for `MapStage` to draw; all
  prediction/import happens via the satellites service.

## Failure modes

- No TLEs stored → the service 404s; the panel surfaces the detail and shows a
  `0 TLE` count. Import resolves it.
- Predict with no observer → inline "Pick an observer point first".

## Cross-references

- Service: [frontend/src/services/satellites.ts](../../frontend/src/services/satellites.ts)
- Backend: [backend-routers/satellites-router.md](../backend-routers/satellites-router.md), [backend/satellite-overpass.md](../backend/satellite-overpass.md)
- Map host: [map-selection-panel.md](map-selection-panel.md), [map-stage-and-layers.md](map-stage-and-layers.md)
- Tour: [product-tour.md](product-tour.md) (`satellites-tab` step)

# Live MGRS cursor readout

## What changed

The bottom-left cursor coordinate chip in `MapStage` used to render a hardcoded `MGRS AUTO` placeholder. It now converts the live `(cursor.lat, cursor.lon)` to MGRS on every mousemove via `mgrs.forward([lon, lat], 5)`.

## Why

Defense analysts communicate positions almost exclusively in MGRS for joint fire support, target coordinate mensuration, and team-wide navigation. A placeholder string is an operational risk in a workstation that ships for tactical use. The `mgrs` package was already vendored on the frontend (used by `SelectionPanel.tsx` for detection-centroid conversion), so reuse — not a new dependency — was the right move.

## Implementation

- [frontend/src/components/map/MapStage.tsx](../../frontend/src/components/map/MapStage.tsx) imports `forward as mgrsForward` from `mgrs` and inlines the conversion in the JSX of the cursor chip (wrapped in a try/catch that falls back to `n/a` outside UTM/UPS zones).

## Cross-references

- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)

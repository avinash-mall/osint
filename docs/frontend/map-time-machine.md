# TimeMachineBar — Temporal Slider

**Path:** [frontend/src/components/map/TimeMachineBar.tsx](../../frontend/src/components/map/TimeMachineBar.tsx)
**Lines:** ~8534 characters

## Purpose

A scrubbable range slider at the bottom of the Geoint workspace. Drives the `(start_time, end_time)` filter applied to detections, satellite passes, and asset tracks.

## Behavior

- **Two thumbs** (start, end) on a continuous date axis.
- **Auto-scope** to the time range of currently loaded data (oldest to newest detection).
- **Quick-presets**: last hour, last 24 h, last week, custom.
- **Playback button** advances the end thumb in time-step increments to replay observations.

## Cross-references

- [workspace-geoint-gaiamap.md](workspace-geoint-gaiamap.md)
- [map-stage-and-layers.md](map-stage-and-layers.md)

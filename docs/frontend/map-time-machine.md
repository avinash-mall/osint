# TimeMachineBar — Temporal Slider

**Path:** [frontend/src/components/map/TimeMachineBar.tsx](../../frontend/src/components/map/TimeMachineBar.tsx)
**Lines:** ~8534 characters

## Purpose

A scrubbable range slider at the bottom of the Geoint workspace. Drives the `(start_time, end_time)` filter applied to detections, satellite passes, asset tracks.

## Behavior

- **Two thumbs** (start, end) on a continuous date axis.
- **Auto-scope** to the time range of currently loaded data (oldest to newest detection).
- **Quick-presets**: last hour, last 24 h, last week, custom.
- **Playback button** advances the end thumb in time-step increments to replay observations.
- **Playhead tooltip** — hovering or keyboard-focusing the scrubber shows the exact ISO timestamp under the playhead via a `.timeline-tip` (UX-AUDIT F15); the range input also exposes it through `aria-valuetext`.
- **Compare / side-by-side** — a `Compare` button beside the playhead pins the prior pass; alt/shift-click any pass diamond pins/un-pins that pass into the compare slot. The map composes the second imagery layer into a clipped pane controlled by a draggable divider; see [map-temporal-swipe-comparator.md](map-temporal-swipe-comparator.md) and [decisions/temporal-swipe-comparator.md](../decisions/temporal-swipe-comparator.md).

## Cross-references

- [workspace-geoint-gaiamap.md](workspace-geoint-gaiamap.md)
- [map-stage-and-layers.md](map-stage-and-layers.md)
- [decisions/ux-audit-001.md](../decisions/ux-audit-001.md)

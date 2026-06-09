# TimeMachineBar — Temporal Slider

**Path:** [frontend/src/components/map/TimeMachineBar.tsx](../../frontend/src/components/map/TimeMachineBar.tsx)
**Lines:** ~8534 characters

## Purpose

A scrubbable range slider at the bottom of the Geoint workspace. Drives the `(start_time, end_time)` filter applied to detections, satellite passes, asset tracks.

## Behavior

- **Two thumbs** (start, end) on a continuous date axis.
- **Auto-scope** to the time range of currently loaded data (oldest to newest detection).
- **Quick-presets**: last hour, last 24 h, last week, custom.
- **Playhead drives imagery** — scrubbing (or clicking a pass diamond) selects the imagery pass nearest under the playhead. GaiaMap owns the wiring (`tmPassFracs` memo + a select-nearest effect); the bar is presentational.
- **Playback button** steps the playhead through the imagery passes oldest→newest (~1.2 s each), selecting each in turn, then stops at the newest. (Previously presentational — the button only toggled its icon.)
- **Playhead tooltip** — hovering or keyboard-focusing the scrubber shows the exact ISO timestamp under the playhead via a `.timeline-tip` (UX-AUDIT F15); the range input also exposes it through `aria-valuetext`.
- **Compare / side-by-side** — a `Compare` button beside the playhead pins the prior pass; alt/shift-click any pass diamond pins/un-pins that pass into the compare slot. The map composes the second imagery layer into a clipped pane controlled by a draggable divider; see [map-temporal-swipe-comparator.md](map-temporal-swipe-comparator.md) and [decisions/temporal-swipe-comparator.md](../decisions/temporal-swipe-comparator.md).
- **Change detection** — when a compare pass is pinned, a `CHANGE` button opens [ChangeDetectionDialog](map-change-detection-dialog.md) for the active-vs-compare pair (ordered by acquisition time).
- **Event-timeline Play = live-follow** — the separate bottom event-timeline's Play button auto-refreshes detections every 5 s so the density strip advances in real time (was presentational).
- See [decisions/completed-deferred-items-2026-06-09.md](../decisions/completed-deferred-items-2026-06-09.md).

## Cross-references

- [workspace-geoint-gaiamap.md](workspace-geoint-gaiamap.md)
- [map-stage-and-layers.md](map-stage-and-layers.md)
- [decisions/ux-audit-001.md](../decisions/ux-audit-001.md)

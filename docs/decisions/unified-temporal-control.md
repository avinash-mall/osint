# Why the bottom dock is one control, not two timelines

**Status:** shipped (2026-06-13). The bottom dock now reads as a single temporal
control — the imagery scrubber with a subordinate activity sparkline — instead
of two stacked timeline widgets.

## Context

The bottom dock stacked two things that both *looked* like full timelines, each
with its own play button and header:
- **TimeMachineBar** — the imagery-acquisition scrubber (diamonds per pass,
  play steps through passes, range 24h/7d/30d).
- **Event-timeline block** — a 60-bucket detection-activity histogram with its
  own play (live-follow auto-refresh), refresh, and "Event timeline · last Nm"
  header.

The analyst read this as "two redundant timelines."

## Decision

Reframe as **one dock = imagery scrubber + a subordinate activity sparkline**,
rather than forcing a literal single-axis merge.

- The event block keeps its histogram but loses its heavy header: a slim
  `ACTIVITY · LAST {N}M` caption (muted, 9.5px), with the live-follow play and
  refresh as `xs` icons, and the in-window counter inline. It now visually reads
  as a sub-strip of the dock, not a co-equal second widget.
- The two play buttons are disambiguated: the scrubber's play steps through
  imagery passes; the activity play is explicitly "Live follow — auto-refresh
  detections."
- The confidence floor and the 15/30/60m window selector were already moved out
  of the dock into the left-rail Filters section (see
  [centralized-filter-surface.md](centralized-filter-surface.md)), so the dock
  now carries only temporal controls.

### Why NOT a single shared axis

The two pieces measure **different time spans**: the scrubber spans the imagery
range (24h/7d/30d ending now); the histogram spans the last-N-minutes activity
window (≤60 buckets). Painting the 60-minute activity as a backdrop behind a
24-hour rail would misrepresent the data (60 min stretched across 24 h). They
are legitimately distinct axes — so the right declutter is visual hierarchy
(one dock, one slim header, subordinate sparkline), not a false overlay.

## Consequences

- One temporal dock with clear hierarchy; the "two timelines" perception is
  gone without distorting either axis.
- `TimeMachineBar` internals are untouched (lower risk); only the event block's
  chrome was slimmed.
- Tour: the `event-timeline` step is retitled "Activity sparkline"; `tm-conf`
  and `event-windows` already moved to the Filters section.

## Cross-references

- [frontend/map-time-machine.md](../frontend/map-time-machine.md)
- [frontend/workspace-geoint-gaiamap.md](../frontend/workspace-geoint-gaiamap.md)
- [decisions/centralized-filter-surface.md](centralized-filter-surface.md)

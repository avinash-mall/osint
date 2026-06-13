# Why a single Filters section in the left rail

**Status:** shipped (2026-06-13). The confidence floor and the recent-activity
window moved into a new "Filters" section in `LayerPanel`.

## Context

Detection filters were scattered across three places:
- **Confidence floor** — a `CONF` slider buried in the bottom `TimeMachineBar`.
- **Recent-activity window** (15 / 30 / 60 m) — segmented buttons in the
  bottom event-timeline strip.
- Category visibility + label search + ALL/NONE/INV — in the detection-class
  tree header (left rail).

So "what's shown on the map" was spread between the bottom dock and the left
rail, and the confidence control in particular was easy to miss.

## Decision

Add a **Filters** section to `LayerPanel` (between Overlays and Detection
Classes) holding the **confidence floor slider** (`data-tour="filter-conf"`)
and the **window selector** (`data-tour="filter-time-window"`). The class
tree's search + bulk toggles stay where they are (physically part of the tree)
but now sit directly below the Filters block, so all filter affordances are
co-located in the left rail.

- State is still owned by `GaiaMap` (`confidenceThreshold` / `timelineWindowMinutes`
  + `setRecentWindow`); `LayerPanel` just receives them as props. No new state
  owner, no behavioural change to the query/threshold logic.
- The `CONF` control was **removed** from `TimeMachineBar` (and its
  `confidence`/`onConfidenceChange` props); the window selector was **removed**
  from the event-timeline strip (the "last Nm" readout label stays).
- Product tour: `tm-conf` → `filter-conf`, `event-windows` → `filter-time-window`;
  `onStepChange` opens the left panel for `filter-*` steps.

### Why

- Filters belong with the operating-picture rail ("what's on the map"), not the
  temporal scrubber. One surface = fewer places to hunt for a control.
- Keeping state in `GaiaMap` makes this a pure relocation — lowest-risk way to
  centralize without touching the filtering pipeline.

## Consequences

- The bottom dock is lighter (no CONF slider, no window buttons), which also
  sets up the temporal-control merge (see plan Phase 2c).
- The window still drives both the detection query and the event-histogram
  colour-coding via the shared `timelineWindowMinutes` state.

## Cross-references

- [frontend/workspace-geoint-gaiamap.md](../frontend/workspace-geoint-gaiamap.md)
- [frontend/map-time-machine.md](../frontend/map-time-machine.md)
- [decisions/analytics-relocated-to-right-tab.md](analytics-relocated-to-right-tab.md)

# Frontend audit — Map workspace fixes (2026-06-11)

**Date:** 2026-06-11 (completed 2026-06-12)
**Status:** adopted

## Context

A correctness audit of the Map workspace (GaiaMap orchestrator + `map/` panel
components) surfaced 17 verified defects: two crash-the-React-root bugs in the
swipe comparator, a cluster of stale-response races (no out-of-order guards on
any async fetch), time-machine effects re-running on array-identity churn,
filter-parity gaps between the marker path and the MVT tile path, and several
smaller display/UX defects. All 17 were fixed; none skipped.

## Fixed

**SwipeControl** ([SwipeControl.tsx](../../frontend/src/components/map/SwipeControl.tsx))
- The compare `<TileLayer>` mounted before the parent effect had created the
  custom `sentinel-compare` pane — react-leaflet 5 runs the CHILD's effect
  first, so `getPane()` was undefined inside `GridLayer._initContainer` and the
  first compare-pin threw, unmounting the whole app. The pane is now created
  synchronously during render (idempotent guard).
- The divider chip was re-parented imperatively out of React's host parent
  (`mapEl.parentElement.appendChild`), so React's own unmount `removeChild`
  threw `NotFoundError` on "Exit compare". It now renders via `createPortal`
  into the map container's parent — no imperative re-parenting.

**GaiaMap time machine** ([GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx))
- The snap-to-nearest-pass effect depended on `[tmValue, tmPassFracs]`, and
  `tmPassFracs` gets a new identity on every imagery refetch (`fetchImagery`
  also closed over `selectedImagery`, so every manual pass selection triggered
  a refetch) — ANY manual pass selection snapped straight back to the newest
  pass. The effect now reads stops from `tmPassFracsRef` and depends on
  `[tmValue]` only; `fetchImagery` reads the current selection from
  `selectedImageryRef` (functional setter) instead of closing over it.
- The Play effect depended on `tmPassFracs` and advanced the playhead
  immediately on every re-run → playback at network speed, wrapping to the
  oldest pass forever (`findIndex` −1 → `i = 0`). Stops are snapshotted from
  the ref at play start, advances happen ONLY from the 1.2 s interval tick,
  and playback ends with `setTmPlaying(false)` past the last stop.

**GaiaMap navigation & races** ([GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx))
- `crossNav` (Open on GEOINT) and the `sentinel:jump-to-detection` event were
  consumed by searching the viewport GeoJSON, which is empty on a fresh mount —
  the jump silently did nothing. A shared `jumpToDetection(id, lat?, lon?)`
  now fetches `GET /api/detections/{id}/enriched` (bbox-independent), selects,
  pans to the returned geometry (falls back to flying to the supplied
  lat/lon), and the crossNav intent is consumed only after the fetch settles
  (ref guard prevents double-handling).
- `fetchDetectionFeatures` / `fetchDetectionTracks` / `fetchDetectionClasses` /
  `fetchImagery` / `selectDetectionById` had no out-of-order guards — a slow
  stale response (e.g. a world-bbox query) overwrote newer results. Each now
  bumps a per-callback monotonic ref token on entry and drops the response if
  a newer call has started. Tokens were chosen over `AbortController` because
  axios cancellation adds error-path branching at every call site for the same
  guarantee.

**MVT tile / marker filter parity** ([DetectionTileLayer.tsx](../../frontend/src/components/map/DetectionTileLayer.tsx), [MapStage.tsx](../../frontend/src/components/map/MapStage.tsx), [_helpers.ts](../../frontend/src/components/map/_helpers.ts))
- The tile layer ignored `hiddenDetectionLabels` (eye-toggled classes hid
  their markers but their boxes stayed) and thresholded calibrated confidence
  while markers used raw. `hiddenDetectionLabels` now flows GaiaMap → MapStage
  → DetectionTileLayer and hides features whose `class` / `parent_class` /
  `original_class` / `label` intersect the hidden set; BOTH paths read
  `calibrated_confidence ?? confidence` (defensive — the MVT SQL already
  COALESCEs it into the tile's `confidence` prop).
- SOLO parity: the UI sends the displayed label (which prefers
  `original_class`) but tiles matched `props.class` only — soloing a refined
  class blanked the map. The tile filter now matches against the leaf-class
  ladder (`original_class` / `class` / `label`). (The backend OR-match side
  was handled in the API-layer audit.)

**MgrsGraticule** ([MgrsGraticule.tsx](../../frontend/src/components/map/MgrsGraticule.tsx))
- The high-zoom accent grid was anchored to the viewport edge (never snapped),
  so it slid with every pan and never matched real ground lines. Lines are now
  anchored to fixed ground origins (`floor(min/step)*step`, same scheme as the
  degree graticule). True UTM/MGRS cell anchoring was NOT attempted: the
  vendored `mgrs` package exposes no clean lat/lon→UTM inverse, so the grid is
  degree-quantized at MGRS spacings and the header comment now says exactly
  that (`mgrsForward` remains a UTM/UPS-coverage probe only).

**Panel stale-response races**
- [IdentificationPanel](../../frontend/src/components/map/IdentificationPanel.tsx)
  kept a stale candidate list under a new detection's header (approve could
  write platform identity onto the WRONG detection). Fixed at the call site:
  [SelectionPanel](../../frontend/src/components/map/SelectionPanel.tsx)
  mounts it with `key={'ident-'+detectionId}`, so changing the selection
  REMOUNTS the panel and resets its state — chosen over an in-panel sequence
  token because the panel's whole state (candidates, error, busy) is
  per-detection, and a remount resets all of it structurally.
- [SimilarPanel](../../frontend/src/components/map/SimilarPanel.tsx) clears
  results at load start and guards with a sequence token (anchor can change
  faster than `/similar` resolves).
- [ReviewPanel](../../frontend/src/components/map/ReviewPanel.tsx) guards the
  queue load with a sequence token keyed to the status tab (a quick
  PENDING→ACCEPTED switch no longer lets the stale tab's response win).

**SelectionPanel** ([SelectionPanel.tsx](../../frontend/src/components/map/SelectionPanel.tsx))
- Review-queue / Similar tile clicks were a silent no-op when the detection
  wasn't in the viewport GeoJSON (both feeds are global). They now route
  through `onJumpToDetection(id, lat, lon)` → GaiaMap's `jumpToDetection`
  (enriched fetch + pan/fly) — never a silent return.
- The WGS84 readout hardcoded "° N, ° E"; it now formats hemispheres from the
  sign with `Math.abs()` (`33.8600° S`, not `-33.8600° N`).
- Target Package export failures only `console.warn`ed while the button
  silently reset; an inline red error chip (`exportError`) now renders under
  the Generate button, matching the panel's existing error styling.

**SatellitesPanel** ([SatellitesPanel.tsx](../../frontend/src/components/map/SatellitesPanel.tsx))
- `hours <= 6 ? hours : 1.5` made every window over 6 h silently fall back to
  the service's 1.5 h ground-track default (a 24 h slider drew a 1.5 h
  track). Now `Math.min(hours, 6)` with an inline "Ground-track length caps at
  6h" note when the slider exceeds the cap.

**Allegiance spelling tolerance** ([atoms.tsx](../../frontend/src/components/atoms.tsx), [SelectionPanel.tsx](../../frontend/src/components/map/SelectionPanel.tsx))
- The tag endpoint historically stored `friendly`; newer writes are normalised
  to `friend`. `natoTagClass` and `AffGlyph` accept both, and the
  SelectionPanel header chip maps either spelling to the friend style — old DB
  rows keep rendering correctly.

## Cross-references

- [frontend/workspace-geoint-gaiamap.md](../frontend/workspace-geoint-gaiamap.md)
- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [frontend/map-selection-panel.md](../frontend/map-selection-panel.md)
- [frontend/map-review-similar-provenance.md](../frontend/map-review-similar-provenance.md)
- [frontend/identification-panel.md](../frontend/identification-panel.md)
- [frontend/map-satellites-panel.md](../frontend/map-satellites-panel.md)
- [decisions/temporal-swipe-comparator.md](temporal-swipe-comparator.md) — the comparator this pass repaired
- [decisions/audit-fixes-frontend-shell-admin-2026-06-12.md](audit-fixes-frontend-shell-admin-2026-06-12.md) — sibling Shell/admin pass
- [decisions/audit-fixes-api-layer-2026-06-11.md](audit-fixes-api-layer-2026-06-11.md) — backend SOLO OR-match counterpart

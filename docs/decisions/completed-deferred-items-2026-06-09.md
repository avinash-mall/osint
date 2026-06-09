# Completed: deferred audit items (2026-06-09)

**Date:** 2026-06-09
**Status:** adopted

## Context

The codebase-correctness audit ([audit-fixes-codebase-correctness-2026-06-08.md](audit-fixes-codebase-correctness-2026-06-08.md))
deferred a set of items that changed detection sensitivity, were larger features,
or were bake-script hardening. This closes them. Each was validated against the
running stack / test suites (inference 99 passed, backend 336 passed, frontend
build clean).

## Detection sensitivity (validated, env-tunable, benchmark-flagged)

- **Presence-ratio gate asymmetry** — see the companion
  [why-batched-presence-gate-floor.md](why-batched-presence-gate-floor.md). The
  batched text path postprocesses at a low gate floor so the SegEarth ratio gate
  sees the full distribution; emitted detections still filter at `score_threshold`.
- **CFAR variance** ([sar_cfar.py](../../backend/sar_cfar.py)) — `var_bg` used the
  guard-**inclusive** mean/second-moment while the Z-score used the guard-**excluded**
  `mu_clutter`, inflating σ next to bright targets and depressing the Z-score where
  detections live. Now the variance is the guard-excluded second moment minus
  `mu_clutter²` (same proportional guard subtraction as the mean). Synthetic check:
  a +0 dB target on −18 dB clutter is detected sharply with zero false alarms on
  pure clutter; `test_change_detection_sar` + `test_calibration` green.

## Larger features

- **Time-machine + event-timeline playback** ([GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx)).
  The TimeMachineBar was presentational. Now: scrubbing/stepping the playhead
  selects the imagery pass nearest under it; **Play** steps through the passes
  oldest→newest (~1.2 s each) then stops; the event-timeline **Play** is a
  live-follow that auto-refreshes detections every 5 s so the density strip
  advances in real time. See [map-time-machine.md](../frontend/map-time-machine.md).
- **ChangeDetectionDialog mounting + generic overlay subsystem**
  ([ChangeDetectionDialog.tsx](../../frontend/src/components/map/ChangeDetectionDialog.tsx),
  [MapStage.tsx](../../frontend/src/components/map/MapStage.tsx),
  [TimeMachineBar.tsx](../../frontend/src/components/map/TimeMachineBar.tsx)). The
  dialog was never mounted and its `sentinel:overlay-geojson` handoff had no
  listener. Now: pinning a compare pass surfaces a **CHANGE** button that opens the
  dialog for the active-vs-compare pair (ordered by acquisition time); MapStage
  listens for `sentinel:overlay-geojson` (and `sentinel:overlay-clear`), renders the
  result as a generic GeoJSON overlay layer with a dismissible chip, and flies to
  its bounds. See [map-change-detection-dialog.md](../frontend/map-change-detection-dialog.md).
- **AI execute target-resolution** ([ai.py](../../backend/routers/ai.py)).
  `execute_action_proposal`'s queued viewshed ignored the proposal `target_id` and
  ran at `run_viewshed`'s default observer. A new `_resolve_target_observer` derives
  the observer from the centroid of the detections accepted/confirmed as that target
  and passes it; if unresolvable the analytic is skipped with a warning rather than
  run at the wrong place.

## Bake-script hardening

- **z14 OOM** ([build_offline_basemap.py](../../scripts/build_offline_basemap.py),
  [build_offline_terrain.py](../../scripts/build_offline_terrain.py)). Submitting
  every `4**z` future up front (z=14 → ~268 M Future objects) exhausted host memory.
  Both bakers now use a bounded sliding window (`max(concurrency*4, 64)` in-flight),
  feeding the next coordinate each time one completes.
- **Idempotency gaps.** `fetch_real_datasets.py` now honours its documented
  per-dataset idempotency (skip when `labels.json` already has ≥ N records; `--force`
  overrides). `fetch_reference_datasets.py::_fetch_dropin_only` now gates on a
  content digest (relative paths + sizes) of the drop-in tree instead of stamping on
  entry count alone, so unchanged trees are no-ops and changed ones refetch.

## Cross-references

- [audit-fixes-codebase-correctness-2026-06-08.md](audit-fixes-codebase-correctness-2026-06-08.md)
- [why-batched-presence-gate-floor.md](why-batched-presence-gate-floor.md)
- [backend/sar-cfar-detector.md](../backend/sar-cfar-detector.md)
- [frontend/map-time-machine.md](../frontend/map-time-machine.md)
- [frontend/map-change-detection-dialog.md](../frontend/map-change-detection-dialog.md)
- [backend-routers/ai-router.md](../backend-routers/ai-router.md)

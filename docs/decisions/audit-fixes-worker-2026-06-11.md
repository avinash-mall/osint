# Celery worker audit — fixes (2026-06-11)

**Date:** 2026-06-11
**Status:** adopted

## Context

A correctness audit of the Celery worker layer surfaced fourteen verified
defects in [backend/worker_legacy.py](../../backend/worker_legacy.py):
silent coverage gaps in the chip planner, transaction poisoning on shared
cursors, a worker-boot NameError, an import-time-frozen detection policy,
FMV clips reported "complete" with zero work done, SAR rasters read whole
into RAM, fused geometry diverging from persisted geometry, and two beat
tasks that were documented but never implemented. All fixes are confined to
`worker_legacy.py`, new worker tests, and the worker/operations docs.

## Fixed

**Chip planner left never-analyzed strips** — `plan_inference_grid` snapped
chip origins DOWN to the COG block grid unbounded; with chip 1008 / overlap
252 / block 512 the snapped step alternated 512/1024 px, so the 1024 step
exceeded the chip size and left recurring ~16 px unscanned strips on both
axes while `coverage_fraction` reported 1.0. The planner now returns
`x_window_sizes`/`y_window_sizes` — each window extended by its snap delta
(`min(chip_size + (raw - snapped), dim - snapped)`) so every chip ends where
the un-snapped chip would have. `slice_and_infer`'s reader path consumes the
per-chip sizes. Regression test:
[backend/tests/test_plan_inference_grid.py](../../backend/tests/test_plan_inference_grid.py).

**`worker.project_label_of_edges` was a permanent no-op** — it read
`getattr(norm, "object_id", None)` but `NormalizedLabel`'s field is
`ontology_object_id`, so no LABEL_OF edge was ever built. Field name fixed.

**FMV revert loaded the wrong inference profile** — `process_fmv`'s
`finally` called `_revert_inference_profile(session, "imagery")`, overriding
the helper's `"imagery_rgb"` default and contradicting
[why-revert-inference-after-fmv.md](why-revert-inference-after-fmv.md)
(the full imagery union OOMs tight-VRAM cards). Now uses the default.

**AOI-allegiance lookup poisoned the store batch** — `_aoi_default_allegiance_at`
swallowed SQL errors on the live `store_detections` batch cursor with no
SAVEPOINT; one error aborted the surrounding transaction and every later
detection INSERT failed with `InFailedSqlTransaction` while ingest reported
success. The SELECT now runs under a `SAVEPOINT aoi_allegiance` with
`ROLLBACK TO SAVEPOINT` on error, plus a per-batch centroid-rounded cache so
spatially clustered detections don't pay one SELECT each.

**Worker wouldn't boot on malformed env JSON** — module-scope
`_load_per_class_valid_fractions()` logged in its except branch, but
`logger` was defined ~40 lines later, so a malformed
`PER_CLASS_MIN_VALID_FRACTIONS` raised NameError at import. `logger` is now
defined above the first module-scope caller.

**Corrupt FMV ended "complete" with 0 detections** — when every
`_prepare_tracking_window` returned None, the clip sailed through to
`tracking_status="complete"`. Empty `sliced` with non-empty `windows` now
raises (the existing except path marks the clip failed); partial extraction
failures are recorded as `tracking_windows_failed` in clip metadata.

**One NDJSON hiccup discarded the whole clip** — `_drain_response_entries`
ran outside any retry/per-line guard, so a mid-stream inference self-heal
reset or one malformed line failed the clip and skipped `consolidate_fmv`.
Each (window, prompt) task now retries once after
`_wait_for_inference_healthy()` (mirrors the imagery chip retry,
[why-retry-chips-across-inference-restart.md](why-retry-chips-across-inference-restart.md));
`json.loads` is guarded per line (unparseable trailing fragments are skipped
with a warning); the clip fails only when the failed-task fraction exceeds
`FMV_MAX_FAILED_TASK_FRACTION` (default 0.05).

**SAR CFAR read whole bands into RAM** — `run_sar_cfar_for_pass` read entire
bands (~1.7 GB/band for S1 GRD float32) before chipping. Bands are now read
per chip window; the global dB-vs-linear decision is made once per band from
a downsampled `out_shape` (≤1024²) probe read.

**Single-band GRDs took the optical path** — `_emit_chip_payload`'s VV/VH
band-description heuristic missed single-band GRDs and rasters with stripped
descriptions, so their chips shipped as `modality=rgb`. The operator/pipeline
modality from ingest metadata is now passed as an authoritative
`modality_hint`; `hint == "sar"` forces the SAR GeoTIFF branch.

**Documented-but-nonexistent beat tasks** —
`worker.cleanup_old_observations` is now real: an hourly beat task pruning
`observations` (by `observed_at`) and `timeline_events` (by `occurred_at`)
older than `OBSERVATION_RETENTION_DAYS` (default 30), interval
`OBSERVATION_CLEANUP_INTERVAL_S`. Re-exported automatically via the worker
package facade's `from worker_legacy import *`.
`worker.compact_inference_dashboard_metrics` was removed from the docs
instead: `/api/inference/dashboard` builds its rows from the live
inference-sam3 `/health` payload — there are no persisted dashboard-metrics
rows to compact. Doc drift `worker.poll_http_feeds` → the real
`worker.tick_feed_poll` fixed in the same pass.

**Fused geometry diverged from persisted geometry** —
`_WeightedBoxFusionIndex.add` and `reconcile_edge_truncated` mutate
`pixel_bbox` only, so `geo_polygon`/`geo_bbox`/`pixel_obb` kept the pre-merge
box. New `_rederive_geo_from_pixel_bbox` re-derives all three from the
mutated bbox with the same pixel→WGS84 transform `_apply_chip_response`
uses; `_geo_stale_after_merge` (edge_reconciled, or `wbf_member_count > 1`)
gates it on both final-flush paths (non-streaming `all_kept` after edge
reconciliation; deferred-streaming WBF heads — WBF streaming already defers
its single store to the end precisely because heads mutate). Test:
[backend/tests/test_dedupe_geo_rederive.py](../../backend/tests/test_dedupe_geo_rederive.py).

**`tick_propose_entities` silently rolled back earlier inserts** — its
per-proposal except inside one transaction had no SAVEPOINT, so the first
SQL error poisoned the transaction and every earlier insert rolled back
while the task reported them written. Each proposal now runs under a
`SAVEPOINT proposal` released on success / rolled back on error.

**Detection policy frozen at import** — `DETECTION_POLICY =
active_detection_policy()` at module scope meant admin confidence-override
changes never reached the long-lived worker (and issued a MainProcess DB
query before fork). The global is gone; `slice_and_infer` fetches the policy
per chip batch and `store_detections` per call —
`active_detection_policy()` is TTL-cached in
[backend/detection_policy.py](../../backend/detection_policy.py) so this is
cheap.

**Seed button waited forever on the skipped path** — `seed_reference_db`'s
idempotency guard returned `"skipped"` before publishing any WS event. The
skip path now publishes a terminal `{"type": "done", "skipped": true, ...}`
on the `reference-seed` topic, mirroring the task's existing started/done
shape.

## Consequences

- Block-snapped chip grids on tiled COGs now genuinely cover every pixel;
  legacy grid consumers fall back to uniform `chip_size` windows.
- A long imagery pass picks up admin threshold changes mid-pass (per-batch
  policy fetch) instead of at the next worker restart.
- `observations`/`timeline_events` no longer grow unbounded on always-on
  deployments; retention is operator-tunable per env.
- FMV ingest tolerates isolated window/prompt failures (≤5% by default) and
  reports `tracking_windows_failed` instead of either lying "complete" or
  discarding finished work.

## Cross-references

- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [operations/celery-beat-schedule.md](../operations/celery-beat-schedule.md)
- [operations/celery-queues-and-tasks.md](../operations/celery-queues-and-tasks.md)
- [why-revert-inference-after-fmv.md](why-revert-inference-after-fmv.md)
- [why-retry-chips-across-inference-restart.md](why-retry-chips-across-inference-restart.md)

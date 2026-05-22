# `backend/tracker.py` — Multi-Pass Satellite Detection Tracker

**Path:** [backend/tracker.py](../../backend/tracker.py)
**Lines:** ~979
**Depends on:** `numpy`, `pyproj`, `scipy.optimize.linear_sum_assignment`, [backend/ontology.py](../../backend/ontology.py), [backend/threat_assessment.py](../../backend/threat_assessment.py)

## Purpose

Associates `detections` rows across **satellite passes** (`satellite_passes`) into **tracks** — stable per-object identifiers carried across acquisitions, via geodesic distance + Kalman prediction + DINOv3-SAT embedding similarity. Driven by `update_tracks_for_pass(pass_id)` after `process_satellite_imagery`; re-run by `POST /api/tracks/detections/reprocess`.

> **Not the FMV tracker.** Drone-video / FMV `fmv_detections` consolidated by a separate module — see [fmv-track-consolidation.md](fmv-track-consolidation.md).

## Why this design

- **Hungarian assignment with category-aware cost weights** — per-track weights (distance, embedding, category compatibility) from env `TRACKER_COST_WEIGHTS` (JSON). Defaults reflect empirical tuning in [scripts/video_tracking_stability.py](../../scripts/video_tracking_stability.py).
- **V_MAX state gates per category** — a "person" track cannot accelerate to 100 m/s in one frame; a "vehicle" can. Each category has a velocity cap rejecting implausible assignments before they enter the cost matrix.
- **Kalman update with per-state σ_a** — process noise depends on what the track is doing ("moving" vs "stopped"). Stopped tracks have lower σ_a so a brief mis-detection doesn't blow up the covariance.
- **14-day age cap, 3-miss timeout** — tracks age out by absolute calendar time or consecutive misses. Both bounds prevent runaway track counts on long-running deployments.

## Key symbols

- [`_load_tracker_weights`](../../backend/tracker.py#L75) — reads `TRACKER_COST_WEIGHTS` env JSON.
- [`_embedding_cost`](../../backend/tracker.py#L150) — cosine distance between DINOv3-SAT embeddings.
- [`_kalman_process_sigma_a`](../../backend/tracker.py#L201), [`_kalman_update_sigma`](../../backend/tracker.py#L238).
- [`_tracker_category`](../../backend/tracker.py#L273) → `_v_max`](../../backend/tracker.py#L281) → [`_track_state`](../../backend/tracker.py#L295).
- [`_haversine_metres`](../../backend/tracker.py#L334) — geodesic distance for cost.
- [`_predict_position`](../../backend/tracker.py#L340), [`_velocity_from_observations`](../../backend/tracker.py#L364).

## Failure modes

- Detection without embedding → embedding cost defaults to neutral; pure-position assignment still works.
- Hungarian assignment infeasible → tracks spawned/retired rather than forced.
- Time gap too large → all tracks miss → on resume tracker spawns new tracks (old ones aged out by miss-count).

## Cross-references

- [fmv-track-consolidation.md](fmv-track-consolidation.md) — the FMV-side tracker (separate module)
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md)
- [inference/dinov3-embeddings.md](../inference/dinov3-embeddings.md)
- [benchmarks/video-tracking-stability.md](../benchmarks/video-tracking-stability.md)
- [scripts/benchmark-scripts.md](../scripts/benchmark-scripts.md) — `bench_fmv.py` and `video_tracking_stability.py`

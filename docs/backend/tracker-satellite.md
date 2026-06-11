# `backend/tracker.py` — Multi-Pass Satellite Detection Tracker

**Path:** [backend/tracker.py](../../backend/tracker.py)
**Lines:** ~1024
**Depends on:** `numpy`, `pyproj`, `scipy.optimize.linear_sum_assignment`, [backend/ontology.py](../../backend/ontology.py), [backend/threat_assessment.py](../../backend/threat_assessment.py)

## Purpose

Associates `detections` rows across **satellite passes** (`satellite_passes`) into **tracks** — stable per-object identifiers carried across acquisitions, via geodesic distance + Kalman prediction + DINOv3-SAT embedding similarity. Driven by `update_tracks_for_pass(pass_id)` after `process_satellite_imagery`; re-run by `POST /api/tracks/detections/reprocess`.

> **Not the FMV tracker.** Drone-video / FMV `fmv_detections` consolidated by a separate module — see [fmv-track-consolidation.md](fmv-track-consolidation.md).

## Why this design

- **Hungarian assignment with category-aware cost weights** — per-track weights (distance, embedding, category compatibility) from env `TRACKER_COST_WEIGHTS` (JSON). Defaults reflect empirical tuning in [scripts/video_tracking_stability.py](../../scripts/video_tracking_stability.py).
- **V_MAX state gates per category** — a "person" track cannot accelerate to 100 m/s in one frame; a "vehicle" can. Each category has a velocity cap rejecting implausible assignments before they enter the cost matrix.
- **Kalman update with per-state σ_a** — process noise depends on what the track is doing ("moving" vs "stopped"). Stopped tracks have lower σ_a so a brief mis-detection doesn't blow up the covariance.
- **Physical displacement ceiling on the gate** — the Kalman σ-growth term (~0.5·σ_a·dt²) is unbounded in dt, so over a long inter-pass gap the gate radius can exceed Earth's circumference and admit a same-class detection on the far side of the planet. The gate is capped at `V_MAX_ceiling·dt·margin + position floor` so uncertainty can still widen it up to — but never beyond — what physics allows. See [decisions/tracker-gate-physical-ceiling.md](../decisions/tracker-gate-physical-ceiling.md).
- **Static buckets map to the stationary V_MAX cell** — `category_for_class` buckets with no V_MAX key of their own (`recreation` = sport courts/fields; `nature` = terrain/water/vegetation) are mapped to `infrastructure` (V_MAX 0) instead of falling through to the mobile `default` bucket. A tennis court is not a 16 m/s object.
- **14-day age cap, 3-miss timeout** — tracks age out by absolute calendar time or consecutive misses. Both bounds prevent runaway track counts on long-running deployments.

## Key symbols

- [`_load_tracker_weights`](../../backend/tracker.py#L75) — reads `TRACKER_COST_WEIGHTS` env JSON.
- [`_embedding_cost`](../../backend/tracker.py#L150) — cosine distance between DINOv3-SAT embeddings.
- [`_kalman_process_sigma_a`](../../backend/tracker.py#L201), [`_kalman_update_sigma`](../../backend/tracker.py#L238).
- [`_tracker_category`](../../backend/tracker.py#L318) (with [`_STATIC_TRACKER_CATEGORIES`](../../backend/tracker.py#L286) and [`_is_static_class`](../../backend/tracker.py#L309) — pins ontology-unknown static NAMES like `tennis_court`/`parking_lot` to `infrastructure`, see [tracker-gate-physical-ceiling.md](../decisions/tracker-gate-physical-ceiling.md) defect 3) → [`_v_max`](../../backend/tracker.py#L339) / [`_v_max_ceiling`](../../backend/tracker.py#L328) → [`_track_state`](../../backend/tracker.py#L353).
- [`_compute_cost`](../../backend/tracker.py#L476) — gate (`np.inf` outside) + weighted cost; the physical ceiling is at [tracker.py#L515-L519](../../backend/tracker.py#L515-L519), `GATE_MAX_SPEED_MARGIN` at [tracker.py#L206](../../backend/tracker.py#L206).
- [`_haversine_metres`](../../backend/tracker.py#L392) — geodesic distance for cost.
- [`_predict_position`](../../backend/tracker.py#L398), [`_velocity_from_observations`](../../backend/tracker.py#L422).

## Failure modes

- Detection without embedding → embedding cost defaults to neutral; pure-position assignment still works.
- Hungarian assignment infeasible → tracks spawned/retired rather than forced.
- Time gap too large → all tracks miss → on resume tracker spawns new tracks (old ones aged out by miss-count).
- Same-class detections far apart over a long Δt → the physical ceiling forces `np.inf` (no association), so distinct objects are not stitched into one continent-spanning track. Without the ceiling the Kalman gate would admit them and draw a streak across the map.

## Cross-references

- [fmv-track-consolidation.md](fmv-track-consolidation.md) — the FMV-side tracker (separate module)
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md)
- [inference/dinov3-embeddings.md](../inference/dinov3-embeddings.md)
- [benchmarks/video-tracking-stability.md](../benchmarks/video-tracking-stability.md)
- [scripts/benchmark-scripts.md](../scripts/benchmark-scripts.md) — `bench_fmv.py` and `video_tracking_stability.py`

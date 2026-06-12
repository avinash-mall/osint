# Backend-core correctness audit — fixes (2026-06-11)

**Date:** 2026-06-11
**Status:** adopted

## Context

A focused audit pass over the backend core analytics/policy modules
(`video_metadata.py`, `terrain.py`, `satellite_anomaly.py`,
`threat_assessment.py`, `ontology.py`, `sar_cfar.py`, `detection_policy.py`)
hunting numerical/geodesy errors, dead code paths, and cache/transaction
hazards. Every finding was verified against the actual code before fixing; all
fixes are surgical and behaviour-targeted. Regression tests were added for
each (offline — no DB, GPU, or media binaries required).

## Fixed

### FMV telemetry ([video_metadata.py](../../backend/video_metadata.py))

- **GPMD samples collapsed into the first second** — `_extract_gpmd` spread
  GPS5 samples across `[0,1)` s regardless of clip length, so
  `_samples_to_rows`'s per-frame dedup discarded almost all telemetry. Now
  threads `duration_s` and spreads samples across the real clip duration,
  matching the `_extract_klv` fallback added on 2026-06-08.
- **Footprint rotated in degree space, counter-clockwise** — `_footprint_wkt`
  converted the metre half-spans to degrees *before* rotating (the
  `cos(lat)` lon scaling made the rotation anisotropic — 2× skew at 60°N) and
  rotated CCW while sensor azimuth is clockwise-from-north. Now rotates the
  metre offsets first with the CW convention
  (`rx = dx·cos + dy·sin; ry = −dx·sin + dy·cos`), then converts to degrees.

### Terrain LOS/viewshed ([terrain.py](../../backend/terrain.py))

- **Viewshed curvature sign** — distant terrain had `_curvature_drop_m`
  *added* to its effective height; curvature makes far ground appear *lower*.
  Now subtracted.
- **LOS measured against the wrong reference** — the straight observer→target
  chord was compared to ground + an observer-anchored `d²` drop, which is the
  horizon model, not the chord model (it mis-scores the far half of the path).
  Now adds the chord bulge `(1−k)·d·(D−d)/2R` (zero at both endpoints) to the
  sampled ground via `_chord_bulge_m`.
- **Antimeridian** — LOS lon interpolation walked the long way around for
  endpoints straddling ±180°, and viewshed rays produced out-of-range
  longitudes. Both now wrap through `_wrap_lon`.

### Satellite anomalies ([satellite_anomaly.py](../../backend/satellite_anomaly.py))

- **RAAN residual not re-wrapped** — over a long TLE gap (≳40 days) the
  expected J2 drift exceeds a full revolution while the observed delta is
  wrapped, so `|actual − expected|` could read ~360° and raise a false
  maneuver alert. The residual is now wrapped into [−180°, 180°). Inclination
  and eccentricity deltas need no wrap (TLE ranges are unambiguous).

### Category mapping ([threat_assessment.py](../../backend/threat_assessment.py))

- **`category_for_class` buckets were effectively dead** — the parent-string
  sets (`"aircraft"`, `"vessel"`, …) were keyed to values `ontology.normalize`
  never produces: the runtime `parent_class` is the object's own canonical
  label ("destroyer", "boeing_737") or a branch label ("naval_/_maritime"),
  so only ~5 of 126 seeded labels hit any bucket and the tracker's
  per-category V_MAX gates / Kalman noise / static pins ran on `"default"`
  for nearly everything. Now maps `NormalizedLabel.branch_id` through
  `_BRANCH_CATEGORIES` (lowercased seed branch ids →
  air/maritime/ground/infrastructure/nature) first, keeping the parent-string
  sets as a fallback for unmapped branches (`Battle_Damage`, unknowns).
  [tracker.py](../../backend/tracker.py) needed no change — `_tracker_category`
  already delegates to `category_for_class`.

### Ontology ([ontology.py](../../backend/ontology.py))

- **`bump_version` could silently roll itself back** — the best-effort history
  INSERT ran inside the same `get_cursor(commit=True)` transaction; a failed
  statement aborts the PG transaction, so the version bump itself rolled back
  while the function reported success. The history block now runs under a
  `SAVEPOINT` with `ROLLBACK TO SAVEPOINT` in the except.
- **`normalize()` cache races** — (a) a memo write computed against the old
  tree could land after a rebuild and poison the new generation; guarded by a
  `_TREE_GENERATION` counter checked under `_CACHE_LOCK`. (b) the tree swap
  was `clear()` + `update()` on a shared dict, giving concurrent readers a
  briefly-empty tree (KeyError on a function documented to never raise); now
  an atomic reference replacement of the fully-built dict.

### SAR CFAR ([sar_cfar.py](../../backend/sar_cfar.py))

- **VH cross-pol gate used guard-inclusive statistics** — the VV path got
  proportional guard subtraction on 2026-06-09 but the VH consistency gate
  kept `_box_kernel_mean(vh, bg_window)`, letting the target's own energy
  depress `z_vh` exactly where detections live (the gate ANDs into the mask,
  so it *removed* true positives). VH now uses the same guard-excluded μ/σ
  derivation as VV.

### Detection policy ([detection_policy.py](../../backend/detection_policy.py))

- **`active_detection_policy` froze admin overrides in workers** — it was
  `@lru_cache(maxsize=1)`, and `invalidate_policy_cache()` only reaches the
  API process; long-lived Celery workers never saw DB override changes.
  Replaced with a 30 s TTL cache (`_POLICY_TTL_S`) behind the identical
  signature; `invalidate_policy_cache()` still forces an immediate rebuild.

## Validation

`backend/` test suite: 303 passed, 104 skipped. New offline regression tests:
[test_video_metadata_gpmd.py](../../backend/tests/test_video_metadata_gpmd.py),
[test_terrain_curvature.py](../../backend/tests/test_terrain_curvature.py),
[test_threat_category.py](../../backend/tests/test_threat_category.py),
[test_sar_cfar.py](../../backend/tests/test_sar_cfar.py), plus new cases in
[test_satellite_anomaly.py](../../backend/tests/test_satellite_anomaly.py)
(wrapped RAAN residual after a 60-day gap) and
[test_detection_policy_thresholds.py](../../backend/tests/test_detection_policy_thresholds.py)
(TTL expiry / within-TTL reuse).

## Cross-references

- [backend/video-metadata-klv.md](../backend/video-metadata-klv.md)
- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md)
- [backend/satellite-anomaly.md](../backend/satellite-anomaly.md)
- [backend/threat-assessment.md](../backend/threat-assessment.md)
- [backend/tracker-satellite.md](../backend/tracker-satellite.md)
- [backend/ontology-system.md](../backend/ontology-system.md)
- [backend/sar-cfar-detector.md](../backend/sar-cfar-detector.md)
- [backend/detection-policy.md](../backend/detection-policy.md)
- [decisions/audit-fixes-codebase-correctness-2026-06-08.md](audit-fixes-codebase-correctness-2026-06-08.md) — the prior sweep this pass extends

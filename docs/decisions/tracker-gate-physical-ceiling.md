# Decision — physical-displacement ceiling on the tracker association gate

**Path:** [backend/tracker.py](../../backend/tracker.py)
**Lines:** ~1024
**Depends on:** `_v_max_ceiling`, `GATE_MAX_SPEED_MARGIN`, `KALMAN_GATE_SIGMAS`, [threat_assessment.category_for_class](../../backend/threat_assessment.py#L165)

## Purpose

Stop the multi-pass detection tracker from stitching two distinct same-class
objects in different cities into one track. The visible symptom was three
`tennis_court` tracks each connecting a court in **Vienna** to one in **Abu
Dhabi** (~4,200 km), drawn as diagonal streak polylines across the Map by the
`detectionTracks` layer.

## Why this design

The gate in [`_compute_cost`](../../backend/tracker.py#L448) was
`r_gate = max(V_MAX·dt·1.25, KALMAN_GATE_SIGMAS·σ_pred)` with
`σ_pred ≈ 0.5·σ_a·dt²`. That Kalman σ-growth term is **unbounded in dt**: for
the mobile `default` bucket (σ_a = 2.0 m/s²) at Δt = 6,000 s (1h40m between
passes) it yields `r_gate ≈ 108,000 km` — larger than Earth's circumference.
Any two same-class detections anywhere on the planet then fall inside the gate,
and an exact class match carries zero class penalty, so they associate.

Two compounding defects, both fixed:

1. **Static class fell into the mobile bucket.** `tennis_court` → parent
   `recreation`, which is not a `V_MAX` key, so `_tracker_category` returned
   the mobile `default` (16 m/s, σ_a 2.0). Fix: `_STATIC_TRACKER_CATEGORIES =
   {"recreation", "nature"}` map to `infrastructure` (V_MAX 0, σ_a 0) — a fixed
   structure is never treated as moving.

2. **Gate had no physical ceiling.** Even for genuinely mobile classes the
   dt² term lets the gate exceed any real travel distance. Fix: cap
   `r_gate` at `V_MAX_ceiling(category)·dt·GATE_MAX_SPEED_MARGIN +
   KALMAN_GATE_SIGMAS·σ_x_base`. The motion term bounds travel at the
   category's top-speed state; the σ_x_base term preserves the bounded
   position-uncertainty floor so legitimate same-spot re-detection (GSD
   jitter) still associates. `GATE_MAX_SPEED_MARGIN` defaults to 2.0
   (env `TRACKER_GATE_SPEED_MARGIN`) for heading/acceleration slack.

## Alternatives rejected

- **Fix only the category mapping.** Closes tennis courts but leaves the dt²
  blow-up for every mobile class — two ships or trucks far apart still
  associate after a long gap. The ceiling is the structural fix.
- **Map the unknown `object` catch-all to stationary too.** Rejected: an
  open-vocab unknown may genuinely move, so it stays in the mobile `default`
  bucket, now safely bounded by the ceiling (open-vocabulary policy, hard
  rule 5).
- **Drop the Kalman widening entirely.** It legitimately helps newborn /
  manoeuvring tracks; the ceiling keeps that benefit up to the physical limit.

## Verification

[backend/tests/test_tracker_gate.py](../../backend/tests/test_tracker_gate.py)
— rejects the cross-continent jump for both `infrastructure` and `default`
categories, and confirms plausible same-spot re-detection and a ~90 km vehicle
move still associate. Existing rows are rebuilt by
`POST /api/tracks/detections/reprocess`.

## Cross-references

- [backend/tracker-satellite.md](../backend/tracker-satellite.md)
- [backend/threat-assessment.md](../backend/threat-assessment.md) — `category_for_class` buckets
- [decisions/why-open-vocabulary.md](why-open-vocabulary.md)

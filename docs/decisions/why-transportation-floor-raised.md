# Why the `transportation` (and `other`) per-class confidence floor was raised

**Date:** 2026-05-28
**Affects:** [backend/detection_policy.py](../../backend/detection_policy.py), [backend/routers/inference.py](../../backend/routers/inference.py), [frontend/src/components/admin/ConfOverrideView.tsx](../../frontend/src/components/admin/ConfOverrideView.tsx)

## Problem

The 2026-05-22 ontology-mode triage-set benchmark
([docs/benchmarks/detection-quality-ontology-mode-2026-05-22.md](../benchmarks/detection-quality-ontology-mode-2026-05-22.md))
showed two parent buckets dragging the analyst-facing precision number down:

| Parent class    | Recall | Precision |
| --------------- | -----: | --------: |
| `transportation` | 100.0% |     3.5% |
| `other`          |  22.0% |    27.0% |

For `transportation` the picture is the worst: the bucket catches every
generic DOTA-OBB `small vehicle` / `large vehicle` call AND every SAM3
`"vehicle"` / `"truck"` text-prompt hit from across many ontology branches.
At the default `GLOBAL_CONFIDENCE_FLOOR=0.40` virtually nothing is filtered
out, so the analyst sees a flood of false-positive `transportation` pins.

`other` is the runner-up. It is the open-vocab catch-all for prompts that
don't cluster into a named bucket — by construction it has the loosest
shape constraints and therefore needs more head-room than the global floor
gives it.

## Research

- The global floor (0.40) is already a precision-first default
  ([decisions/why-precision-first-inference-defaults.md](why-precision-first-inference-defaults.md)).
  Raising it further would crush recall on well-behaved buckets like
  `aircraft` / `vessel` / `building`. Per-class shaping is the right knob.
- `backend/detection_policy.py` already has the plumbing: a
  `PER_CLASS_CONFIDENCE_OVERRIDES` env JSON, a DB-stored override row, and
  a `threshold_for_parent(parent_class, policy)` helper that the imagery
  worker calls. Until now the env+DB maps started **empty** — no shipped
  defaults — so the only way to tame `transportation` was a manual env edit
  or an admin-matrix PUT after the fact.
- Bucket-splitting (separate `road_vehicle` / `rail_vehicle` /
  `parked_aircraft`) would surgically address the underlying recall/precision
  tradeoff, but it requires ontology schema additions, an ontology re-seed,
  and downstream UI category-facet work. That belongs to Task 2.7's
  FAIR1M-specialist effort, not a one-day threshold tune.

## Decision

Ship a `DEFAULT_PER_CLASS_THRESHOLDS` constant in
`backend/detection_policy.py`:

```python
DEFAULT_PER_CLASS_THRESHOLDS: dict[str, float] = {
    "transportation": 0.55,
    "other":          0.50,
}
```

Merge it into `active_detection_policy()` at the **bottom** of the priority
stack, so the precedence is:

```text
code defaults  <  PER_CLASS_CONFIDENCE_OVERRIDES env  <  inference_config DB row
```

Operators retain full runtime control via the admin matrix
([frontend/src/components/admin/ConfOverrideView.tsx](../../frontend/src/components/admin/ConfOverrideView.tsx))
— a DB-stored override of `0.30` still wins over the shipped `0.55`. The
existing `GET /api/inference/confidence-overrides` handler already returns
the merged `class_thresholds` dict as `env_per_class_confidence_overrides`,
so the admin UI surfaces the new defaults automatically with an ENV badge
the moment the worker reloads.

`GLOBAL_CONFIDENCE_FLOOR` stays at 0.40 — only the two named buckets move.

## What was deliberately NOT done

- **No bucket splitting.** Adding `road_vehicle` / `rail_vehicle` /
  `parked_aircraft` is the right long-term fix and is captured by Task 2.7
  (FAIR1M fine-grained specialist). It would change ontology shape, prompt
  seeding, and the UI category-facet system — out of scope for a threshold
  tune.
- **No global floor change.** Raising `GLOBAL_CONFIDENCE_FLOOR` would punish
  buckets like `aircraft` (vessel, building, infrastructure) that already
  have acceptable precision at 0.40.
- **No new env var.** The defaults live in code; operators tune via the
  existing env JSON or the existing admin matrix.
- **No schema change.** The `inference_config` row format is untouched.
- **No deletion of any class.** Open-vocab policy preserved
  ([decisions/why-open-vocabulary.md](why-open-vocabulary.md)). The two
  buckets keep firing, they just need higher per-detection confidence to
  surface.

## Measured impact

The 2026-05-22 benchmark predicts the precision lift directly: at floor=0.40
`transportation` produced ~28× more false positives than true positives
(3.5% precision). Raising the floor to 0.55 trims the long tail of low-
confidence detections — concretely, every `transportation` pin below 0.55
disappears from the map, and the recall hit is bounded by how many true
positives sat between 0.40 and 0.55. The next triage-set run after this
change ships will be added to
[docs/benchmarks/](../benchmarks/) with the new before/after table.

For `other`, the move from 0.40 → 0.50 is a smaller correction — recall is
already low (22%), so trimming the lowest-confidence calls mostly removes
FPs without further depressing recall.

This is reversible at runtime. If the lift overshoots and recall on
operationally-important `transportation` targets drops below the bench
floor, an admin lowers the override to 0.45 in the matrix without a code
deploy.

## Cross-references

- [backend/detection-policy.md](../backend/detection-policy.md)
- [backend-routers/inference-router.md](../backend-routers/inference-router.md) — `GET/PUT /api/inference/confidence-overrides`
- [decisions/why-open-vocabulary.md](why-open-vocabulary.md)
- [decisions/why-precision-first-inference-defaults.md](why-precision-first-inference-defaults.md)
- [decisions/why-generic-labels-when-unverified.md](why-generic-labels-when-unverified.md) — sibling Task 1.2 fix on the display side
- [benchmarks/detection-quality-ontology-mode-2026-05-22.md](../benchmarks/detection-quality-ontology-mode-2026-05-22.md) — the measured failure that motivated this tune

# Why a FAIR1M-2.0 OBB specialist

**Decision date:** 2026-05-28
**Status:** Plumbing landed; checkpoint operator-baked
**Related:** [why-evidence-ranked-detections.md](why-evidence-ranked-detections.md), [why-grounding-dino-auto-gated.md](why-grounding-dino-auto-gated.md), [removed-defence-yolo.md](removed-defence-yolo.md)

## Problem

The DOTA-v1 head we ship (`yolo26m-obb.pt`) knows 18 generic classes:
plane, ship, helicopter, large/small vehicle, bridge, harbor, .... For
defence-analyst work that is too coarse. Recent benchmark runs measured
per-class AP = 0.0 for every fine-grained military bucket the ontology
exposes — `armored_vehicle`, `battle_damage`, `fortification`,
`industrial`, `military_installation`, `missile_strategic`,
`tactical_vehicle`, `urban`. The analyst feedback was unambiguous:
"plane" is not actionable when the question is "Boeing 737 or Su-25".

## Research

FAIR1M-2.0 (GaoFen Challenge) is the canonical open-data benchmark for
fine-grained oriented-bbox detection in aerial imagery. It has **37
sub-classes** covering airframe families (Boeing 737/747/777/787, A220,
A321, A330, A350, ARJ21, Cessna), ship sub-types (Warship, Tugboat,
Fishing Boat, Engineering Ship, Liquid/Dry/Passenger Cargo, Motorboat),
and vehicle sub-types (Dump Truck, Cargo Truck, Trailer, Tractor, Truck
Tractor, Excavator, Van) — exactly the buckets DOTA-v1 collapses.

No mature public Ultralytics checkpoint is widely available for FAIR1M-2.0.
The training pipeline is straightforward (Ultralytics `yolo obb train`
takes a YOLO-format dataset directly) but takes ~12 h on a single 24 GB
GPU plus the upfront dataset download and conversion. Training is
infeasible inside an implementation session.

## Decision

Ship the **plumbing** in this commit — runner module, gate, dispatch
wiring, manifest entry, tests, docs, runbook. Defer the actual
**weight bake** to operator action via the runbook
[../operations/fair1m-bake.md](../operations/fair1m-bake.md). The runner
gracefully no-ops when no weights file is present (returns `model=None`,
same pattern as DOTA-OBB), so the absence is invisible at runtime until
an operator rsyncs the checkpoint into `assets/static/inference-weights/fair1m/`.

The specialist is **default-on for the imagery profile** (`SAM3_LOAD_FAIR1M_OBB=1`)
because the load is free when weights are absent. It is **auto-gated**
the same way Grounding-DINO is: it only fires when the request's prompts
mention vocabulary that DOTA-v1 does not already cover. Operator override
via `metadata.force_fair1m_obb=true`.

This matches the precedent set by T1.4 (calibration shipping): plumbing
ships in the commit; the actual measurement is operator-scheduled.

## NOT done in this commit

- **No checkpoint trained.** Weight file is `.gitignore`d. The runner
  reports `loaded=false` in `/health` until weights land.
- **No assets-image bake change.** `assets/Dockerfile` and
  `assets/scripts/entrypoint.sh` are untouched. When an operator has
  weights, the existing `inference-weights/*` rsync mechanism will pick
  them up.
- **No DOTA-OBB replacement.** DOTA-OBB stays loaded; the two specialists
  run side-by-side and the FAIR1M gate excludes DOTA-vocab prompts so
  they don't double-detect.
- **No dataset converter.** `scripts/prepare_fair1m_dataset.py` is left
  as a runbook TODO with the conversion outline.
- **FAIR1M detections are never RemoteCLIP-verified.** By symmetry with
  DOTA-OBB's exclusion (`REMOTECLIP_VERIFIER_LAYERS=sam3,grounding_dino`),
  FAIR1M's closed-vocab calls are not second-guessed by the verifier. The
  consequence: FAIR1M detections will surface in the UI with the
  `inferred` label-quality (default) chip, never the `[VERIFIED]` chip
  that T1.2 reserves for `semantic_margin >= LABEL_VERIFIER_MARGIN_FLOOR`.
  This is intentional — FAIR1M's training distribution is the source of
  truth for its 37 classes, and asking an open-vocab CLIP variant to
  re-rank a "Boeing 737" detection would be the same precision-loss the
  GDINO auto-gate addresses ([decisions/why-grounding-dino-auto-gated.md]).
  Future work could add a `TRUSTED_SOURCE_LAYERS` set to
  `label_quality_for` so closed-vocab specialists default to `verified`,
  but that decision belongs with a measured ablation, not this scaffold.

## Measured impact (expected, not yet confirmed)

Per the approved scope plan, aircraft AP should rise from the current
0.36 (single "plane" bucket) to **>0.6** once the FAIR1M checkpoint is
baked — driven mostly by the airframe-family classes that the current
0.0-AP defence buckets cover. Naval and vehicle sub-classes are expected
to follow the same pattern.

The plumbing change in this commit is **load-flag default-on, runtime
no-op** until the bake — so it cannot regress the current detection
pipeline. The integration test asserts this.

## Cross-references

- [../inference/fair1m-obb-specialist.md](../inference/fair1m-obb-specialist.md) — runner module doc
- [../operations/fair1m-bake.md](../operations/fair1m-bake.md) — bake runbook
- [../inference/dota-obb-specialist.md](../inference/dota-obb-specialist.md) — sibling pattern
- [../inference/grounding-dino-gate.md](../inference/grounding-dino-gate.md) — gate pattern this mirrors
- [../conventions/adding-a-new-detection-model.md](../conventions/adding-a-new-detection-model.md)
- [../operations/calibration-shipping.md](../operations/calibration-shipping.md) — precedent for "ship plumbing, defer bake"

# Why generic labels are kept generic until a verifier confirms

**Date:** 2026-05-28
**Affects:** [backend/detection_policy.py](../../backend/detection_policy.py), [backend/worker_legacy.py](../../backend/worker_legacy.py), [frontend/src/components/map/_helpers.ts](../../frontend/src/components/map/_helpers.ts), [frontend/src/components/map/SelectionPanel.tsx](../../frontend/src/components/map/SelectionPanel.tsx), [frontend/src/components/map/MapStage.tsx](../../frontend/src/components/map/MapStage.tsx)

## Problem

Analysts saw "Fighter Aircraft" on detections where the only thing the model
actually emitted was DOTA-OBB's generic `plane`. The pipeline path was:

1. DOTA-OBB returns `(bbox, score, "plane")` from its 18-class head.
2. `worker_legacy.store_detections` calls `ontology.normalize("plane")`.
3. The ontology lookup tie-breaks "plane" to whichever `ontology_objects.label`
   first matches the canonical prompt — often a specific defence label like
   `Fighter Aircraft`.
4. That **fabricated specific label** is written into the metadata as
   `canonical_label` and rendered as the detection's title in the UI.

This is the same class of bug that
[decisions/why-deconflicted-detection-prompts.md](why-deconflicted-detection-prompts.md)
fixed at the **prompt** level (one object per unique prompt). That fix removed
the "facility ×8 → arbitrary defence label" tie-break for prompts an operator
configured. But the DOTA-OBB head is a **closed 18-class detector** that ships
with the model — operators don't pick its labels. So the same fabrication kept
happening for every `plane`, `ship`, and `large vehicle` row.

The user's complaint that detections "provide wrong detections for most of the
categories" traces directly to this: the analyst sees a high-confidence
"Fighter Aircraft" pin and cannot tell that the underlying signal only said
"plane".

## Research

- **SAM 3** is calibrated for the prompts the operator types; its outputs are
  trust-as-said unless a verifier disagrees.
- **DOTA-OBB**'s 18 categories are deliberately generic (the v1 spec). The
  head is not a fine-grained classifier; promoting `plane → Fighter Aircraft`
  has no model evidence behind it.
- **RemoteCLIP** (already integrated, optional) can score `crop ↔ candidate
  label` pairs and produce a `semantic_margin`. When that margin clears a
  configured floor the specific ontology label has measured support.

## Decision

The display layer is now **precision-first**:

* `backend/detection_policy.py` exports
  `DOTA_OBB_GENERIC_CLASSES`, `label_quality_for`, and `display_label_for`.
  Each detection gets a `label_quality` of `verified` | `inferred` | `generic`:
  * **verified** — `semantic_margin >= LABEL_VERIFIER_MARGIN_FLOOR`
    (default 0.10, env-tunable). Display uses `ont.canonical_label`.
  * **generic**  — `source_layer == dota_obb` AND `normalize_label(original_class)`
    is in `DOTA_OBB_GENERIC_CLASSES` AND not verified. Display becomes
    `"{Parent} (generic)"` (e.g. `"Aircraft (generic)"`); the fabricated
    `canonical_label` is NOT used.
  * **inferred** — everything else. Display uses `canonical_label` (the operator
    typed the SAM3 prompt, so it's honest), but the UI does not award it a chip.
* `worker_legacy.store_detections` persists `display_label` and `label_quality`
  into the detection metadata alongside the existing `original_class`,
  `parent_class`, and `canonical_label` fields. The SQL `class` column and
  every existing metadata key remain untouched (backwards-compatible).
* `frontend/src/components/map/_helpers.ts` gains a `displayLabel(props)`
  ladder that reads `display_label` first; `labelQuality(props)` exposes the
  triage state.
* `SelectionPanel.tsx` renders the resolved label in the header and a small
  `[GENERIC]` (warn-coloured) or `[VERIFIED]` (ok-coloured) chip with a
  tooltip explaining the state.
* `MapStage.tsx` popup gains a `LABEL_QUALITY` line beside `ORIG` / `PARENT`.

## What was deliberately NOT done

* The deconflicted ontology stays unchanged — this is a **display** policy,
  not an ontology change. `ontology.normalize()` still returns the same
  `NormalizedLabel`; we just stop using its `canonical_label` for the generic
  case.
* No new model, no new prompt, no new label is added. The 18 DOTA-OBB
  generics keep firing exactly as before.
* The `class` SQL column is not overwritten. Queries, downstream Neo4j
  projectors, and existing dashboards continue to see the raw detector class.
* No labels are deleted — generic detections are still first-class citizens
  of the open-vocab policy (see [why-open-vocabulary.md](why-open-vocabulary.md)).
  They just stop borrowing a fabricated specific name.

## Measured impact

Before: every DOTA-OBB `plane`/`ship`/`large vehicle` detection was rendered
with whatever specific defence label the ontology tie-break landed on.
A typical run with ~120 `plane` detections produced ~120 "Fighter Aircraft"
pins, none of which had model evidence for the specific class.

After: those same detections render as `"Aircraft (generic)"` /
`"Vehicle (generic)"` / `"Naval (generic)"` with a `[GENERIC]` chip. The
analyst can immediately see that the row is a generic shape match, not a
verified identification. When T1.6 enables RemoteCLIP at scale, the chip
flips to `[VERIFIED]` for the rows that survive the semantic_margin floor —
and only those rows get the specific defence label.

## Cross-references

- [backend/detection-policy.md](../backend/detection-policy.md)
- [decisions/why-deconflicted-detection-prompts.md](why-deconflicted-detection-prompts.md) — the prompt-level half of the fix
- [decisions/why-open-vocabulary.md](why-open-vocabulary.md)
- [decisions/removed-fair1m-and-remoteclip.md](removed-fair1m-and-remoteclip.md) — the RemoteCLIP verifier that lifted `generic → verified` was removed
- [inference/dota-obb-specialist.md](../inference/dota-obb-specialist.md) — the 18 generic classes
- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [frontend/map-selection-panel.md](../frontend/map-selection-panel.md)
- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)

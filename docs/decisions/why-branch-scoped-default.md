# Why branch-scoped prompts are the ingest default

**Date:** 2026-05-28
**Affects:** [frontend/src/components/IngestConnect.tsx](../../frontend/src/components/IngestConnect.tsx), [frontend/src/utils/promptsForBranch.ts](../../frontend/src/utils/promptsForBranch.ts)

## Problem

The ingest UX used to let analysts cherry-pick individual ontology objects, or
leave the picker untouched. With an empty selection, the imagery worker fell
back to the inference service's `precision` defaults; with anything picked, the
explicit list won. There was no first-class "scope to my mission branch" lever.
In practice operators left the picker alone and the platform ran against the
full ~131-prompt open-vocab vocabulary.

That fan-out is the largest single false-positive driver in the stack.
[benchmarks/detection-quality-ontology-mode-2026-05-22.md](../benchmarks/detection-quality-ontology-mode-2026-05-22.md)
measured the cost on DOTA-v1.0 val:

| Configuration                | sam3+dota_obb mAP | SAM3 alone mAP |
|------------------------------|-------------------|----------------|
| Oracle prompts (per-chip GT) | 0.60              | 0.40           |
| Ontology mode, 131 prompts   | 0.51              | **0.16**       |

SAM 3 alone collapses 0.40 → 0.16 purely from vocabulary width.

## Research

The LAE-80C aerial open-vocab study (arXiv:2601.22164) quantifies the
mechanism. Going from 80 candidate classes down to 3.2 yielded a **15× F1
gain**; synonym expansion and "aerial view of" prefixes both *hurt*. The only
intervention that worked was **vocabulary scoping**. SegEarth-OV3
(arXiv:2512.08730) corroborates: the cure for overhead false positives is
suppressing irrelevant categories, not richer prompts.

End-to-end plumbing for branch-scoped prompts had already been wired up in
[decisions/why-deconflicted-detection-prompts.md](why-deconflicted-detection-prompts.md):
`POST /api/ingest/upload` accepts an `ontology_branch` form field, the worker
threads it into inference metadata, and `resolve_prompts()` honours
`metadata.ontology_branch` in ontology mode. What was missing was the UX
default — operators had to know to send it.

## Decision

The imagery upload UX now defaults to **branch-scoped prompts**. A three-mode
vocabulary-scope selector sits above the Detection Objects tree:

1. **Mission branch** (default) — single-select dropdown of top-level
   ontology branches, defaulting to the first branch as soon as the tree
   loads. The frontend resolves the branch (and its descendants) into a
   deduplicated prompt list client-side via
   [`promptsForBranch`](../../frontend/src/utils/promptsForBranch.ts) and sends
   that as `text_prompts`. `ontology_branch` is also sent for backend
   provenance.
2. **Cherry-pick objects** — legacy hand-pick UX, unchanged in behaviour;
   the operator's selection becomes `text_prompts` verbatim.
3. **All branches** — explicit opt-out. Flattens every branch into one
   prompt list. Guarded with a yellow warning ("Full ontology fan-out
   (~131 prompts). Higher false-positive rate per LAE-80C; use only for
   exploratory passes.").

A status chip in the existing models row shows the active mode and prompt
count, so operators always see how scoped their run is before clicking
Upload — e.g. `[Branch: Air] 18 prompts` or `[All branches] 131 prompts ⚠`.

Within `branch` mode the tree stays visible, filtered to the selected
branch, so an operator can further restrict to a subset; if they do, the
restricted intersection becomes `text_prompts`, otherwise the full branch
slice is used.

The choice to derive prompts client-side (rather than just sending
`ontology_branch` and a new `prompt_source=ontology` form field) keeps this
a pure-frontend change — no backend router edit, no inference patch — and
uses already-cached ontology data. The end-to-end behaviour is identical to
the server-side fan-out path.

## What was deliberately NOT done

- **No `prompt_source` form field** added to the ingest router. The
  client-side derivation route was chosen because the data is already in
  memory and zero backend code needs to move.
- **No UI hiding of the opt-out**. The "All branches" mode is a single
  click away; analysts working on exploratory passes need it. The yellow
  warning is education, not friction.
- **No synonym expansion or "aerial view of" prefixes** — LAE-80C measured
  both as harmful.
- **No removal of the cherry-pick mode**. It's still the right answer when
  an operator knows exactly which 2-3 prompts they want.
- **No backend changes**. Pre-existing `ontology_branch` handling in
  `routers/ingest.py` and the worker is unchanged; client-derived
  `text_prompts` win at `resolve_prompts()` per
  [why-precision-first-inference-defaults.md](why-precision-first-inference-defaults.md).

## Measured impact

The decision is grounded in two independent measurements:

- **Our own DOTA-v1.0 val benchmark** (above): branch-scoped 87 prompts
  yields 0.55 mAP at ~45% lower latency than the 131-prompt unscoped run
  (0.51 mAP). See
  [benchmarks/detection-quality-scoped-2026-05-22.md](../benchmarks/detection-quality-scoped-2026-05-22.md).
- **LAE-80C aerial open-vocab study**: 15× F1 gain going from 80 →
  3.2 classes.

The default change converts the existing measured win from a power-user
feature into the path of least resistance.

## Cross-references

- [decisions/why-deconflicted-detection-prompts.md](why-deconflicted-detection-prompts.md) — the plumbing this default rides on.
- [decisions/why-open-vocabulary.md](why-open-vocabulary.md) — labels remain first-class; this is about scoping the candidate set, not deleting classes.
- [decisions/why-precision-first-inference-defaults.md](why-precision-first-inference-defaults.md) — why explicit `text_prompts` continue to win at the inference layer.
- [benchmarks/detection-quality-ontology-mode-2026-05-22.md](../benchmarks/detection-quality-ontology-mode-2026-05-22.md)
- [benchmarks/detection-quality-scoped-2026-05-22.md](../benchmarks/detection-quality-scoped-2026-05-22.md)
- [frontend/workspace-ingest.md](../frontend/workspace-ingest.md)

# Why detection prompts were deconflicted (one object per unique prompt)

**Date:** 2026-05-22
**Affects:** [backend/scripts/seeds/defenceOntology.seed.json](../../backend/scripts/seeds/defenceOntology.seed.json), [backend/ontology.py](../../backend/ontology.py), [backend/scripts/seed_ontology.py](../../backend/scripts/seed_ontology.py), [backend/detection_policy.py](../../backend/detection_policy.py)

## Problem

Analysts saw the platform "force wrong positives" — civilian/benign objects surfaced
with alarming military labels (a shopping mall tagged "Launch Pad").

Two root causes in the ontology seed:

1. **Overloaded prompts.** Many ontology objects shared one vague prompt string —
   `"facility"` ×8, `"tower"` ×5, `"warship"` ×7, `"fixed-wing aircraft"` ×7,
   `"factory or powerplant"` ×8. The model received only the vague string; on a
   detection, `ontology.normalize()` tie-broke to an arbitrary one of the sharing
   objects and stamped its fabricated defence label.
2. **Whole-vocabulary fan-out.** Grounding-DINO joined all ~140 prompts into one
   `". "`-separated query, causing cross-concept token "bleed".

## Research

Independent sources converge (web research, May 2026):

- SAM 3 wants **simple, specific noun phrases** and has a *calibrated* presence head —
  Meta designed it so thresholds should **not** be tuned per dataset.
- Grounding-DINO: long concatenated captions cause concept bleed; ambiguous text
  causes false positives.
- Aerial-imagery study (arXiv:2601.22164): open-vocab detectors run a **69% false
  positive rate** on overhead imagery; cutting the vocabulary 80→3.2 classes gave a
  **15× F1 gain**. Synonym expansion and "aerial view of" prefixes were tested and
  **both hurt** — the only thing that works is **vocabulary scoping**.
- SegEarth-OV3 (arXiv:2512.08730): the cure for overhead false positives is
  suppressing irrelevant categories, not richer prompts.

## Decision

- **One ontology object per unique prompt; the UI label IS the prompt** (title-cased).
  No separate hand-maintained defence-terminology layer. Objects that sent an
  identical prompt were collapsed — the models never distinguished them anyway, so
  showing "Fighter Aircraft" vs "Bomber" for an identical `"fixed-wing aircraft"`
  detection was fabricated precision. At the time this reduced ~272 objects to
  145 entries; the Prithvi sentinel entries were later removed with the Prithvi
  heads.
- A generic detection now shows an honest generic label ("Facility"), never a
  fabricated specific one ("Launch Pad").
- **`seed_ontology.py --reseed` prunes** objects absent from the JSON, so the
  revision fully applies on an existing DB instead of leaving orphan rows.
- **Scene-scoped vocabularies, wired end-to-end**: `default_prompts(sensor, branch)`
  and `GET /api/ontology/default-prompts?branch=` return one branch + its
  descendants. The inference `resolve_prompts()` honours `metadata.ontology_branch`
  in ontology mode, and `POST /api/ingest/upload` accepts an `ontology_branch`
  form field that the imagery worker threads into the inference request. An
  operator scoping a run to its mission branch detects against ~15–25 prompts
  instead of ~130.
- **`GLOBAL_CONFIDENCE_FLOOR`** raised 0.35 → 0.40. SAM 3 is calibrated, so this is
  a secondary lever; the prompt deconfliction is the primary fix.

## What was deliberately NOT done

- No invented corpus terms (`"missile silo"`, `"launch pad"`) — they are outside the
  models' training vocabulary and would detect poorly. Prompts stay plain noun
  phrases the models recognise.
- No synonym expansion, no domain prefixes — research shows both reduce recall.
- Branch `matchers` left unchanged — they are a free-text-normalization superset and
  a broader matcher set stays harmless.

## Measured impact

DOTA-v1.0 val (30 chips), `compare_inference_layers.py`:

- **Oracle prompts** (per-chip GT classes): `sam3+dota_obb` 0.60 mAP@0.5.
- **Ontology mode, full 131-prompt vocabulary** (`--ontology-mode`): `sam3+dota_obb`
  drops to 0.51 mAP; SAM 3 *alone* collapses 0.40 → 0.16 — confirming vocabulary
  width, not labelling, is the dominant false-positive driver. The `transportation`
  bucket runs at precision 0.035.
- See [benchmarks/detection-quality-eval-2026-05-22.md](../benchmarks/detection-quality-eval-2026-05-22.md)
  and [benchmarks/detection-quality-ontology-mode-2026-05-22.md](../benchmarks/detection-quality-ontology-mode-2026-05-22.md).

This is why scoping — not threshold tuning — is the headline fix.

## Cross-references

- [backend/ontology-system.md](../backend/ontology-system.md)
- [backend/detection-policy.md](../backend/detection-policy.md)
- [decisions/why-open-vocabulary.md](why-open-vocabulary.md)
- [decisions/why-category-presence-gate.md](why-category-presence-gate.md)

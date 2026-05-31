# Reclassify Storage Tank → Industrial, Helipad → Airfield in the ontology seed

**Date:** 2026-05-31
**Status:** adopted
**Source:** [backend/scripts/seeds/defenceOntology.seed.json](../../backend/scripts/seeds/defenceOntology.seed.json)

## Decision

Two ontology objects were filed under semantically wrong branches in the canonical seed.
Moved them to the correct branch:

- **Storage Tank**: `Airfield_Aviation` → `Industrial_Dual_Use`. A storage tank is industrial
  infrastructure (it now sits beside Oil or Gas Facility / Chimney / Water Treatment), not an
  aircraft. It was the only `Storage_Tank` object in the seed, so DOTA `storage-tank`
  detections were mislabelled `aircraft`.
- **Helipad**: `Military_Installations` → `Airfield_Aviation`. A helipad is aviation
  infrastructure; `Airfield_Aviation`'s own matcher regex already lists `\bhelipad\b`, so the
  seed had contradicted itself (the exact object in Military_Installations shadowed the
  matcher, mapping `helipad` → `military_installation`).

## Why

Surfaced by `scripts/eval_metrics/tests/test_label_normalizer.py` (DOTA→category mapping):
`storage-tank` resolved to `aircraft` and `helipad` to `military_installation`. The eval
normalizer mirrors runtime resolution (exact object label/id/prompt wins over branch matchers),
so the failures were genuine ontology-placement errors, not a normalizer or test-logic bug.
Both objects' canonical categories now match their real-world function.

## Scope / propagation

- Seed JSON edited (object count unchanged at 145; two `branch_id` reassignments).
- Applied to the running DB with `python -m scripts.seed_ontology --reseed` (UPSERT updates
  `branch_id`; `ontology_version` bumped → backend ontology cache + the inference
  `/api/ontology/default-prompts` 30 s cache refresh). Fresh installs pick it up via the
  normal seed.
- Affects branch-scoped prompts, the analyst ontology tree, and the canonical category /
  icon assigned to future `storage-tank` and `helipad` detections.

## Cross-references

- [backend/ontology-system.md](../backend/ontology-system.md)
- [why-deconflicted-detection-prompts.md](why-deconflicted-detection-prompts.md)
- [why-open-vocabulary.md](why-open-vocabulary.md)

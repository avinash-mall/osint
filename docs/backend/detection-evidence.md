# `backend/detection_evidence.py` — Evidence Ranking

**Path:** [backend/detection_evidence.py](../../backend/detection_evidence.py)
**Lines:** ~212
**Depends on:** env `EVIDENCE_MAX_ASPECT_RATIO`, `EVIDENCE_MIN_MASK_COMPACTNESS`, `EVIDENCE_MIN_VALID_FRACTION`

## Purpose

Assign each persisted imagery detection an evidence score, evidence tier, member-source list, physical validator results, reject reasons.

## Why this design

Open-vocab detections stay visible, but confirmed map objects need stronger evidence. Pure module, runs after calibration + georeferencing, using source provenance, WBF membership, semantic verifier output, SAR proxy flags, physical sanity checks. See [why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md).

## Key symbols

- [`apply_evidence_ranking`](../../backend/detection_evidence.py#L27-L86) — mutates one detection with `evidence_score`, `evidence_tier`, `member_sources`, `validator_results`, `reject_reasons`.
- [`validate_physics`](../../backend/detection_evidence.py#L88-L135) — checks bbox shape, valid fraction, size plausibility, edge truncation, SAR proxy warnings.
- [`_tier_for`](../../backend/detection_evidence.py#L137-L156) — maps score + policy signals → `confirmed` | `candidate` | `discovery`.
- [`_member_sources`](../../backend/detection_evidence.py#L158-L166) — preserves WBF/source-layer provenance.

## Inputs / Outputs

Input: worker detection dict after calibration, georeferencing, ontology normalization. Output: same dict enriched for PostGIS metadata; no rows deleted.

## Failure modes

Missing geometry / size estimates → warnings, not exceptions. SAR synthetic-preview detections capped to `candidate`/`discovery` unless from `sar_cfar`.

## Cross-references

- [worker-legacy-monolith.md](worker-legacy-monolith.md)
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)
- [decisions/why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md)

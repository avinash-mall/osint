# `backend/detection_evidence.py` — Evidence Ranking

**Path:** [backend/detection_evidence.py](../../backend/detection_evidence.py)
**Lines:** ~212
**Depends on:** env `EVIDENCE_MAX_ASPECT_RATIO`, `EVIDENCE_MIN_MASK_COMPACTNESS`, `EVIDENCE_MIN_VALID_FRACTION`

## Purpose

Assign each persisted imagery detection an evidence score, evidence tier, member-source list, physical validator results, and reject reasons.

## Why this design

Open-vocabulary detections must remain visible, but confirmed map objects should require stronger evidence. This pure module runs after calibration and georeferencing, using source provenance, WBF membership, semantic verifier output, SAR proxy flags, and physical sanity checks. See [why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md).

## Key symbols

- [`apply_evidence_ranking`](../../backend/detection_evidence.py#L27-L86) — mutates one detection with `evidence_score`, `evidence_tier`, `member_sources`, `validator_results`, and `reject_reasons`.
- [`validate_physics`](../../backend/detection_evidence.py#L88-L135) — checks bbox shape, valid fraction, size plausibility, edge truncation, and SAR proxy warnings.
- [`_tier_for`](../../backend/detection_evidence.py#L137-L156) — maps score and policy signals to `confirmed`, `candidate`, or `discovery`.
- [`_member_sources`](../../backend/detection_evidence.py#L158-L166) — preserves WBF/source-layer provenance.

## Inputs / Outputs

Input is a worker detection dict after calibration, georeferencing, and ontology normalization. Output is the same dict enriched for PostGIS metadata; no rows are deleted by this module.

## Failure modes

Missing geometry or size estimates produce warnings instead of exceptions. SAR synthetic-preview detections are capped to `candidate` or `discovery` unless they came from `sar_cfar`.

## Cross-references

- [worker-legacy-monolith.md](worker-legacy-monolith.md)
- [inference/remoteclip-verifier.md](../inference/remoteclip-verifier.md)
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)
- [decisions/why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md)

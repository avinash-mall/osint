# Why Evidence-Ranked Detections

## Decision

Detections are no longer treated as equally authoritative once they pass the global confidence floor. The backend enriches every persisted imagery detection with `evidence_score`, `evidence_tier`, source membership, semantic-verifier output, physical validator results, reject reasons.

## Why

The old hybrid stack allowed single-source open-vocabulary detections and specialist OBB detections to land in the same review stream with only confidence and broad source provenance separating them. Preserved recall, but made false positives too easy to promote in analyst workflows.

Evidence ranking keeps the open-vocabulary policy intact while making confirmation harder:

- closed-set OBB and CFAR detections get source trust, but still pass physical validators;
- SAM3/GDINO novel labels stay visible as `discovery` unless corroborated;
- RemoteCLIP-style semantic verification can promote, but never creates detections;
- SAR synthetic-preview labels stay conservative unless CFAR or other evidence supports them.

## Trade-offs accepted

- Some true open-vocabulary targets will enter as `discovery` instead of `confirmed`.
- The verifier adds optional latency when `SAM3_LOAD_REMOTECLIP=1`.
- Physical validators are broad sanity checks, not hard mission taxonomy rules; tune by environment before using as hard filters.

## Cross-references

- [backend/detection-evidence.md](../backend/detection-evidence.md)
- [inference/remoteclip-verifier.md](../inference/remoteclip-verifier.md)
- [inference/dota-obb-specialist.md](../inference/dota-obb-specialist.md)
- [decisions/why-open-vocabulary.md](why-open-vocabulary.md)

# Removed: FAIR1M-OBB detector + RemoteCLIP verifier

**Date:** 2026-05-31
**Status:** adopted (supersedes [why-fair1m-specialist.md](why-fair1m-specialist.md) and
[why-remoteclip-default-on.md](why-remoteclip-default-on.md))

## Decision

Permanently remove two inference layers that contributed no detections in practice while
carrying code, config, model-bake, and VRAM/latency cost:

- **FAIR1M-OBB** (`fair1m_obb.py`, `fair1m_gate.py`): shipped as a no-op stub — no checkpoint
  was ever baked (the runner returned `{model: None}`), so it produced zero detections on
  every deployment. Its fine-grained niche overlaps DOTA-OBB.
- **RemoteCLIP verifier** (`remoteclip_verifier.py`): a label re-ranker that never proposes
  boxes; weights were frequently absent (`loaded:false`) and its `semantic_margin` signal was
  measured at 0 contribution on the validated runs.

**Scope:** this decision covered FAIR1M-OBB and the RemoteCLIP verifier only. The open-vocabulary
Grounding-DINO / LAE-DINO layers were left in place at the time but were later removed as well —
see [removed-grounding-dino-lae.md](removed-grounding-dino-lae.md).

## What this touched

- inference-sam3: deleted `fair1m_obb.py`, `fair1m_gate.py`, `remoteclip_verifier.py` and
  their tests; removed imports, `SAM3_LOAD_FAIR1M_OBB` / `SAM3_LOAD_REMOTECLIP` /
  `REMOTECLIP_VERIFIER_LAYERS` flags, `PROFILE_COMPONENTS` entries, `_build_component` /
  `_empty_bundle` / `_version_snapshot` / health-map / load-flags wiring, the per-chip FAIR1M
  gate block, and the RemoteCLIP per-detection verify block in `_detect_pipeline`.
- `fusion.py`: dropped the `fair1m_obb` WBF trust weight.
- `Dockerfile.gpu`: dropped the RemoteCLIP HF bake (FAIR1M had no bake).
- `scripts/gpu_profiles.py`, `docker-compose.yml`, `.env.example`: removed the env flags.
- Backend: dropped `fair1m_obb` from the calibration temperature map + its test. The generic
  `semantic_margin` / `semantic_verifier` evidence-ranking plumbing is **kept** — it degrades
  gracefully (margin defaults to 0 → labels stay "inferred"/"generic", never "verified"; no
  demotion, no errors) and can be fed by a future verifier.
- Frontend: dropped the `fair1m_obb` source-layer label; reworded the "verified" chip tooltip
  to drop the RemoteCLIP name.

## Kept deliberately (NOT the layer)

The **FAIR1M reference dataset** stays: `scripts/fetch_reference_datasets.py` still fetches it
(CC-BY-4.0) for the platform-identification embedding corpus, and the FAIR1M label-source
normalization (`detection_policy.SOURCE_PREFIXES`, `detectionTaxonomy.ts`) stays. These serve
reference-platform re-ID, which is independent of the removed detector.

## Cross-references

- [why-dynamic-modality-loading-on-tight-vram.md](why-dynamic-modality-loading-on-tight-vram.md)
- [why-precision-first-inference-defaults.md](why-precision-first-inference-defaults.md)
- [inference/service-overview.md](../inference/service-overview.md)
- [inference/model-manifest.md](../inference/model-manifest.md)

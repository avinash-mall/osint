# `inference-sam3/remoteclip_verifier.py` — RemoteCLIP Verifier

**Path:** [inference-sam3/remoteclip_verifier.py](../../inference-sam3/remoteclip_verifier.py)
**Lines:** ~174
**Depends on:** `open_clip_torch`, `huggingface_hub`, `torch`, env `SAM3_LOAD_REMOTECLIP` (default `1`), `REMOTECLIP_VERIFIER_LAYERS` (default `sam3,grounding_dino`), `REMOTECLIP_MODEL_ID`, `REMOTECLIP_ARCH`, `REMOTECLIP_MARGIN_THRESHOLD`, `REMOTECLIP_LOCAL_FILES_ONLY`

## Purpose

Semantic verifier for existing detection crops. Scores candidate labels against the crop + context patch, returns verifier metadata for backend evidence ranking.

## Why this design

RemoteCLIP is a remote-sensing vision-language model → better verifier for satellite crops than generic CLIP. Never proposes detections — avoids turning a semantic classifier into another false-positive source. Loading is optional and fail-closed → air-gapped deployments without baked weights keep running. Default-on for the imagery profile as of T1.6 (weights baked in `Dockerfile.gpu`), but the per-detection verify call is gated to `source_layer ∈ REMOTECLIP_VERIFIER_LAYERS` — DOTA-OBB is deliberately excluded so its closed-vocab 18-class detector is not second-guessed by an open-vocab text matcher. See [decisions/why-remoteclip-default-on.md](../decisions/why-remoteclip-default-on.md).

## Key symbols

- [`load`](../../inference-sam3/remoteclip_verifier.py#L24-L64) — best-effort OpenCLIP + RemoteCLIP checkpoint loader.
- [`verify`](../../inference-sam3/remoteclip_verifier.py#L66-L122) — scores a crop against labels, returns `semantic_margin`, `passed`, `top_labels`.
- [`model_versions`](../../inference-sam3/remoteclip_verifier.py#L124-L134) — exposes loaded state in `/health`.
- [`_crop_with_context`](../../inference-sam3/remoteclip_verifier.py#L136-L156) — pads candidate boxes before verifier scoring.
- `REMOTECLIP_VERIFIER_LAYERS` ([inference-sam3/main.py#L211-L213](../../inference-sam3/main.py#L211-L213)) — module-level frozenset of source layers that may invoke `verify`; populated from `REMOTECLIP_VERIFIER_LAYERS` env (default `sam3,grounding_dino`).

## Inputs / Outputs

Input: the full chip, a detector-provided pixel bbox, candidate labels. Output: a JSON-safe verifier record stored on the detection as `semantic_verifier`; backend copies `semantic_margin` into persisted metadata. Detections whose `source_layer` is outside the gate carry no `semantic_verifier` / `semantic_margin` field and stay at their detector-native label quality.

## Failure modes

Missing dependency / weights, tiny crops, or verifier runtime errors → return `enabled=false`; detections continue through the pipeline without semantic promotion. `REMOTECLIP_LOCAL_FILES_ONLY=1` is the default → runtime never downloads weights. If an operator widens `REMOTECLIP_VERIFIER_LAYERS` to include `dota_obb`, the closed-vocab DOTA labels become subject to open-vocab CLIP scoring — expected drop in DOTA-class precision (see decision doc).

## Cross-references

- [main-app-entrypoint.md](main-app-entrypoint.md)
- [model-manifest.md](model-manifest.md)
- [backend/detection-evidence.md](../backend/detection-evidence.md)
- [decisions/why-remoteclip-default-on.md](../decisions/why-remoteclip-default-on.md)
- [decisions/why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md)

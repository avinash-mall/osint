# `inference-sam3/remoteclip_verifier.py` — RemoteCLIP Verifier

**Path:** [inference-sam3/remoteclip_verifier.py](../../inference-sam3/remoteclip_verifier.py)
**Lines:** ~174
**Depends on:** `open_clip_torch`, `huggingface_hub`, `torch`, env `SAM3_LOAD_REMOTECLIP`, `REMOTECLIP_MODEL_ID`, `REMOTECLIP_ARCH`, `REMOTECLIP_MARGIN_THRESHOLD`, `REMOTECLIP_LOCAL_FILES_ONLY`

## Purpose

Semantic verifier for existing detection crops. It scores candidate labels against the crop and context patch, then returns verifier metadata for backend evidence ranking.

## Why this design

RemoteCLIP is a remote-sensing vision-language model, so it is a better verifier for satellite crops than generic CLIP. It never proposes detections; this avoids turning a semantic classifier into another false-positive source. Loading is optional and fail-closed so air-gapped deployments without baked weights keep running.

## Key symbols

- [`load`](../../inference-sam3/remoteclip_verifier.py#L24-L64) — best-effort OpenCLIP + RemoteCLIP checkpoint loader.
- [`verify`](../../inference-sam3/remoteclip_verifier.py#L66-L122) — scores a crop against labels and returns `semantic_margin`, `passed`, and `top_labels`.
- [`model_versions`](../../inference-sam3/remoteclip_verifier.py#L124-L134) — exposes loaded state in `/health`.
- [`_crop_with_context`](../../inference-sam3/remoteclip_verifier.py#L136-L156) — pads candidate boxes before verifier scoring.

## Inputs / Outputs

Input is the full chip, a detector-provided pixel bbox, and candidate labels. Output is a JSON-safe verifier record stored on the detection as `semantic_verifier`; the backend copies `semantic_margin` into persisted metadata.

## Failure modes

Missing dependency, missing weights, tiny crops, or verifier runtime errors all return `enabled=false`; detections continue through the pipeline without semantic promotion. `REMOTECLIP_LOCAL_FILES_ONLY=1` is the default so runtime never downloads weights.

## Cross-references

- [main-app-entrypoint.md](main-app-entrypoint.md)
- [model-manifest.md](model-manifest.md)
- [backend/detection-evidence.md](../backend/detection-evidence.md)
- [decisions/why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md)

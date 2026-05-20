# `inference-sam3/requirements.txt` — Python Dependencies

**Path:** [inference-sam3/requirements.txt](../../inference-sam3/requirements.txt)
**Lines:** ~19
**Depends on:** `pip`, CUDA/PyTorch preinstall from [Dockerfile.gpu](../../inference-sam3/Dockerfile.gpu)

## Purpose

List inference-service Python packages installed after PyTorch and SAM3 are already present in the image.

## Why this design

PyTorch is pinned by GPU profile and installed before this file. Domain libraries then install from one small requirements file. `open_clip_torch` is included only so the optional RemoteCLIP verifier can load baked weights; if the verifier is disabled, it does not participate in `/detect`.

## Key symbols

- `transformers` — Grounding-DINO and DINOv3-SAT loader dependency.
- `ultralytics` — DOTA-OBB and YOLOE runtime.
- `open_clip_torch` — optional RemoteCLIP verifier runtime.

## Inputs / Outputs

Input is a pip requirements file. Output is the Python environment inside `sentinel-inference-sam3:gpu`.

## Failure modes

Dependency resolution failures stop image build. Runtime remains offline because packages are installed during build, not pulled by the service.

## Cross-references

- [deployment/inference-gpu-dockerfile.md](../deployment/inference-gpu-dockerfile.md)
- [remoteclip-verifier.md](remoteclip-verifier.md)

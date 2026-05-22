# `inference-sam3/requirements.txt` — Python Dependencies

**Path:** [inference-sam3/requirements.txt](../../inference-sam3/requirements.txt)
**Lines:** ~19
**Depends on:** `pip`, CUDA/PyTorch preinstall from [Dockerfile.gpu](../../inference-sam3/Dockerfile.gpu)

## Purpose

Lists inference-service Python packages installed after PyTorch + SAM3 are already in the image.

## Why this design

PyTorch pinned by GPU profile, installed before this file. Domain libraries then install from one small requirements file. `open_clip_torch` included only so the optional RemoteCLIP verifier can load baked weights; verifier disabled → it doesn't participate in `/detect`.

## Key symbols

- `transformers` — Grounding-DINO + DINOv3-SAT loader dependency.
- `ultralytics` — DOTA-OBB + YOLOE runtime.
- `open_clip_torch` — optional RemoteCLIP verifier runtime.

## Inputs / Outputs

Input: a pip requirements file. Output: the Python environment inside `sentinel-inference-sam3:gpu`.

## Failure modes

Dependency resolution failures stop image build. Runtime stays offline — packages installed during build, not pulled by the service.

## Cross-references

- [deployment/inference-gpu-dockerfile.md](../deployment/inference-gpu-dockerfile.md)
- [remoteclip-verifier.md](remoteclip-verifier.md)

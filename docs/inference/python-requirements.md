# `inference-sam3/requirements.txt` — Python Dependencies

**Path:** [inference-sam3/requirements.txt](../../inference-sam3/requirements.txt)
**Lines:** ~24
**Depends on:** `pip`, CUDA/PyTorch preinstall from [Dockerfile.gpu](../../inference-sam3/Dockerfile.gpu)

## Purpose

Lists inference-service Python packages installed after PyTorch + SAM3 are already in the image.

## Why this design

PyTorch pinned by GPU profile, installed before this file. Domain libraries then install from one small requirements file.

**Pinning policy:** every direct dependency is pinned with `==` to the exact version
resolved in the running container (`pip freeze`), not a fresh re-resolution — a
known-good set that keeps rebuilds (including air-gapped / BuildKit-cache-pruned ones)
reproducible instead of drifting. Bump deliberately and re-freeze. `torch`/`torchvision`/
`torchaudio` are intentionally absent here: they are GPU-profile-injected as `Dockerfile.gpu`
build ARGs from [scripts/gpu_profiles.py](../../scripts/gpu_profiles.py). The backend
([backend/requirements.txt](../../backend/requirements.txt)) follows the same `==`-pinned
policy (with its own CPU-only `torch==…+cpu`).

## Key symbols

- `transformers` — DINOv3-SAT loader dependency.
- `ultralytics` — DOTA-OBB + YOLOE runtime.

## Inputs / Outputs

Input: a pip requirements file. Output: the Python environment inside `sentinel-inference-sam3:gpu`.

## Failure modes

Dependency resolution failures stop image build. Runtime stays offline — packages installed during build, not pulled by the service.

## Cross-references

- [deployment/inference-gpu-dockerfile.md](../deployment/inference-gpu-dockerfile.md)

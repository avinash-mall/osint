# `inference-sam3/Dockerfile.gpu` — GPU Inference Image

**Path:** [inference-sam3/Dockerfile.gpu](../../inference-sam3/Dockerfile.gpu)
**Lines:** ~162
**Depends on:** CUDA base image, PyTorch wheels, [inference-sam3/requirements.txt](../../inference-sam3/requirements.txt), `HF_TOKEN`

## Purpose

Build the GPU inference image and pre-bake model weights so the runtime service can run offline.

## Why this design

The image downloads Python dependencies and optional model weights at build time, then runtime containers use the populated `/models` cache. The DOTA bake now stages both `yolo26m-obb.pt` and `yolo11n-obb.pt`; RemoteCLIP is baked best-effort for verifier deployments but remains runtime-disabled by default.

## Key symbols

- `ARG TORCH_VERSION` / `ARG TORCHVISION_VERSION` — PyTorch version pair used with the configured CUDA wheel index.
- `RUN ... huggingface-cli download ...` — build-time weight cache population.
- `RUN python /tmp/verify_bake.py` — required-weight sanity check.

## Inputs / Outputs

Inputs are Docker build args, `HF_TOKEN`, and the checked-in inference service tree. Output is `sentinel-inference-sam3:gpu`.

## Failure modes

Optional heads log and continue when unavailable. Required SAM3 or DINOv3-SAT weights fail the build in `verify_bake.py`.

## Cross-references

- [inference/service-overview.md](../inference/service-overview.md)
- [inference/model-manifest.md](../inference/model-manifest.md)
- [deployment/offline-airgap-deployment.md](offline-airgap-deployment.md)

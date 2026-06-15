# `inference-sam3/Dockerfile.gpu` — GPU Inference Image

**Path:** [inference-sam3/Dockerfile.gpu](../../inference-sam3/Dockerfile.gpu)
**Lines:** ~176
**Depends on:** CUDA base image, PyTorch wheels, [inference-sam3/requirements.txt](../../inference-sam3/requirements.txt), `HF_TOKEN`, `MVRSD_WEIGHTS_URL` + `GITHUB_TOKEN` (MVRSD bake)

## Purpose

Build the GPU inference image, pre-bake model weights so the runtime service runs offline.

## Why this design

Image downloads Python dependencies + optional model weights at build time; runtime containers use the populated `/models` cache. DOTA bake stages both `yolo26m-obb.pt` and `yolo11n-obb.pt`; YOLOE stages the MobileCLIP2 text encoder; the default-ON MVRSD military-vehicle specialist is baked from `MVRSD_WEIGHTS_URL` to `/models/mvrsd/mvrsd_yolo11m.pt` (hard rule #8: no runtime downloads). The MVRSD repo is **private**, so `MVRSD_WEIGHTS_URL` is the GitHub **API** asset endpoint and the `GITHUB_TOKEN` build ARG must carry a valid Bearer token; the bake `curl`s the asset with that token. An empty URL is a no-op and an empty/wrong token 404s — both are swallowed by the `|| echo … failed` fallback so the build still succeeds, leaving `/models/mvrsd/` empty so `mvrsd.load()` honour-gates (model=None, zero candidates). See [why-mvrsd-military-vehicle-specialist.md](../decisions/why-mvrsd-military-vehicle-specialist.md). The RemoteCLIP verifier, FAIR1M-OBB detector, and Prithvi heads were removed.

## Key symbols

- `ARG TORCH_VERSION` / `ARG TORCHVISION_VERSION` — PyTorch version pair used with the configured CUDA wheel index.
- `RUN ... huggingface-cli download ...` — build-time weight cache population.
- `RUN python /tmp/verify_bake.py` — required-weight sanity check.

## Inputs / Outputs

Inputs: Docker build args, `HF_TOKEN`, the checked-in inference service tree. Output: `sentinel-inference-sam3:gpu`.

## Failure modes

Optional assets log and continue when unavailable. Required SAM3 or DINOv3-SAT weights fail the build in `verify_bake.py`. The MVRSD bake is intentionally tolerant: an empty `MVRSD_WEIGHTS_URL`, or an authenticated `curl` that 404s because `GITHUB_TOKEN` is empty/invalid against the private asset, both log `[bake] mvrsd weight …` and continue — the layer then loads-but-empty at runtime (honour-gate) rather than blocking the build.

## Cross-references

- [inference/service-overview.md](../inference/service-overview.md)
- [inference/model-manifest.md](../inference/model-manifest.md)
- [deployment/offline-airgap-deployment.md](offline-airgap-deployment.md)
- [decisions/why-mvrsd-military-vehicle-specialist.md](../decisions/why-mvrsd-military-vehicle-specialist.md)

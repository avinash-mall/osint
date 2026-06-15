# `inference-sam3/Dockerfile.gpu` — GPU Inference Image

**Path:** [inference-sam3/Dockerfile.gpu](../../inference-sam3/Dockerfile.gpu)
**Lines:** ~194
**Depends on:** CUDA base image, PyTorch wheels, [inference-sam3/requirements.txt](../../inference-sam3/requirements.txt), `HF_TOKEN`, `MVRSD_WEIGHTS_URL` + `GITHUB_TOKEN` (MVRSD bake), `model_cache` build context (compose `additional_contexts`, default `./model-cache`)

## Purpose

Build the GPU inference image, pre-bake model weights so the runtime service runs offline.

## Why this design

Image downloads Python dependencies + optional model weights at build time; runtime containers use the populated `/models` cache. **Offline reuse:** the bake `RUN` first bind-mounts the `model_cache` build context (compose `additional_contexts`, default `./model-cache`); when that context carries an HF hub (a previously-baked `/models` tree, ~16 GB staged via `docker cp <inference-container>:/models/. ./model-cache/`), the step `cp -a`s it into `/models` and exports `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1`, so every `huggingface-cli download` / ultralytics / terratorch call below becomes a cache-only no-op and the MVRSD `curl` is skipped (`-s` guard). This lets an **air-gapped or BuildKit-cache-pruned host rebuild the image without re-fetching from HF**; an empty/`.gitkeep`-only context (fresh clone / connected host) falls through to the normal online bake. See [offline-airgap-deployment.md](offline-airgap-deployment.md). DOTA bake stages both `yolo26m-obb.pt` and `yolo11n-obb.pt`; YOLOE stages the MobileCLIP2 text encoder; the default-ON MVRSD military-vehicle specialist is baked from `MVRSD_WEIGHTS_URL` to `/models/mvrsd/mvrsd_yolo11m.pt` (hard rule #8: no runtime downloads). The MVRSD repo is **private**, so `MVRSD_WEIGHTS_URL` is the GitHub **API** asset endpoint and the `GITHUB_TOKEN` build ARG must carry a valid Bearer token; the bake `curl`s the asset with that token. An empty URL is a no-op and an empty/wrong token 404s — both are swallowed by the `|| echo … failed` fallback so the build still succeeds, leaving `/models/mvrsd/` empty so `mvrsd.load()` honour-gates (model=None, zero candidates). See [why-mvrsd-military-vehicle-specialist.md](../decisions/why-mvrsd-military-vehicle-specialist.md). The RemoteCLIP verifier, FAIR1M-OBB detector, and Prithvi heads were removed.

## Key symbols

- `ARG TORCH_VERSION` / `ARG TORCHVISION_VERSION` — PyTorch version pair used with the configured CUDA wheel index.
- `RUN --mount=type=bind,from=model_cache … huggingface-cli download …` — offline-reuse seed (copy local `/model-cache` → `/models` + go offline) followed by build-time weight cache population (no-ops when seeded).
- `RUN python /tmp/verify_bake.py` — required-weight sanity check.

## Inputs / Outputs

Inputs: Docker build args, `HF_TOKEN`, the checked-in inference service tree. Output: `sentinel-inference-sam3:gpu`.

## Failure modes

Optional assets log and continue when unavailable. Required SAM3 or DINOv3-SAT weights fail the build in `verify_bake.py` — on an air-gapped host with no `model_cache` staged this is the expected stop (nothing to download), so stage `./model-cache` first or build on a connected host. When `model_cache` is seeded, the downloads are offline no-ops and the seeded MVRSD weight is preserved (the `-s` guard skips the `curl`). The MVRSD bake is intentionally tolerant: an empty `MVRSD_WEIGHTS_URL`, or an authenticated `curl` that 404s because `GITHUB_TOKEN` is empty/invalid against the private asset, both log `[bake] mvrsd weight …` and continue — the layer then loads-but-empty at runtime (honour-gate) rather than blocking the build.

## Cross-references

- [inference/service-overview.md](../inference/service-overview.md)
- [inference/model-manifest.md](../inference/model-manifest.md)
- [deployment/offline-airgap-deployment.md](offline-airgap-deployment.md)
- [decisions/why-mvrsd-military-vehicle-specialist.md](../decisions/why-mvrsd-military-vehicle-specialist.md)

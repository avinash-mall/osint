# Inference Service — Overview

**Path:** [inference-sam3/](../../inference-sam3/)
**Entry:** [inference-sam3/main.py](../../inference-sam3/main.py)
**Image:** `sentinel-inference-sam3:gpu` (from [inference-sam3/Dockerfile.gpu](../../inference-sam3/Dockerfile.gpu))
**Port:** internal 8001

## Purpose

FastAPI service for the main inference pool. LAE-DINO runs in its own sidecar,
but is exposed through the `grounding_dino` layer in this service:

| Component | Repo / source | Module |
|---|---|---|
| SAM 3 (image) | `facebook/sam3` (gated) or `1038lab/sam3` (mirror) | [sam3_runner.py](../../inference-sam3/sam3_runner.py) |
| SAM 3.1 (video) | `facebook/sam3.1` (gated) | [sam3_runner.py](../../inference-sam3/sam3_runner.py) |
| YOLOE-26x-seg(-pf) FMV tracker | Open AGPL-3.0 weights | [yoloe.py](../../inference-sam3/yoloe.py) |
| DINOv3-SAT-L | `facebook/dinov3-vitl16-pretrain-sat493m` (gated) | [embedding.py](../../inference-sam3/embedding.py) |
| TerraMind v1 (S1→S2) | IBM TerraMind | [terramind.py](../../inference-sam3/terramind.py) |
| DOTA-OBB | Ultralytics `yolo26m-obb.pt` (`yolo11n-obb.pt` fallback) | [dota_obb.py](../../inference-sam3/dota_obb.py) |
| LAE-DINO sidecar client (`grounding_dino` layer) | `inference-lae` HTTP service | [grounding_dino.py](../../inference-sam3/grounding_dino.py), [lae-dino-sidecar.md](lae-dino-sidecar.md) |
| MVRSD military-vehicle (default-on, RGB profile) | `yolo11m` fine-tuned on MVRSD (GitHub release asset) | [mvrsd.py](../../inference-sam3/mvrsd.py) |

Runtime memory pool holds one **profile** at a time, swappable via `/load?profile=`. Profiles
are `fmv`, the per-modality imagery profiles `imagery_rgb` / `imagery_msi` / `imagery_sar`, the
`imagery` union (all three), and `all` (everything). `/detect` auto-routes to the per-modality
profile via `_profile_for_modality(modality)`; a resident superset (`imagery` or `all`) serves
any subset without a reload. On tight-VRAM cards (`SAM3_LOAD_POLICY=dynamic`) only one modality
profile is resident at a time so a 16 GiB GPU can serve RGB/MSI/SAR/FMV by swapping. See
[profile-pool-lifecycle.md](profile-pool-lifecycle.md) and
[decisions/why-dynamic-modality-loading-on-tight-vram.md](../decisions/why-dynamic-modality-loading-on-tight-vram.md).

## Endpoints

| Method | Path | Use |
|---|---|---|
| `GET` | `/health` | Loaded models, replicas, active requests, model versions, VRAM |
| `GET` | `/health/memory` | Per-component memory snapshot |
| `POST` | `/health/memory/reset` | Clear PyTorch caching allocator |
| `POST` | `/load?profile=` | Force-load `imagery_rgb` / `imagery_msi` / `imagery_sar` / `imagery` / `fmv` / `all` |
| `POST` | `/unload` | Tear down and respawn (only reliable way to free SAM3 VRAM) |
| `POST` | `/detect` | Per-chip image segmentation (multipart `image` + JSON `metadata`) |
| `POST` | `/detect_video` | FMV tracking — multipart `video`; streams `application/x-ndjson` |

Full per-modality request contract: [main-app-entrypoint.md](main-app-entrypoint.md).

## VRAM budget (RTX 5070 Ti, 16 GB)

| Components | Steady-state VRAM |
|---|---|
| SAM 3 + SAM 3.1 video + DINOv3-SAT-L + DOTA-OBB + YOLOE + MVRSD (FMV/all profile; LAE sidecar separate) | ~12 GB |
| + TerraMind | ~16 GB (24 GB+ card recommended) |

Per-component flags: [main-app-entrypoint.md](main-app-entrypoint.md) and the env table in [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md).

## Cross-references

- [main-app-entrypoint.md](main-app-entrypoint.md) — request/response shapes
- [profile-pool-lifecycle.md](profile-pool-lifecycle.md)
- [decisions/why-sam3-as-foundation.md](../decisions/why-sam3-as-foundation.md)
- [decisions/why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)

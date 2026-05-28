# Inference Service — Overview

**Path:** [inference-sam3/](../../inference-sam3/)
**Entry:** [inference-sam3/main.py](../../inference-sam3/main.py)
**Image:** `sentinel-inference-sam3:gpu` (from [inference-sam3/Dockerfile.gpu](../../inference-sam3/Dockerfile.gpu))
**Port:** internal 8001

## Purpose

Single FastAPI service bundling every model the platform uses:

| Component | Repo / source | Module |
|---|---|---|
| SAM 3 (image) | `facebook/sam3` (gated) or `1038lab/sam3` (mirror) | [sam3_runner.py](../../inference-sam3/sam3_runner.py) |
| SAM 3.1 (video) | `facebook/sam3.1` (gated) | [sam3_runner.py](../../inference-sam3/sam3_runner.py) |
| YOLOE-26x-seg(-pf) FMV tracker | Open AGPL-3.0 weights | [yoloe.py](../../inference-sam3/yoloe.py) |
| DINOv3-SAT-L | `facebook/dinov3-vitl16-pretrain-sat493m` (gated) | [embedding.py](../../inference-sam3/embedding.py) |
| Prithvi-EO-2.0 (flood/burn) | `ibm-nasa-geospatial/Prithvi-EO-V2-300M` | [prithvi_heads.py](../../inference-sam3/prithvi_heads.py) |
| TerraMind v1 (S1→S2) | IBM TerraMind | [terramind.py](../../inference-sam3/terramind.py) |
| DOTA-OBB | Ultralytics `yolo26m-obb.pt` (`yolo11n-obb.pt` fallback) | [dota_obb.py](../../inference-sam3/dota_obb.py) |
| Grounding-DINO | `IDEA-Research/grounding-dino-*` | [grounding_dino.py](../../inference-sam3/grounding_dino.py) |
| RemoteCLIP verifier | `chendelong/RemoteCLIP` | [remoteclip_verifier.py](../../inference-sam3/remoteclip_verifier.py) |

Runtime memory pool holds one of three **profiles** swappable via `/load?profile=`. See [profile-pool-lifecycle.md](profile-pool-lifecycle.md).

## Endpoints

| Method | Path | Use |
|---|---|---|
| `GET` | `/health` | Loaded models, replicas, active requests, model versions, VRAM |
| `GET` | `/health/memory` | Per-component memory snapshot |
| `POST` | `/health/memory/reset` | Clear PyTorch caching allocator |
| `POST` | `/load?profile=` | Force-load `imagery` / `fmv` / `all` |
| `POST` | `/unload` | Tear down and respawn (only reliable way to free SAM3 VRAM) |
| `POST` | `/detect` | Per-chip image segmentation (multipart `image` + JSON `metadata`) |
| `POST` | `/detect_video` | FMV tracking — multipart `video`; streams `application/x-ndjson` |

Full per-modality request contract: [main-app-entrypoint.md](main-app-entrypoint.md).

## VRAM budget (RTX 5070 Ti, 16 GB)

| Components | Steady-state VRAM |
|---|---|
| SAM 3 + SAM 3.1 video + DINOv3-SAT-L + DOTA-OBB + GDINO + YOLOE (FMV/all profile) | ~12 GB |
| + Prithvi + TerraMind | ~22 GB (24 GB+ card required) |
| + RemoteCLIP verifier | Optional extra VRAM; disabled by default |

Per-component flags: [main-app-entrypoint.md](main-app-entrypoint.md) and the env table in [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md).

## Cross-references

- [main-app-entrypoint.md](main-app-entrypoint.md) — request/response shapes
- [profile-pool-lifecycle.md](profile-pool-lifecycle.md)
- [decisions/why-sam3-as-foundation.md](../decisions/why-sam3-as-foundation.md)
- [decisions/why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)

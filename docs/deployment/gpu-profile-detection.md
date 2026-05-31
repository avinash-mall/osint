# GPU Profile Detection — `scripts/configure_host.py`

**Source:** [scripts/configure_host.py](../../scripts/configure_host.py), [scripts/gpu_profiles.py](../../scripts/gpu_profiles.py)

## Purpose

Read `nvidia-smi`, resolve the matching CUDA / PyTorch / TorchVision / arch-list profile (Turing through Blackwell), write a `SENTINEL GENERATED GPU CONFIG` block into `.env`.

**Run before any GPU build.** Do not hand-edit the generated block or copy it between machines.

## Supported profiles

| GPU family | Profile | CUDA | PyTorch | TorchVision |
|---|---|---|---|---|
| Turing (T4, sm_75) | `turing` | 12.4 | 2.6 | 0.21 |
| Ampere (A100 sm_80 / A40 sm_86) | `ampere` | 12.4 | 2.6 | 0.21 |
| Hopper (H100, sm_90) | `hopper` | 12.6 | 2.6 | 0.21 |
| Blackwell (RTX 50, sm_120) | `blackwell` | 12.8 | 2.7 | 0.22 |

## Build args written to .env

- `SAM3_CUDA_VERSION`, `SAM3_TORCH_INDEX_URL`, `SAM3_TORCH_VERSION`, `SAM3_TORCHVISION_VERSION`, `SAM3_TORCH_CUDA_ARCH_LIST`, `SAM3_GPU_PROFILE`, `SAM3_UBUNTU_VERSION`
- `SAM3_ENABLE_TF32` (sm_80+ only)
- `SAM3_CUDNN_BENCHMARK` (off on Turing; cu126 re-searches kernels)

## Loading policy: hot vs dynamic (VRAM-gated)

`runtime_env(vram_mib=…)` emits a **loading policy** based on measured total VRAM, gated at
`sam3_hot_load_min_vram_mib` (default 24 GiB). This prevents the failure where a 16 GiB card
loaded every model at once and OOMed on every SAM3 forward — see
[decisions/why-dynamic-modality-loading-on-tight-vram.md](../decisions/why-dynamic-modality-loading-on-tight-vram.md).

- **`SAM3_LOAD_POLICY=hot`** (VRAM ≥ threshold): honours the profile's own preload fields; the
  full `imagery` model union stays resident, no swap latency. `SAM3_RESTING_PROFILE=imagery`.
- **`SAM3_LOAD_POLICY=dynamic`** (VRAM < threshold): one **per-modality** profile resident at a
  time. `SAM3_RESTING_PROFILE=imagery_rgb`; the inference service swaps to `imagery_msi`,
  `imagery_sar`, or `fmv` on the first request of that modality (profiles in
  `inference-sam3/main.py` `PROFILE_COMPONENTS`, routed by `_profile_for_modality`). No preload
  (`SAM3_PRELOAD_MODELS=0`). All four modalities stay available — nothing is permanently lost.

On dynamic cards the proven dead-weight detectors are also gated off (≈0 net-new boxes, real
VRAM+latency): `SAM3_LOAD_GROUNDING_DINO=0`, `SAM3_LOAD_FAIR1M_OBB=0`, `SAM3_LOAD_REMOTECLIP=0`.
`SAM3_LOAD_DINOV3_SAT/PRITHVI/TERRAMIND` stay enabled — the per-modality split keeps
Prithvi/Terramind out of the RGB working set instead of dropping them.

New flags written to `.env`: `SAM3_LOAD_POLICY`, `SAM3_RESTING_PROFILE`, `SAM3_LOAD_FAIR1M_OBB`,
`SAM3_LOAD_REMOTECLIP`. The docker-compose `inference-sam3` `environment:` block must pass each
through for it to reach the container.

## Preflight failure

Preflight fails before build when a profile requires a newer host driver. E.g. Blackwell profile asks for driver 555.x+; on 535.x, `configure_host.py` reports the missing minimum and exits non-zero.

## Re-run when

- After upgrading the GPU.
- After upgrading the NVIDIA driver.

## Cross-references

- [scripts/configure-host-gpu.md](../scripts/configure-host-gpu.md)
- [decisions/disable-addmm-cuda-lt.md](../decisions/disable-addmm-cuda-lt.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)

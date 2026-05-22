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

## Preflight failure

Preflight fails before build when a profile requires a newer host driver. E.g. Blackwell profile asks for driver 555.x+; on 535.x, `configure_host.py` reports the missing minimum and exits non-zero.

## Re-run when

- After upgrading the GPU.
- After upgrading the NVIDIA driver.

## Cross-references

- [scripts/configure-host-gpu.md](../scripts/configure-host-gpu.md)
- [decisions/disable-addmm-cuda-lt.md](../decisions/disable-addmm-cuda-lt.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)

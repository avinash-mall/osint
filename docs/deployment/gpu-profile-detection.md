# GPU Profile Detection — `scripts/configure_host.py`

**Source:** [scripts/configure_host.py](../../scripts/configure_host.py), [scripts/gpu_profiles.py](../../scripts/gpu_profiles.py)

## Purpose

Read `nvidia-smi`, resolve the matching CUDA / PyTorch / TorchVision / arch-list profile (Turing through Blackwell), write a `SENTINEL GENERATED GPU CONFIG` block into `.env`.

**Run before any GPU build.** Do not hand-edit the generated block or copy it between machines.

## Supported profiles

| GPU family | Profile | CUDA wheel | PyTorch | TorchVision |
|---|---|---|---|---|
| Turing (T4, sm_75) | `turing_sm75` | cu126 | 2.7.1 | 0.22.1 |
| Ampere consumer (RTX 30, sm_80/86) | `ampere_sm80_86` | cu130 | 2.10.0 | 0.25.0 |
| Ampere datacenter (A100/A40, sm_80/86) | `ampere_sm80_86_datacenter` | cu130 | 2.10.0 | 0.25.0 |
| Ada (RTX 40 / L40, sm_89) | `ada_sm89` | cu126 | 2.7.1 | 0.22.1 |
| Hopper (H100/H200, sm_90) | `hopper_sm90` | cu130 | 2.10.0 | 0.25.0 |
| Blackwell datacenter (B200, sm_100) | `blackwell_sm100` | cu130 | 2.10.0 | 0.25.0 |
| **Blackwell consumer (RTX 50, sm_120)** | `blackwell_sm120` | **cu132** | **2.12.0** | **0.27.0** |

Per-architecture build lines are deliberate. Most cards ride the cu130/torch-2.10
baseline; **consumer Blackwell (sm_120) is pinned to the latest line — torch 2.12
on CUDA 13.2 (cu132)** — to get the newest kernels for that silicon. cu132 is
PyTorch's experimental channel: verify a build + inference pass on real Blackwell
hardware before production. Note the cu13x concurrent-forward CUDA-context poison
is **version-independent** (reproduced on cu128/cu130/cu132 — see
[decisions/why-serialize-forwards-on-a100-cu13x.md](../decisions/why-serialize-forwards-on-a100-cu13x.md));
the sm_120 bump is about kernel currency, not that bug.

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

## Throughput knobs (VRAM- and GPU-count-derived)

Two optical-throughput optimisations are sized to the host automatically (see
[decisions/optical-inference-throughput.md](../decisions/optical-inference-throughput.md)):

- **`SAM3_EMBED_BATCH_SIZE`** — crops per DINOv3 forward in the batched per-detection
  embedding path. VRAM-tiered via the profile field `sam3_embed_batch_size`: Turing 16;
  consumer Ampere / Ada / consumer Blackwell 32; datacenter Ampere (A100) 64; Hopper and
  datacenter Blackwell 96.
- **`INFERENCE_CHIP_CONCURRENCY`** and **`INFERENCE_MIN_PENDING_CHIPS`** — derived from the
  **GPU count** (`len(info.gpus)`), not the profile, because the inference pool runs one model
  replica per visible GPU. `configure_host` raises concurrency to `max(profile_baseline, gpu_count)`
  so the worker's poster pool can feed every replica, and sets the adaptive back-off floor
  `INFERENCE_MIN_PENDING_CHIPS = gpu_count` so it never collapses onto one GPU under latency
  variance. A single-GPU host keeps the profile baseline (concurrency 1, floor 1).

The docker-compose `inference-sam3` block must pass `SAM3_EMBED_BATCH_SIZE`, and the `worker`
block `INFERENCE_MIN_PENDING_CHIPS`, for these to reach the containers.

## Per-process VRAM ceiling (manual only)

`configure_host` **no longer auto-derives a VRAM cap.** It used to read `memory.used` and emit
`SAM3_GPU_MEMORY_FRACTION` (plus frugal batch overrides) when a card looked shared, but that
auto-detection routinely misfired — counting the Sentinel stack's *own* resident replicas as a
co-tenant — and throttled SAM3 into spurious OOMs on dedicated cards. The generated block now
always lets inference use the whole card; see
[decisions/why-removed-auto-vram-cap.md](../decisions/why-removed-auto-vram-cap.md).

`SAM3_GPU_MEMORY_FRACTION` still exists as a **manual** escape hatch for genuine shared-GPU
hosts (e.g. a vLLM co-tenant): set it by hand (outside the generated block) to a fraction in
`(0,1)` and `inference-sam3/main.py:_apply_gpu_memory_fraction` applies it per replica via
`torch.cuda.set_per_process_memory_fraction`, so an over-budget alloc raises a catchable OOM
instead of illegal-accessing the neighbour. Default (unset / `0`) = no cap.

## Preflight failure

Preflight fails before build when a profile requires a newer host driver. E.g. Blackwell profile asks for driver 555.x+; on 535.x, `configure_host.py` reports the missing minimum and exits non-zero.

## Re-run when

- After upgrading the GPU.
- After upgrading the NVIDIA driver.

## Cross-references

- [scripts/configure-host-gpu.md](../scripts/configure-host-gpu.md)
- [decisions/disable-addmm-cuda-lt.md](../decisions/disable-addmm-cuda-lt.md)
- [offline-airgap-deployment.md](offline-airgap-deployment.md)

# Decision: pin consumer Blackwell (sm_120) to the latest cu132 / torch 2.12 build line

## Context

`scripts/gpu_profiles.py` resolves each GPU to a build profile (CUDA wheel +
torch versions) that `scripts/configure_host.py` writes into `.env`. Most
profiles share the cu130 / torch-2.10 baseline. Consumer Blackwell (RTX 50-series,
sm_120) is brand-new silicon that benefits most from the newest CUDA kernels, and
the operator asked for it to track the *latest* released line independently of the
rest of the fleet.

A separate experiment (this session) established that the cu13x **concurrent-forward
CUDA-context poison is version-independent** — it reproduced identically on
cu128 / cu130 / cu132 (torch 2.8 / 2.10 / 2.12) on A100. So moving any profile
between CUDA versions does **not** fix that bug; the fix is architectural
(`SAM3_SERIALIZE_FORWARDS`, see
[why-serialize-forwards-on-a100-cu13x.md](why-serialize-forwards-on-a100-cu13x.md)).
This decision is therefore **only about kernel currency for new silicon**, not the
poison.

## Decision

Bump only the `blackwell_sm120` profile to the latest line:

| field | before | after |
|---|---|---|
| `torch_index_url` | cu130 | **cu132** |
| `torch_version` | 2.10.0+cu130 | **2.12.0+cu132** |
| `torchvision_version` | 0.25.0+cu130 | **0.27.0+cu132** |
| `torchaudio_version` | 2.10.0+cu130 | 2.12.0+cu132 (unused — Dockerfile installs only torch+torchvision) |

Everything else is unchanged:

- `cuda_version` stays `13.2.0` — the base image (`nvidia/cuda:13.2.0-devel`) is
  already CUDA 13.2 and shared with every cu130 profile; the cu132 wheels bundle
  their own CUDA 13.2 runtime.
- `min_driver_version` stays `575.51` — CUDA 13.x minor-version compatibility runs
  13.2 wheels on the cu130 driver baseline.
- `torch_cuda_arch_list`, compute capability, and all runtime knobs are unchanged.
- **No other profile changes.** A100 (`ampere_sm80_86_datacenter`), B200
  (`blackwell_sm100`), Hopper, Ada, Turing all stay on their existing lines.
  Verified: `resolve_gpu_profile("RTX 5090")` → cu132/2.12; A100/B200/H100 → cu130/2.10.

The Dockerfile build args (`TORCH_INDEX_URL`, `TORCH_VERSION`,
`TORCHVISION_VERSION`) already read from the profile via `build_env()`, so no
Dockerfile or compose change is needed.

## Caveats

- **cu132 is PyTorch's experimental build channel** (CUDA 13.0 is the stable PyPI
  default for torch 2.12). Verify a full image build + inference pass on real
  sm_120 hardware before production — this could not be tested on the A100-only
  build host.
- torch 2.12 deprecates the cu128 wheel line; cu130/cu132 are the supported 13.x
  channels going forward.

## Consequences

- RTX 50-series hosts build against the newest kernels without affecting any other
  card's validated stack.
- The build matrix now spans cu126 (Turing/Ada), cu130 (most datacenter), and
  cu132 (consumer Blackwell) — three lines to keep in mind when bumping versions.

## Cross-references

- [scripts/configure-host-gpu.md](../scripts/configure-host-gpu.md) / [deployment/gpu-profile-detection.md](../deployment/gpu-profile-detection.md)
- [why-serialize-forwards-on-a100-cu13x.md](why-serialize-forwards-on-a100-cu13x.md) — the version-independent poison + its real fix.
- [disable-addmm-cuda-lt.md](disable-addmm-cuda-lt.md) — sibling per-arch cu13x mitigation.

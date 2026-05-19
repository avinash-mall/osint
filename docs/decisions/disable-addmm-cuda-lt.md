# `DISABLE_ADDMM_CUDA_LT=1` — Why It's the Default

## Decision

The inference container ships with env `DISABLE_ADDMM_CUDA_LT=1`. This routes `nn.Linear` / `addmm` calls **off** the cuBLAS-Lt code path and onto plain cuBLAS.

## Why

A long-running corruption bug in cuBLAS-Lt manifests on A100 (sm_80) with CUDA 13.0 (cu130): the `addmm` kernel produces silently wrong outputs in some shape×dtype combinations. The bug doesn't crash — it just returns numerically incorrect tensors, which then propagate into downstream detections.

We hit this in production. The fix is to disable cuBLAS-Lt for `addmm` specifically. Performance impact is small (a few percent) on the affected hardware, and zero on Blackwell / Hopper where cuBLAS-Lt isn't problematic.

The variable is set in `docker-compose.yml` so it applies everywhere, regardless of which GPU profile is active. Operators who want to test the cuBLAS-Lt path can `unset DISABLE_ADDMM_CUDA_LT` for that specific deployment.

## How it works

[`inference-sam3/main.py`](../../inference-sam3/main.py) reads the env var early in startup and monkey-patches `torch.addmm` to use the non-Lt backend. The patch is unconditional — there's no per-shape gate, since the corruption isn't deterministic enough to filter.

## Trade-offs accepted

- Slightly slower `nn.Linear` on affected GPUs.
- One more env var that has to stay set; if an operator copies an `.env` without it, they may hit the corruption.

## Cross-references

- [deployment/gpu-profile-detection.md](../deployment/gpu-profile-detection.md)
- [inference/main-app-entrypoint.md](../inference/main-app-entrypoint.md)

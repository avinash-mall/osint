# `DISABLE_ADDMM_CUDA_LT=1` — Why It's the Default

## Decision

Inference container ships with env `DISABLE_ADDMM_CUDA_LT=1` → routes `nn.Linear` / `addmm` calls **off** the cuBLAS-Lt code path onto plain cuBLAS.

## Why

A long-running corruption bug in cuBLAS-Lt manifests on A100 (sm_80) with CUDA 13.0 (cu130): the `addmm` kernel produces silently wrong outputs in some shape×dtype combinations. Doesn't crash — returns numerically incorrect tensors which propagate into downstream detections.

Hit this in production. Fix: disable cuBLAS-Lt for `addmm` specifically. Performance impact small (a few percent) on affected hardware, zero on Blackwell / Hopper where cuBLAS-Lt isn't problematic.

Set in `docker-compose.yml` → applies everywhere, regardless of active GPU profile. Operators wanting to test the cuBLAS-Lt path can `unset DISABLE_ADDMM_CUDA_LT` for that deployment.

## How it works

[`inference-sam3/main.py`](../../inference-sam3/main.py) reads the env var early in startup, monkey-patches `torch.addmm` to use the non-Lt backend. Patch is unconditional — no per-shape gate, since the corruption isn't deterministic enough to filter.

## Trade-offs accepted

- Slightly slower `nn.Linear` on affected GPUs.
- One more env var that must stay set; an operator copying an `.env` without it may hit the corruption.

## Cross-references

- [deployment/gpu-profile-detection.md](../deployment/gpu-profile-detection.md)
- [inference/main-app-entrypoint.md](../inference/main-app-entrypoint.md)

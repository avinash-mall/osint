# `scripts/configure_host.py` — GPU Profile Bootstrap

**Path:** [scripts/configure_host.py](../../scripts/configure_host.py)
**Source of truth for profiles:** [scripts/gpu_profiles.py](../../scripts/gpu_profiles.py)

## Purpose

Reads `nvidia-smi`, picks a matching profile, writes a `# === SENTINEL GENERATED GPU CONFIG ===` block into `.env`.

## Usage

```bash
python scripts/configure_host.py            # detect + write
python scripts/configure_host.py --dry-run  # detect + print, do not write
python scripts/configure_host.py --force    # overwrite even if block exists
```

## What it writes

Build-time + runtime variables for the GPU layer. See [deployment/gpu-profile-detection.md](../deployment/gpu-profile-detection.md) for the full list.

It also **divides the GPUs across services**: it reads the operator's
`SENTINEL_RESERVED_GPUS` (a preserved input, e.g. `0,1` for a vLLM co-tenant) and
generates `SAM3_VISIBLE_DEVICES` + `LAE_VISIBLE_DEVICES` — dedicating the last
free card to inference-lae at ≥3 free, otherwise SAM3 keeps every card and LAE
shares the last (protecting SAM3's replicas). `SAM3_SERIALIZE_FORWARDS` is
emitted only for multi-replica SAM3, and the chip-dispatch knobs track the
SAM3-allocated count. See [decisions/why-auto-gpu-division.md](../decisions/why-auto-gpu-division.md).

## What it never writes

- `HF_TOKEN`, `SESSION_SECRET`, `ADMIN_PASSWORD` — operator concerns.
- `SAM3_GPU_MEMORY_FRACTION` — manual shared-GPU escape hatch; the old
  live-memory auto cap was removed.
- Anything outside the `SENTINEL GENERATED GPU CONFIG` block — the rest of `.env` is preserved exactly.

## When to re-run

- After upgrading the GPU.
- After upgrading the NVIDIA driver.
- After copying `.env` from a different machine (the block in the source `.env` is wrong for this host).

## Cross-references

- [deployment/gpu-profile-detection.md](../deployment/gpu-profile-detection.md)
- [deployment/offline-airgap-deployment.md](../deployment/offline-airgap-deployment.md)
- [decisions/disable-addmm-cuda-lt.md](../decisions/disable-addmm-cuda-lt.md)

# Why configure_host auto-divides GPUs across services

**Status:** accepted
**Date:** 2026-06-10
**Scope:** `scripts/configure_host.py`, `docker-compose.yml` (inference-sam3)

## Decision

`scripts/configure_host.py` now **assigns the host GPUs to the inference-sam3
service** and writes `SAM3_VISIBLE_DEVICES` into the generated block (previously
`SAM3_VISIBLE_DEVICES` was a manual operator line). A new operator input,
**`SENTINEL_RESERVED_GPUS`** (preserved, never generated), carves out co-tenants
like a vLLM server.

## The assignment rule (`partition_gpus`)

`available = all_indices − SENTINEL_RESERVED_GPUS`, then:

- **≥1 free** → SAM3 gets every free card (one replica per card).
- **0 free** → emit no device keys; compose `:-all` backstops; warn.

## Why this design

inference-sam3 is the throughput-critical GPU service: it runs **one model
replica per visible GPU**, so its speed scales with card count. With no second
GPU service to carve cards out for, the rule simply hands SAM3 every free card —
the only judgement left is honouring `SENTINEL_RESERVED_GPUS` so a co-tenant
(e.g. a vLLM server) keeps its cards.

On the dev host (4× A100, GPUs 0,1 reserved for vLLM) this yields SAM3 on `2,3`
(2 replicas) — automatic and reservation-aware.

`SAM3_SERIALIZE_FORWARDS=1` is emitted only when SAM3 gets >1 replica (the
A100+cu13x cross-replica poison case); the compose default `:-1` backstops the
single-replica case so emission can never *weaken* the fix. The chip-dispatch
knobs (`INFERENCE_CHIP_CONCURRENCY`, `INFERENCE_MIN_PENDING_CHIPS`) track the
**SAM3-allocated** GPU count, not the raw host count (a prior bug).

## Migration note

`SAM3_VISIBLE_DEVICES` is now a *generated* key, so `replace_generated_block`
removes any hand-set `SAM3_VISIBLE_DEVICES` line on the next run. Operators set
`SENTINEL_RESERVED_GPUS` instead and re-run `python scripts/configure_host.py`.

## Cross-references

- [deployment/gpu-profile-detection.md](../deployment/gpu-profile-detection.md)
- [decisions/why-serialize-forwards-on-a100-cu13x.md](why-serialize-forwards-on-a100-cu13x.md)
- [decisions/why-removed-auto-vram-cap.md](why-removed-auto-vram-cap.md)

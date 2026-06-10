# Why configure_host auto-divides GPUs across services

**Status:** accepted
**Date:** 2026-06-10
**Scope:** `scripts/configure_host.py`, `docker-compose.yml` (inference-sam3, inference-lae)

## Decision

`scripts/configure_host.py` now **partitions the host GPUs across the two GPU
services** and writes `SAM3_VISIBLE_DEVICES` + `LAE_VISIBLE_DEVICES` into the
generated block (previously `SAM3_VISIBLE_DEVICES` was a manual operator line and
`LAE_VISIBLE_DEVICES` defaulted to "share SAM3's cards"). A new operator input,
**`SENTINEL_RESERVED_GPUS`** (preserved, never generated), carves out co-tenants
like a vLLM server.

## The partition rule (`partition_gpus`)

`available = all_indices − SENTINEL_RESERVED_GPUS`, then:

- **≥3 free** → dedicate the *last* card to inference-lae; SAM3 gets the rest
  (still ≥2 replicas). LAE is fully isolated — no VRAM contention.
- **==2 free** → SAM3 keeps **both** cards (2 replicas); inference-lae **shares**
  the last card.
- **==1 free** → both share the one card.
- **0 free** → emit no device keys; compose `:-all` backstops; warn.

## Why this design

inference-sam3 is the throughput-critical service: it runs **one model replica
per visible GPU**, so its speed scales with card count. inference-lae is tiny
(~2-4 GB, single-GPU). The naïve "always give LAE its own card" rule *halves*
SAM3 on a 2-free-GPU host — empirically that cost (one SAM3 replica) is larger
than the contention sharing causes. So the rule **protects SAM3's replicas** and
only carves out a dedicated LAE card when ≥3 GPUs are free (SAM3 still keeps ≥2).

On the dev host (4× A100, GPUs 0,1 reserved for vLLM) this yields SAM3 on `2,3`
(2 replicas) + LAE sharing `3` — i.e. the prior hand-tuned layout, now automatic
and reservation-aware.

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
- [decisions/why-lae-dino-replaces-grounding-dino.md](why-lae-dino-replaces-grounding-dino.md)
- [decisions/why-removed-auto-vram-cap.md](why-removed-auto-vram-cap.md)

# Removed the auto-derived per-process VRAM cap from `configure_host.py`

## Decision

**Removed:** the "co-tenant VRAM ceiling" block in `scripts/configure_host.py` (and its
`COTENANT_*` constants) that read `memory.used` at configure time and, when a card looked busy,
emitted `SAM3_GPU_MEMORY_FRACTION` plus the frugal `SAM3_EMBED_BATCH_SIZE→16` /
`SAM3_BATCHED_TEXT_CHUNK_SIZE→8` overrides into the generated `.env` block.

**Kept:** the runtime honor of `SAM3_GPU_MEMORY_FRACTION` in
`inference-sam3/main.py:_apply_gpu_memory_fraction`. It is now a **manual-only** escape hatch —
dormant by default (unset / `0` = no cap), set by hand on a genuine shared-GPU host. Also kept:
the per-replica GPU-forward serialization and current-device pinning from the same incident
(`069590b1`, `f20b38a8`) — those are correctness fixes for concurrent-cuBLAS illegal access, not
capacity throttles.

## Why

- **The auto-detection misfired in the common case.** It could not distinguish a real
  neighbour (a vLLM co-tenant) from the Sentinel stack's *own* resident inference replicas. If
  `configure_host.py` ran while the stack was up — the obvious thing to do — it counted SAM3's
  own ~6 GiB as a "co-tenant" and emitted `SAM3_GPU_MEMORY_FRACTION≈0.39` on a dedicated 16 GiB
  card. SAM3 then OOM'd against a ~6 GiB ceiling while ~8 GiB sat free, failing **every** chip of
  a full Sentinel-2 tile. The mitigation's own docs warned "run with the stack down," but a
  config step that silently bricks inference when run the natural way is a footgun, not a guard.
- **The failure mode it caused (spurious OOM, zero detections) was worse than the one it
  prevented.** The cap existed to turn a co-tenant collision into a *clean* OOM instead of an
  illegal memory access. On dedicated cards — the overwhelmingly common deployment — there is no
  neighbour to collide with, so the cap bought nothing and cost the whole detection pipeline.
- **The real protection for shared GPUs is still available**, just not automatic. An operator who
  knowingly shares a card sets `SAM3_GPU_MEMORY_FRACTION` by hand; the runtime path is unchanged.

## Resulting behaviour

- `configure_host.py` never writes `SAM3_GPU_MEMORY_FRACTION` or the frugal batch overrides.
  Verified: running it with the GPU at ~10 GiB used (stack up) now emits **no** cap and keeps
  `SAM3_EMBED_BATCH_SIZE=32` (previously this exact condition produced `0.39`).
- Dedicated cards run SAM3 against the whole card — the default and intended state.
- Shared-GPU hosts opt in manually via `SAM3_GPU_MEMORY_FRACTION`.

## Cross-references

- [deployment/gpu-profile-detection.md](../deployment/gpu-profile-detection.md) — "Per-process VRAM ceiling (manual only)"
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md) — `SAM3_GPU_MEMORY_FRACTION`
- [decisions/optical-inference-throughput.md](optical-inference-throughput.md) — original incident; the device-pin + per-replica lock parts remain in force

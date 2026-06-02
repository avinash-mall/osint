# Decision: serialize GPU forwards process-wide on A100 / CUDA 13.x

## Context

On the A100 80GB (sm_80) + CUDA 13.x build, two GPU forwards running
concurrently **in the same inference process** intermittently raise
`cudaErrorIllegalAddress` ("an illegal memory access was encountered"), which
poisons the whole process CUDA context — every subsequent kernel launch then
fails identically until the process restarts. This is the same class of
bundled-`sam3` / cu13x kernel race documented in
[why-exit-on-poisoned-cuda-context.md](why-exit-on-poisoned-cuda-context.md)
and [disable-addmm-cuda-lt.md](disable-addmm-cuda-lt.md); it cannot be fixed
from this repo.

The existing per-replica forward lock (`bundle["lock"]`, see
[optical-inference-throughput.md](optical-inference-throughput.md)) only
serializes two forwards on the **same** GPU. Reproduced on this host:

- **3 sequential** `/detect_raw` requests → clean.
- **8 concurrent** `/detect_raw` requests → instant illegal-access → self-heal
  restart. The 8 requests round-robin across the 2 replicas, so two forwards on
  **different** GPUs of the one process run at once — the per-replica lock does
  not cover that cross-replica case.
- A long imagery job overlapping an FMV clip → **image `/detect_raw` forward
  concurrent with a video `/detect_video` forward** → same poison. (Image×image
  and image×video both poison; **video×video does not** — FMV alone on both
  GPUs runs clean.)

Before this change the worker masked the first case (it dropped the rest of the
scene as zero-detection chips and reported false success) and the image×video
case produced a self-heal restart loop once the worker learned to retry across
restarts.

## Decision

Add `SAM3_SERIALIZE_FORWARDS` (default **on**, wired in `docker-compose.yml`
like `DISABLE_ADDMM_CUDA_LT`). When on, **at most one GPU forward runs at a time
across the whole process**, enforced in three layers:

1. **`forward_lock` (threading), shared across replicas.** Every image forward
   stack in `sam3_runner.py` (`run_text_prompts`, `_run_text_prompts_batched`,
   `_run_text_prompts_cached_batched`, `run_box_prompts`) and the specialist /
   embedding `_locked` wrapper in `main.py` acquire `bundle["forward_lock"]`,
   which is the one shared `_global_forward_lock` when serialization is on (a
   fresh per-replica lock when off). Stops image forwards on different replicas
   running at once.
2. **`_detect_serial_lock` (asyncio), per request.** `_detect_pipeline_guarded`
   holds it across the whole `/detect(_raw)` pipeline so request B cannot start
   until request A has fully returned — closing the async-tail gap where A's
   forward releases the threading lock while its GPU work is still draining and
   B launches on the other replica. It yields the event loop, so `/health`
   stays responsive while detect requests queue.
3. **The video stream holds `_global_forward_lock` for its window.** Each
   `/detect_video` `stream()` acquires the global lock for the duration of the
   window so an image forward cannot run concurrently with a video forward
   (the image×video case). `bundle["lock"]` stays per-replica (it gates one
   video session per GPU, held for the whole stream — see
   [main-app-entrypoint.md](../inference/main-app-entrypoint.md)).

`/health` reports `serialize_forwards` so operators can confirm the mitigation
is active.

## Trade-offs accepted

- **Imagery loses cross-GPU parallelism** (one forward at a time); a large
  multi-pass scene takes proportionally longer. Correctness over throughput on
  affected hardware.
- **FMV loses intra-process video parallelism** when serialization is on (the
  global lock serializes video windows too, even though video×video is safe).
  Acceptable because the flag is off on hardware without the bug.
- Set `SAM3_SERIALIZE_FORWARDS=0` on Hopper/Blackwell (or any host where the
  concurrent-forward race does not reproduce) to regain full parallelism.

## Alternatives considered

- **Per-replica lock only (status quo).** Rejected: does not cover cross-replica
  or image×video concurrency — the actual triggers here.
- **Reject FMV `/load` while an imagery job is active.** Rejected: `/load` only
  sees per-request `_active_requests`, which dips to 0 in the gaps between
  serialized imagery chips/passes, so a profile swap still slips in. Serializing
  the forwards themselves is gap-free.
- **Process-per-GPU.** Rejected: large architectural change; a single shared
  lock achieves correctness now.

## Consequences

- The concurrent-forward poison is eliminated when the flag is on (verified: a
  40-request concurrent `/detect_raw` burst and a full imagery+FMV run complete
  with zero restarts). Any residual poison still self-heals via
  [why-exit-on-poisoned-cuda-context.md](why-exit-on-poisoned-cuda-context.md)
  and is ridden out by the worker's chip retry
  ([why-retry-chips-across-inference-restart.md](why-retry-chips-across-inference-restart.md)).

## Cross-references

- [why-exit-on-poisoned-cuda-context.md](why-exit-on-poisoned-cuda-context.md) — the self-heal backstop, now covering every `/detect` path.
- [why-retry-chips-across-inference-restart.md](why-retry-chips-across-inference-restart.md) — worker rides out a restart instead of dropping chips.
- [optical-inference-throughput.md](optical-inference-throughput.md) — the original per-replica forward lock.
- [disable-addmm-cuda-lt.md](disable-addmm-cuda-lt.md) — sibling A100/cu13x mitigation.
- [inference/main-app-entrypoint.md](../inference/main-app-entrypoint.md)
- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md)

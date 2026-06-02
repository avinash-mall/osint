# Decision: exit the process on a poisoned CUDA context in the text-prompt path

## Context

`_run_text_prompts_cached_batched` wraps each text chunk's forward in a
`try/except` and, on failure, logs a warning and `continue`s to the next chunk
(only raising if *every* chunk fails). That graceful-skip was designed for
**recoverable** faults — most importantly a per-tile GPU OOM, where the cuBLAS
handle stays valid and skipping one content-heavy chunk lets the tile still
return the detections from the chunks that fit.

Production logs showed a different failure that the skip handled **wrongly**:

```
WARNING:sam3_runner:sam3 cached-batched chunk failed (offset=0, labels=[...]): CUDA error: an illegal memory access was encountered
WARNING:sam3_runner:sam3 cached-batched chunk failed (offset=16, labels=[...]): CUDA error: an illegal memory access was encountered
INFO:     POST /detect_raw HTTP/1.1 500 Internal Server Error
... repeats on every subsequent request, indefinitely ...
```

A `cudaErrorIllegalAddress` (and device-side asserts, cuBLAS/cuDNN init
failures) is **unrecoverable in-process**: it sticks to the process's CUDA
context, so every subsequent kernel launch — the next chunk, then the next
request's `encode_image` — fails identically. The container became a zombie:
`/health` keeps returning `model_loaded: true` (it never touches the GPU), so
Docker's healthcheck stays green and `restart: unless-stopped` never fires,
while every `/detect_raw` returns 500. The platform is air-gapped and run by
analysts; a silent indefinite outage is the worst outcome.

The likely trigger is two concurrent forwards racing the same replica's
default-stream cuBLAS workspace (the exact race the per-replica forward lock in
`main.py` already guards against — see
[optical-inference-throughput.md](optical-inference-throughput.md)); the root
race lives inside the bundled `sam3` CUDA kernels and cannot be fixed from this
repo. This decision is about *recovering* from it reliably, not preventing the
underlying kernel fault.

## Decision

Classify the chunk exception. On an **unrecoverable** CUDA fault, stop trying to
degrade gracefully and `os._exit(1)` so the `restart: unless-stopped` policy
respawns the container with a clean context — converting an indefinite zombie
into a ~100 s restart. OOM and any non-CUDA error keep the existing per-chunk
skip / raise-if-all-fail behaviour.

```python
def _cuda_context_poisoned(exc: Exception) -> bool:
    if not isinstance(exc, RuntimeError):       # torch.AcceleratorError subclasses RuntimeError
        return False
    text = str(exc)
    if "CUDA out of memory" in text or "out of memory" in text.lower():
        return False                            # recoverable → keep graceful skip
    return ("CUDA error" in text or "illegal memory access" in text
            or "device-side assert" in text or "CUBLAS_STATUS" in text
            or "cuDNN error" in text)
```

This mirrors the **existing** self-heal already used for the multiplex-video
predictor warmup (`sam3_runner.py#L458-L500`), which `os._exit(1)`s on the same
class of cuBLAS-state corruption. We are extending an established pattern to the
text-detection path, not inventing a new one.

## Alternatives considered

- **Make `/health` probe the GPU** (run a tiny tensor op) so the healthcheck
  goes red. Rejected as insufficient on its own: a red healthcheck does **not**
  restart a container under `restart: unless-stopped` — only a process exit (or
  an external autoheal sidecar, which we don't ship) does. Process exit is the
  reliable trigger.
- **Reset the CUDA context in-process** (`torch.cuda` teardown / re-init).
  Rejected: PyTorch has no supported API to rebuild a context corrupted by an
  illegal memory access; the codebase already documents this (cuBLAS-Lt /
  multiplex path).
- **Keep skipping chunks.** Rejected: every subsequent op fails, so this just
  serves 500s forever while reporting healthy — the bug we observed.

## Consequences

- A poisoned context now self-heals: one failed request, then a clean restart,
  instead of an indefinite 500 storm.
- An in-flight request at exit time gets a dropped connection (already failing).
- A genuinely *persistent* trigger would cause a restart loop, but that is
  visible (container `Restarting`) and actionable, unlike a green-but-broken
  zombie. No silent masking either way.

## Cross-references

- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md) — cached-encoder fast path, failure modes.
- [optical-inference-throughput.md](optical-inference-throughput.md) — per-replica forward lock that guards the default-stream cuBLAS race.
- [cached-forward-device-normalise.md](cached-forward-device-normalise.md) — the multi-GPU device-pin fix for the original `_get_img_feats` illegal-access cascade.

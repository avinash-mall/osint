# /detect returns 503 (not a 500 crash) when the needed model isn't resident

**Path:** [inference-sam3/main.py](../../inference-sam3/main.py) `/detect`,
[inference-sam3/sam3_runner.py](../../inference-sam3/sam3_runner.py) `run_text_prompts` / `run_box_prompts`
**Lines:** N/A (decision record for the linked change)
**Depends on:** the single-pool profile model in [profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md)

## Decision

`/detect` calls `_ensure_profile("imagery")` then `_next_bundle()`, and now asserts the
selected bundle actually carries `sam3_image` before running prompts:

```python
if bundle.get("sam3_image") is None:
    raise HTTPException(503, f"sam3_image not resident (profile={_current_profile}); retry")
```

The same one-line guard is mirrored at the top of `run_text_prompts` and
`run_box_prompts` (raising `RuntimeError`) so no caller can dereference a `None`
component bundle.

## Why this design

The GPU runs a single profile pool. An imagery `/detect` auto-heals to the imagery
profile, but between `_ensure_profile()` and using the bundle, a concurrent FMV
request can swap the pool to `fmv` (which has no `sam3_image`). The old code then ran
`bundle["sam3_image"]["model"]` and crashed with `TypeError: 'NoneType' object is not
subscriptable` — surfacing as an opaque HTTP 500 and a stack trace in the logs, while
the analyst's detection silently failed.

A 503 is the honest answer: the service is momentarily not holding the model this
request needs. The worker's bounded `ThreadPoolExecutor` already treats 503 from the
inference service as retryable backpressure, so the request simply retries once the
swap settles. The companion change [why-revert-inference-after-fmv.md](why-revert-inference-after-fmv.md)
makes the swap rare in the first place.

## Considered alternatives

- **Hold `_load_lock` across the whole detect.** Rejected: inference runs for hundreds
  of ms under the bundle lock; serialising every detect behind the load lock would
  destroy throughput, and the unload could still free the held bundle's tensors.
- **Keep `sam3_image` resident across all profiles.** Rejected here: that's a VRAM
  budgeting change (the "all" profile already does this on big GPUs); the guard is the
  cheap correctness fix that works on every GPU profile.

## Cross-references

- [why-revert-inference-after-fmv.md](why-revert-inference-after-fmv.md)
- [inference/main-app-entrypoint.md](../inference/main-app-entrypoint.md)
- [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md)

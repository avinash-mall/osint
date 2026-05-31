# Worker reverts the inference profile to `imagery` after FMV processing

**Path:** [backend/worker_legacy.py](../../backend/worker_legacy.py) `process_fmv` /
`_revert_inference_profile`
**Lines:** ~25 added
**Depends on:** the single-pool profile model; `_ensure_fmv_profile`

## Decision

`process_fmv` loads the `fmv` profile via `_ensure_fmv_profile`, and now reverts the
service to `imagery` in its `finally` block:

```python
finally:
    _revert_inference_profile(session, "imagery")  # best-effort
    session.close()
```

`_revert_inference_profile` mirrors `_ensure_fmv_profile`'s 409-tolerant retry but is
best-effort and bounded (~30 s) and never raises: a 409 means another FMV session is
still in flight and correctly keeps `fmv`, so it gives up quietly.

## Why this design

The COP's resting state is the `imagery` profile (sam3_image + DOTA-OBB + etc.). An FMV
task switches the single GPU pool to `fmv` (sam3_video) and the old code left it there
indefinitely, so after anyone processed a drone clip the map's imagery detection
degraded — every imagery `/detect` had to pay a full profile reload, and concurrently
could even race into the unloaded-component crash
([why-503-on-unloaded-component.md](why-503-on-unloaded-component.md)). Reverting at the
end of the FMV task returns the resting state to `imagery` so the COP keeps working,
while the 409-tolerance ensures back-to-back FMV jobs don't thrash the profile.

## Considered alternatives

- **Revert eagerly between FMV windows.** Rejected: pointless churn — the task still
  needs `fmv` for its next window; revert only once, at the end.
- **Track a "previous profile" and restore exactly it.** Rejected: `imagery` is the
  canonical resting profile; restoring an arbitrary prior state adds state for no gain.
- **Never revert; make imagery detect tolerate `fmv`.** Rejected: that's the guard in
  the companion decision, which handles the *race*; the resting state should still be
  `imagery` so steady-state imagery detection isn't paying reload latency.

## Cross-references

- [why-503-on-unloaded-component.md](why-503-on-unloaded-component.md)
- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)

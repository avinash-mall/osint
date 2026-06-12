# Removed: empty `inference-sam3/detection_policy.py`

**Date:** 2026-06-08 (corrected 2026-06-09, 2026-06-12)
**Status:** adopted — with corrections (see below)

## Correction (2026-06-12)

The "loads the **backend** module by path" claim below was wrong **in
production**: `BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"`
is `/backend` inside the container — a path that never exists, because `/app`
*is* the `inference-sam3/` mount — so the container always took the
`try/except` fallback and used the naive slugifier. The compose file-mount
`./backend/detection_policy.py:/app/detection_policy.py:ro` was never read by
`fusion.py` at all.

`fusion.py` now tries an ordered candidate list
([fusion.py#L65-L97](../../inference-sam3/fusion.py#L65-L97)):

1. `Path(__file__).resolve().parent / "detection_policy.py"` — resolves to
   `/app/detection_policy.py` inside the container (the compose file-mount,
   i.e. the real backend module).
2. `BACKEND_DIR / "detection_policy.py"` — the dev-host checkout's
   `backend/detection_policy.py`.

A candidate is accepted only if it is non-empty (`stat().st_size > 0`) and
defines a callable `parent_class_for_label` — so on the dev host, where
candidate 1 is the 0-byte root-owned bind-mount anchor described in the 2026-06-09
correction, the loader skips it and falls through to the real backend module.
Only when no candidate qualifies does the naive slugifier fallback apply (with
a warning). Regression-tested in
[tests/test_audit_fixes.py](../../inference-sam3/tests/test_audit_fixes.py).
See [audit-fixes-inference-2026-06-12.md](audit-fixes-inference-2026-06-12.md).

## Correction (2026-06-09)

This file is **not** truly removable: it is the host-side **docker bind-mount
point** for the compose line `./backend/detection_policy.py:/app/detection_policy.py:ro`.
Because `/app` is itself the `inference-sam3/` bind mount, docker re-creates
`inference-sam3/detection_policy.py` (root-owned, 0-byte) on every
`inference-sam3` container start to anchor that file mount. So deleting it from
git is fine (it carries no source), but the 0-byte file reappears whenever the
container runs. It is now **gitignored** so it no longer shows as an untracked,
root-owned artifact. The real module remains `backend/detection_policy.py`.

## Decision

Stop tracking `inference-sam3/detection_policy.py` in git. It is a 0-byte file
(empty across its entire git history) imported by nothing — its only role is as
the docker mount point described above.

The inference service's only consumer of detection-policy logic — `fusion.py`'s
`parent_class_for_label` — loads the **backend** module by path:

```python
spec = importlib.util.spec_from_file_location(
    "backend_detection_policy", BACKEND_DIR / "detection_policy.py")
```

where `BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"`
([fusion.py#L18](../../inference-sam3/fusion.py#L18), [#L57](../../inference-sam3/fusion.py#L57)).
That points at [backend/detection_policy.py](../../backend/detection_policy.py)
(the real 430-line module), with a `try/except` that falls back to a local
slugifier if the backend tree is absent. The empty sibling file in
`inference-sam3/` never participated in either path.

## What this touched

- Deleted `inference-sam3/detection_policy.py` (empty).
- No imports, Dockerfile copies, or docs referenced it — nothing else changed.

## Cross-references

- [backend/detection-policy.md](../backend/detection-policy.md) — the real policy module.
- [inference/fusion-and-nms.md](../inference/fusion-and-nms.md) — the fusion consumer.

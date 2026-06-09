# Removed: empty `inference-sam3/detection_policy.py`

**Date:** 2026-06-08 (corrected 2026-06-09)
**Status:** adopted — with a correction (see "Correction" below)

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

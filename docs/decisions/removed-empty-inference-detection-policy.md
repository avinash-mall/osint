# Removed: empty `inference-sam3/detection_policy.py`

**Date:** 2026-06-08
**Status:** adopted

## Decision

Delete `inference-sam3/detection_policy.py`. It was a 0-byte file, empty across
its entire git history, and referenced by nothing.

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

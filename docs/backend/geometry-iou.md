# `backend/geometry.py` — Bbox/IoU/Point Helpers

**Path:** [backend/geometry.py](../../backend/geometry.py)
**Lines:** ~95
**Depends on:** Pure Python.

## Purpose

Reusable geometry primitives used by multiple routers. Both xyxy-absolute and cxcywh-normalized bbox formats supported — the codebase uses both depending on data path.

## Key symbols

- [`parse_bbox`](../../backend/geometry.py#L20) — `"minlon,minlat,maxlon,maxlat"` → tuple. HTTP 400 on malformed input.
- [`iou_xyxy`](../../backend/geometry.py#L34) — absolute coords, two boxes → IoU float.
- [`iou_cxcywh`](../../backend/geometry.py#L53) — normalized cxcywh, two boxes → IoU float.
- [`point_payload`](../../backend/geometry.py#L73) — pulls `(lat, lon)` from heterogeneous payload shapes; `(None, None)` if absent.
- [`make_square_feature`](../../backend/geometry.py#L85) — square GeoJSON Feature centered on a point; used by analytics endpoints.

## Cross-references

- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md) — uses `parse_bbox`
- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md) — uses `make_square_feature`
- [backend/fmv-track-consolidation.md](fmv-track-consolidation.md) — uses IoU helpers

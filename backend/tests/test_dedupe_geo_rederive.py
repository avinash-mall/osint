"""Geometry refresh after dedupe-time pixel_bbox mutation.

WBF fusion and edge-truncated reconciliation rewrite ``pixel_bbox`` in place;
``_rederive_geo_from_pixel_bbox`` must bring ``pixel_obb`` / ``geo_polygon`` /
``geo_bbox`` back in line so the persisted geom matches the merged box.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rasterio.crs import CRS
from rasterio.transform import from_origin

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from worker_legacy import (
    _DetectionDedupeIndex,
    _WeightedBoxFusionIndex,
    _geo_stale_after_merge,
    _rederive_geo_from_pixel_bbox,
)

# pixel (x, y) -> lon = 10 + 0.001 * x, lat = 50 - 0.001 * y
TRANSFORM = from_origin(10.0, 50.0, 0.001, 0.001)
CRS_4326 = CRS.from_epsg(4326)


def _det(bbox, **extra):
    return {
        "class": "ship",
        "parent_class": "ship",
        "confidence": 0.8,
        "pixel_bbox": list(bbox),
        "pixel_obb": [bbox[0], bbox[1], bbox[2], bbox[1], bbox[2], bbox[3], bbox[0], bbox[3]],
        "geo_bbox": [0.0, 0.0, 0.0, 0.0],  # deliberately stale
        "geo_polygon": [0.0] * 8,
        **extra,
    }


def _expected_geo_bbox(bbox):
    lon1, lat1 = TRANSFORM * (bbox[0], bbox[1])
    lon2, lat2 = TRANSFORM * (bbox[2], bbox[3])
    return [min(lon1, lon2), min(lat1, lat2), max(lon1, lon2), max(lat1, lat2)]


def test_wbf_fused_head_geo_rederived_to_fused_bbox():
    idx = _WeightedBoxFusionIndex(iou_threshold=0.2)
    idx.add([_det([0, 0, 10, 10])])
    idx.add([_det([2, 2, 12, 12])])
    head = idx.heads()[0]
    assert head["wbf_member_count"] == 2
    assert _geo_stale_after_merge(head)
    fused_bbox = list(head["pixel_bbox"])
    assert fused_bbox != [0, 0, 10, 10]  # fusion moved the box

    _rederive_geo_from_pixel_bbox(head, TRANSFORM, CRS_4326)

    assert head["geo_bbox"] == _expected_geo_bbox(fused_bbox)
    assert head["pixel_obb"] == [
        fused_bbox[0], fused_bbox[1], fused_bbox[2], fused_bbox[1],
        fused_bbox[2], fused_bbox[3], fused_bbox[0], fused_bbox[3],
    ]
    pts = list(zip(head["geo_polygon"][0::2], head["geo_polygon"][1::2]))
    assert len(pts) == 4
    assert min(p[0] for p in pts) == head["geo_bbox"][0]
    assert max(p[1] for p in pts) == head["geo_bbox"][3]


def test_single_member_wbf_head_not_flagged_stale():
    idx = _WeightedBoxFusionIndex(iou_threshold=0.2)
    idx.add([_det([0, 0, 10, 10])])
    assert not _geo_stale_after_merge(idx.heads()[0])


def test_edge_reconciled_union_geo_rederived():
    a = _det([0, 0, 10, 10], edge_truncated=True, confidence=0.9)
    b = _det([10, 0, 20, 10], edge_truncated=True, confidence=0.5)
    idx = _DetectionDedupeIndex()
    reconciled, merges = idx.reconcile_edge_truncated([a, b])
    assert merges == 1
    assert len(reconciled) == 1
    winner = reconciled[0]
    assert winner["dedupe_method"] == "edge_reconciled"
    assert winner["pixel_bbox"] == [0, 0, 20, 10]
    assert _geo_stale_after_merge(winner)

    _rederive_geo_from_pixel_bbox(winner, TRANSFORM, CRS_4326)

    assert winner["geo_bbox"] == _expected_geo_bbox([0, 0, 20, 10])

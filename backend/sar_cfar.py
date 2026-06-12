"""Phase 5.20b — CFAR (Constant False Alarm Rate) ship detector for SAR.

A self-contained, optical-prior-free point-target detector for Sentinel-1
GRD (or any 2-band VV/VH SAR raster). The companion to Phase 5.20's
"don't run SAM3 on SAR pseudo-RGB by default" — CFAR fills the resulting
gap with a SAR-native detector whose entire decision is based on local
clutter statistics, not learned optical features.

The algorithm: two-parameter CA-CFAR (cell-averaging) on a dB-scaled
backscatter image. For each candidate pixel, compare its intensity against
the local clutter mean + N-σ where the local window excludes a small
guard band immediately around the pixel. Pixels that pass become point
detections; contiguous detected pixels are merged into bounding boxes.

This is **inference-time only** (matches the plan's "no fine-tuning"
constraint) and runs on CPU via numpy / scipy. No GPU dependency, no model
weights to ship. It is **not** a replacement for a learned SAR detector
on complex maritime scenes — but it's the right baseline for the moment
we already disabled SAM3 on SAR.

Public surface::

    from sar_cfar import detect_ships_cfar
    detections = detect_ships_cfar(
        vv_db,             # 2-D float32 array in dB
        vh_db=None,        # optional cross-pol
        threshold_sigma=2.5,
        guard_px=4,
        background_px=20,
        min_pixels=4,
    )
    # detections: list of dicts {"bbox_xyxy": [x1,y1,x2,y2],
    #                            "score": float, "dB_peak": float,
    #                            "class": "ship", "method": "cfar"}
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def _box_kernel_mean(arr: np.ndarray, window: int) -> np.ndarray:
    """Mean of a square ``window``-px box around every pixel.

    Uses a summed-area table (integral image) with a sentinel zero row/column
    so indexing is clean and edge-safe. O(N) total work, numpy-only — runs
    on a CPU worker container without scipy / skimage.
    """
    if window <= 1:
        return arr.astype(np.float32, copy=False)
    pad = window // 2
    padded = np.pad(arr.astype(np.float32, copy=False), pad, mode="reflect")
    big_h, big_w = padded.shape
    # SAT with leading zero row + column → sat[r+1, c+1] = sum of padded[:r+1, :c+1].
    sat = np.zeros((big_h + 1, big_w + 1), dtype=np.float32)
    sat[1:, 1:] = padded
    sat = sat.cumsum(axis=0).cumsum(axis=1)
    h, w = arr.shape
    # For each original pixel (y, x), the window covers padded[y:y+window, x:x+window].
    # In SAT coords that's sat[y+window, x+window] - sat[y, x+window]
    #                       - sat[y+window, x] + sat[y, x].
    s_br = sat[window : window + h, window : window + w]
    s_tr = sat[: h,                   window : window + w]
    s_bl = sat[window : window + h, : w]
    s_tl = sat[: h,                   : w]
    summed = s_br - s_tr - s_bl + s_tl
    return summed / (window * window)


def _bbox_components(mask: np.ndarray, min_pixels: int) -> list[tuple[int, int, int, int, int]]:
    """Connected-component bounding boxes on a binary mask.

    Uses a simple two-pass scan (no scipy/skimage dependency). Returns a
    list of ``(x1, y1, x2, y2, pixel_count)``. Components smaller than
    ``min_pixels`` are dropped — single-pixel returns are speckle.
    """
    h, w = mask.shape
    labels = np.zeros(mask.shape, dtype=np.int32)
    counts: dict[int, int] = {}
    bboxes: dict[int, list[int]] = {}
    next_label = 1

    # Two-pass: assign labels via union-find then collapse.
    parent: dict[int, int] = {}

    def _find(x: int) -> int:
        root = x
        while parent.get(root, root) != root:
            root = parent[root]
        while parent.get(x, x) != root:
            nxt = parent[x]
            parent[x] = root
            x = nxt
        return root

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # Pass 1: provisional labels with 4-connectivity.
    for y in range(h):
        for x in range(w):
            if not mask[y, x]:
                continue
            up = labels[y - 1, x] if y > 0 else 0
            left = labels[y, x - 1] if x > 0 else 0
            if up == 0 and left == 0:
                labels[y, x] = next_label
                parent[next_label] = next_label
                next_label += 1
            elif up != 0 and left == 0:
                labels[y, x] = up
            elif up == 0 and left != 0:
                labels[y, x] = left
            else:
                labels[y, x] = min(up, left)
                _union(up, left)

    # Pass 2: collapse + aggregate.
    for y in range(h):
        for x in range(w):
            lbl = labels[y, x]
            if lbl == 0:
                continue
            root = _find(lbl)
            counts[root] = counts.get(root, 0) + 1
            bb = bboxes.get(root)
            if bb is None:
                bboxes[root] = [x, y, x, y]
            else:
                if x < bb[0]: bb[0] = x
                if y < bb[1]: bb[1] = y
                if x > bb[2]: bb[2] = x
                if y > bb[3]: bb[3] = y

    out: list[tuple[int, int, int, int, int]] = []
    for root, bb in bboxes.items():
        if counts[root] < min_pixels:
            continue
        out.append((bb[0], bb[1], bb[2] + 1, bb[3] + 1, counts[root]))
    return out


def detect_ships_cfar(
    vv_db: np.ndarray,
    vh_db: Optional[np.ndarray] = None,
    *,
    threshold_sigma: float = 2.5,
    guard_px: int = 4,
    background_px: int = 20,
    min_pixels: int = 4,
    max_detections: int = 2000,
) -> list[dict]:
    """Run a two-parameter CA-CFAR detector over a dB-scaled SAR image.

    Args:
        vv_db: 2-D float array, dB scale (typical S1 GRD VV: -25..-5 dB
            over water, +5..+15 dB over metal ships). Bigger values =
            stronger backscatter.
        vh_db: optional cross-pol band. When provided, a pixel must pass
            CFAR on **both** bands — sharply reduces false positives from
            land-edge / sea-state speckle that's bright only in VV.
        threshold_sigma: detection threshold in σ above local mean
            (default 2.5σ ≈ Pfa ~0.006 under Gaussian-clutter assumption).
        guard_px: half-side of the guard band (excluded from background
            estimation) around each candidate pixel.
        background_px: half-side of the background window used to estimate
            local clutter μ + σ.
        min_pixels: minimum connected-component size in pixels.
        max_detections: safety cap to prevent runaway speckle storms.

    Returns:
        List of detection dicts shaped like the rest of Sentinel's pipeline::

            {
                "class": "ship",
                "parent_class": "vessel",
                "source_layer": "sar_cfar",
                "method": "cfar",
                "bbox": [cx, cy, w, h]   (normalised cxcywh, like SAM3),
                "pixel_bbox": [x1, y1, x2, y2],
                "confidence": float,    (normalised CFAR Z-score)
                "dB_peak": float,
                "modality": "sar",
            }
    """
    vv = np.ascontiguousarray(vv_db, dtype=np.float32)
    if vv.ndim != 2:
        raise ValueError("vv_db must be a 2-D array")
    h, w = vv.shape
    if h < 2 * background_px or w < 2 * background_px:
        logger.warning("sar_cfar: image %dx%d too small for window=%d", h, w, background_px)
        return []
    bg_window = 2 * background_px + 1
    gd_window = 2 * guard_px + 1
    # Local background estimates — μ and σ — excluding the guard band.
    mu_bg = _box_kernel_mean(vv, bg_window)
    mu_gd = _box_kernel_mean(vv, gd_window)
    # Subtract the small guard window from the larger background window
    # in proportional terms to approximate "background outside guard".
    gd_weight = (gd_window ** 2) / (bg_window ** 2)
    mu_clutter = (mu_bg - gd_weight * mu_gd) / max(1e-6, 1.0 - gd_weight)
    # Variance must be taken over the SAME guard-excluded background as the
    # mean. Computing E[x^2] over the full window and subtracting mu_bg^2 used
    # the guard-INCLUSIVE second moment + mean while the Z-score uses the
    # guard-EXCLUDED mu_clutter — mixing populations inflated sigma next to
    # bright targets (the guard region's energy leaked into the clutter sigma),
    # depressing the Z-score exactly where detections live. Subtract the guard
    # window's second moment in the same proportional way as the mean.
    e2_bg = _box_kernel_mean(vv ** 2, bg_window)
    e2_gd = _box_kernel_mean(vv ** 2, gd_window)
    e2_clutter = (e2_bg - gd_weight * e2_gd) / max(1e-6, 1.0 - gd_weight)
    var_clutter = e2_clutter - mu_clutter ** 2
    sigma_clutter = np.sqrt(np.maximum(0.01, var_clutter))
    # Z-score of every pixel vs its local clutter.
    z_vv = (vv - mu_clutter) / sigma_clutter
    detection_mask = z_vv > threshold_sigma
    # Optional cross-pol consistency check.
    if vh_db is not None:
        vh = np.ascontiguousarray(vh_db, dtype=np.float32)
        if vh.shape != vv.shape:
            raise ValueError("vh_db shape must match vv_db")
        # Same proportional guard subtraction as the VV path above — the
        # guard-inclusive statistics depressed z on the target.
        mu_vh_bg = _box_kernel_mean(vh, bg_window)
        mu_vh_gd = _box_kernel_mean(vh, gd_window)
        mu_vh = (mu_vh_bg - gd_weight * mu_vh_gd) / max(1e-6, 1.0 - gd_weight)
        e2_vh_bg = _box_kernel_mean(vh ** 2, bg_window)
        e2_vh_gd = _box_kernel_mean(vh ** 2, gd_window)
        e2_vh = (e2_vh_bg - gd_weight * e2_vh_gd) / max(1e-6, 1.0 - gd_weight)
        var_vh = e2_vh - mu_vh ** 2
        sigma_vh = np.sqrt(np.maximum(0.01, var_vh))
        z_vh = (vh - mu_vh) / sigma_vh
        detection_mask &= z_vh > (threshold_sigma * 0.6)  # cross-pol weaker

    # Connected-component pruning + bbox extraction.
    components = _bbox_components(detection_mask, min_pixels=min_pixels)
    components.sort(key=lambda c: c[4], reverse=True)
    components = components[:max_detections]

    detections: list[dict] = []
    for x1, y1, x2, y2, npix in components:
        z_region = z_vv[y1:y2, x1:x2]
        z_peak = float(z_region.max()) if z_region.size else 0.0
        db_peak = float(vv[y1:y2, x1:x2].max())
        # Map Z-score to a [0, 1] confidence. Saturate at z=8 (very rare
        # under the Gaussian model — anything stronger is a clear target).
        confidence = max(0.0, min(1.0, (z_peak - threshold_sigma) / 8.0 + 0.5))
        cx = (x1 + x2) / 2.0 / w
        cy = (y1 + y2) / 2.0 / h
        bw = (x2 - x1) / w
        bh = (y2 - y1) / h
        detections.append({
            "class": "ship",
            "parent_class": "vessel",
            "source_layer": "sar_cfar",
            "method": "cfar",
            "bbox": [cx, cy, bw, bh],
            "pixel_bbox": [float(x1), float(y1), float(x2), float(y2)],
            "confidence": confidence,
            "dB_peak": db_peak,
            "modality": "sar",
            "evidence": [f"cfar_z_peak={z_peak:.2f}", f"npix={npix}"],
        })
    logger.info(
        "sar_cfar: image=%dx%d threshold=%.1fσ guard=%d bg=%d -> %d detections",
        h, w, threshold_sigma, guard_px, background_px, len(detections),
    )
    return detections

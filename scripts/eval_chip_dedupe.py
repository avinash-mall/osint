#!/usr/bin/env python3
"""Worker-level cross-chip dedupe evaluation: NMS vs WBF.

Compares the worker's two cross-chip deduplication strategies — the default
greedy-NMS ``_DetectionDedupeIndex`` and the opt-in confidence-averaging
``_WeightedBoxFusionIndex`` (``DEDUPE_METHOD=wbf``) — on a labelled DOTA-v1.0
val slice. Both paths consume the SAME global-coord detection stream so the
only variable is the reconciliation algorithm.

Pipeline (mirrors backend/worker_legacy.py:slice_and_infer):
  1. Load DOTA val images + GT boxes (image-pixel coords).
  2. plan_inference_grid(width, height, chip_size, overlap, max_chips) — the
     real worker planner, with the worker's default chip_size/overlap.
  3. For each chip window: crop, POST to /detect (cached on disk), and map
     each detection's normalized bbox/obb to GLOBAL image-pixel coords exactly
     as _apply_chip_response does (x_offset + local*win*scale, scale=1.0).
  4. Stream the per-chip global detections through BOTH dedupe indices via
     .add() — matching the streaming worker — then read out survivors
     (NMS: accumulated survivors; WBF: .heads()). reconcile_edge_truncated()
     is run on both (NMS does real edge stitching; WBF is a documented no-op).
  5. Score each survivor set against GT at a fixed IoU (default 0.50) with
     greedy confidence-ordered matching: per-class + overall P/R/F1, plus a
     per-class AP@IoU (11-point-free, all-points interpolation) and mAP.

The DOTA labels are open-set; we map the inference open-vocab labels onto the
DOTA class vocabulary via scripts/eval_metrics/label_normalizer when available,
else a built-in synonym table. Unmatched predicted classes are scored under
their normalized label and simply won't match any GT class (counted as FPs) —
this is honest: a label the detector emits that DOTA never annotated cannot be
a true positive at the class level.

Deterministic and cacheable. Re-runs read cached /detect JSON from
``--cache-dir`` so only the dedupe + scoring re-executes.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests
from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

# Real worker implementations — do NOT reimplement.
from worker_legacy import (  # noqa: E402
    _DetectionDedupeIndex,
    _WeightedBoxFusionIndex,
    plan_inference_grid,
)

_DOTA_DIR = _REPO_ROOT / "inference-sam3" / "eval" / "datasets" / "dota"
_DEFAULT_CACHE = _REPO_ROOT / "bench" / "chip_dedupe_cache"


# ---------------------------------------------------------------------------
# Label normalization: open-vocab prediction label -> DOTA class
# ---------------------------------------------------------------------------
_DOTA_CLASSES = {
    "plane", "ship", "storage-tank", "baseball-diamond", "tennis-court",
    "basketball-court", "ground-track-field", "harbor", "bridge",
    "large-vehicle", "small-vehicle", "helicopter", "roundabout",
    "soccer-ball-field", "swimming-pool", "container-crane", "airport", "helipad",
}

# Built-in synonym map: detector open-vocab labels -> DOTA class. Kept small and
# explicit; anything not here keeps its raw normalized label (and will not match
# a DOTA GT class).
_SYNONYMS = {
    "airplane": "plane", "plane": "plane", "aircraft": "plane", "jet": "plane",
    "airliner": "plane", "fighter jet": "plane", "fighter": "plane",
    "helicopter": "helicopter", "chopper": "helicopter",
    "ship": "ship", "boat": "ship", "vessel": "ship", "cargo ship": "ship",
    "container ship": "ship", "tanker": "ship", "yacht": "ship", "ferry": "ship",
    "car": "small-vehicle", "small vehicle": "small-vehicle", "sedan": "small-vehicle",
    "suv": "small-vehicle", "pickup truck": "small-vehicle", "van": "small-vehicle",
    "vehicle": "small-vehicle", "automobile": "small-vehicle",
    "truck": "large-vehicle", "large vehicle": "large-vehicle", "bus": "large-vehicle",
    "trailer": "large-vehicle", "lorry": "large-vehicle", "semi truck": "large-vehicle",
    "storage tank": "storage-tank", "tank": "storage-tank", "oil tank": "storage-tank",
    "silo": "storage-tank",
    "tennis court": "tennis-court",
    "basketball court": "basketball-court",
    "baseball diamond": "baseball-diamond", "baseball field": "baseball-diamond",
    "ground track field": "ground-track-field", "running track": "ground-track-field",
    "track field": "ground-track-field", "athletics track": "ground-track-field",
    "soccer ball field": "soccer-ball-field", "soccer field": "soccer-ball-field",
    "football field": "soccer-ball-field",
    "swimming pool": "swimming-pool", "pool": "swimming-pool",
    "harbor": "harbor", "harbour": "harbor", "dock": "harbor", "pier": "harbor",
    "marina": "harbor", "wharf": "harbor", "port": "harbor",
    "bridge": "bridge", "overpass": "bridge", "viaduct": "bridge",
    "roundabout": "roundabout", "traffic circle": "roundabout",
    "container crane": "container-crane", "crane": "container-crane",
    "gantry crane": "container-crane",
    "airport": "airport", "airfield": "airport", "runway": "airport",
    "helipad": "helipad", "helicopter pad": "helipad",
}

try:
    from eval_metrics.label_normalizer import normalize_label as _ext_norm  # type: ignore
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
except Exception:
    _ext_norm = None


def normalize_pred_label(raw: str) -> str:
    """Map an open-vocab prediction label onto the DOTA vocabulary when possible."""
    if not raw:
        return "unknown"
    key = str(raw).strip().lower()
    if key in _DOTA_CLASSES:
        return key
    hyph = key.replace(" ", "-")
    if hyph in _DOTA_CLASSES:
        return hyph
    if key in _SYNONYMS:
        return _SYNONYMS[key]
    return key  # keep raw; will simply not match a DOTA class


def normalize_gt_label(raw: str) -> str:
    return str(raw).strip().lower()


# ---------------------------------------------------------------------------
# /detect call + cache
# ---------------------------------------------------------------------------
def detect_chip(crop: Image.Image, endpoint: str, cache_dir: Path, cache_key: str) -> list[dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{cache_key}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text())
        except json.JSONDecodeError:
            pass
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    buf.seek(0)
    resp = requests.post(
        endpoint,
        files={"image": (f"{cache_key}.png", buf, "image/png")},
        data={"metadata": json.dumps({"modality": "rgb"})},
        timeout=900,
    )
    resp.raise_for_status()
    dets = resp.json().get("detections", []) or []
    cache_path.write_text(json.dumps(dets))
    return dets


# ---------------------------------------------------------------------------
# Chip-local normalized bbox/obb -> global image-pixel coords (mirror worker)
# ---------------------------------------------------------------------------
def map_to_global(det: dict, x_off: int, y_off: int, win_w: int, win_h: int,
                  img_w: int, img_h: int) -> dict | None:
    bbox = det.get("bbox")
    if not bbox or len(bbox) < 4:
        return None
    cx, cy, w, h = [float(v) for v in bbox[:4]]
    chip_px_cx = cx * win_w
    chip_px_cy = cy * win_h
    chip_px_w = max(0.0, w * win_w)
    chip_px_h = max(0.0, h * win_h)
    lx1 = chip_px_cx - chip_px_w / 2
    ly1 = chip_px_cy - chip_px_h / 2
    lx2 = chip_px_cx + chip_px_w / 2
    ly2 = chip_px_cy + chip_px_h / 2
    # scale_x/scale_y = 1.0 for normal chips (chip-px == source-window-px)
    ax1 = min(max(x_off + lx1, 0), img_w)
    ay1 = min(max(y_off + ly1, 0), img_h)
    ax2 = min(max(x_off + lx2, 0), img_w)
    ay2 = min(max(y_off + ly2, 0), img_h)
    if ax2 <= ax1 or ay2 <= ay1:
        return None

    obb = det.get("obb")
    if obb and len(obb) == 8:
        pixel_obb = []
        for i, v in enumerate(obb):
            if i % 2 == 0:
                pixel_obb.append(min(max(x_off + float(v) * win_w, 0), img_w))
            else:
                pixel_obb.append(min(max(y_off + float(v) * win_h, 0), img_h))
    else:
        pixel_obb = [ax1, ay1, ax2, ay1, ax2, ay2, ax1, ay2]

    out = dict(det)  # preserve class/confidence/source_layer/modality/edge_truncated/...
    out["pixel_bbox"] = [ax1, ay1, ax2, ay2]
    out["pixel_obb"] = pixel_obb
    # parent_class drives the dedupe bucket; keep the inference value as-is.
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def iou_xyxy(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def score_image(preds: list[dict], gts: list[dict], iou_thr: float):
    """Greedy confidence-ordered matching per class. Returns per-class
    (tp, fp, fn) counts and a list of (class, confidence, is_tp) for AP."""
    gt_by_class: dict[str, list[dict]] = defaultdict(list)
    for g in gts:
        gt_by_class[normalize_gt_label(g["label"])].append(
            {"box": g["bbox_xyxy"], "used": False}
        )
    pred_by_class: dict[str, list[dict]] = defaultdict(list)
    for p in preds:
        cls = normalize_pred_label(p.get("parent_class") or p.get("class"))
        pb = p.get("pixel_bbox")
        if not pb:
            continue
        pred_by_class[cls].append({"box": pb, "conf": float(p.get("confidence") or 0.0)})

    per_class = defaultdict(lambda: [0, 0, 0])  # cls -> [tp, fp, fn]
    ap_records = []  # (cls, conf, is_tp)
    classes = set(gt_by_class) | set(pred_by_class)
    for cls in classes:
        gboxes = gt_by_class.get(cls, [])
        pboxes = sorted(pred_by_class.get(cls, []), key=lambda d: d["conf"], reverse=True)
        for p in pboxes:
            best_iou, best_g = 0.0, None
            for g in gboxes:
                if g["used"]:
                    continue
                i = iou_xyxy(p["box"], g["box"])
                if i > best_iou:
                    best_iou, best_g = i, g
            is_tp = best_iou >= iou_thr and best_g is not None
            if is_tp:
                best_g["used"] = True
                per_class[cls][0] += 1
            else:
                per_class[cls][1] += 1
            ap_records.append((cls, p["conf"], is_tp))
        per_class[cls][2] += sum(1 for g in gboxes if not g["used"])
    return per_class, ap_records


def average_precision(records: list[tuple[str, float, bool]], total_gt: int) -> float:
    """All-points-interpolated AP for one class. records: (conf, is_tp)."""
    if total_gt == 0:
        return float("nan")
    recs = sorted(records, key=lambda r: r[1], reverse=True)
    tp = fp = 0
    precisions, recalls = [], []
    for _, _conf, is_tp in recs:
        if is_tp:
            tp += 1
        else:
            fp += 1
        precisions.append(tp / (tp + fp))
        recalls.append(tp / total_gt)
    if not precisions:
        return 0.0
    # all-points interpolation
    precisions = np.array(precisions)
    recalls = np.array(recalls)
    ap = 0.0
    prev_r = 0.0
    # envelope precision
    for i in range(len(precisions)):
        p_interp = precisions[i:].max()
        dr = recalls[i] - prev_r
        if dr > 0:
            ap += p_interp * dr
            prev_r = recalls[i]
    return float(ap)


# ---------------------------------------------------------------------------
# Dedupe drivers (mirror the streaming worker)
# ---------------------------------------------------------------------------
def run_nms(per_chip: list[list[dict]]):
    idx = _DetectionDedupeIndex()
    survivors: list[dict] = []
    for chip_dets in per_chip:
        survivors.extend(idx.add(chip_dets))
    survivors, merges = idx.reconcile_edge_truncated(survivors)
    return survivors, {"raw_seen": idx.raw_seen, "edge_merges": merges}


def run_wbf(per_chip: list[list[dict]], iou_thr: float, expected_models: int):
    idx = _WeightedBoxFusionIndex(iou_threshold=iou_thr, expected_models=expected_models)
    for chip_dets in per_chip:
        idx.add(chip_dets)
    survivors = idx.heads()
    survivors, _ = idx.reconcile_edge_truncated(survivors)
    return survivors, {"raw_seen": idx.raw_seen, "n_clusters": len(survivors)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--endpoint", default="http://localhost:8001/detect")
    ap.add_argument("--dota-dir", type=Path, default=_DOTA_DIR)
    ap.add_argument("--cache-dir", type=Path, default=_DEFAULT_CACHE)
    ap.add_argument("--chip-size", type=int, default=1008)
    ap.add_argument("--overlap", type=int, default=252)
    ap.add_argument("--max-chips", type=int, default=64)
    ap.add_argument("--iou-match", type=float, default=0.50)
    ap.add_argument("--wbf-iou", type=float, default=0.55)
    ap.add_argument("--wbf-expected-models", type=int, default=2)
    ap.add_argument("--num-images", type=int, default=30)
    ap.add_argument("--out-json", type=Path, default=_REPO_ROOT / "bench" / "chip_dedupe_nms_vs_wbf.json")
    args = ap.parse_args()

    labels = json.loads((args.dota_dir / "labels.json").read_text())
    labels = labels[: args.num_images]

    nms_pc = defaultdict(lambda: [0, 0, 0])
    wbf_pc = defaultdict(lambda: [0, 0, 0])
    nms_ap = defaultdict(list)
    wbf_ap = defaultdict(list)
    gt_counts = defaultdict(int)
    nms_total_dets = wbf_total_dets = raw_total = 0
    multichip_images = 0
    total_chips = 0
    edge_merges_total = 0
    examples = []

    for rec in labels:
        chip_path = args.dota_dir / rec["chip_file"]
        if not chip_path.exists():
            print(f"SKIP missing image {chip_path}", file=sys.stderr)
            continue
        img = Image.open(chip_path).convert("RGB")
        W, H = img.size
        gts = rec.get("annotations", [])
        for g in gts:
            gt_counts[normalize_gt_label(g["label"])] += 1

        grid = plan_inference_grid(W, H, args.chip_size, args.overlap, args.max_chips)
        step = grid["step"]
        x_offsets = grid.get("x_offsets") or [i * step for i in grid["x_indices"]]
        y_offsets = grid.get("y_offsets") or [i * step for i in grid["y_indices"]]
        x_sizes = grid.get("x_window_sizes") or [args.chip_size] * len(x_offsets)
        y_sizes = grid.get("y_window_sizes") or [args.chip_size] * len(y_offsets)
        n_chips = len(x_offsets) * len(y_offsets)
        total_chips += n_chips
        if n_chips > 1:
            multichip_images += 1

        per_chip_global: list[list[dict]] = []
        img_bytes_sha = hashlib.sha1(chip_path.read_bytes()).hexdigest()[:12]
        for xi, (xo, xw) in enumerate(zip(x_offsets, x_sizes)):
            for yi, (yo, yw) in enumerate(zip(y_offsets, y_sizes)):
                xo, yo, xw, yw = int(xo), int(yo), int(xw), int(yw)
                crop = img.crop((xo, yo, min(xo + xw, W), min(yo + yw, H)))
                ck = f"{Path(rec['chip_file']).stem}_{img_bytes_sha}_x{xo}_y{yo}_w{xw}_h{yw}"
                raw_dets = detect_chip(crop, args.endpoint, args.cache_dir, ck)
                mapped = []
                for d in raw_dets:
                    m = map_to_global(d, xo, yo, crop.width, crop.height, W, H)
                    if m is not None:
                        mapped.append(m)
                per_chip_global.append(mapped)

        nms_surv, nms_meta = run_nms([list(c) for c in per_chip_global])
        wbf_surv, wbf_meta = run_wbf([list(c) for c in per_chip_global],
                                     args.wbf_iou, args.wbf_expected_models)
        raw_total += sum(len(c) for c in per_chip_global)
        nms_total_dets += len(nms_surv)
        wbf_total_dets += len(wbf_surv)
        edge_merges_total += nms_meta["edge_merges"]

        n_pc, n_ap = score_image(nms_surv, gts, args.iou_match)
        w_pc, w_ap = score_image(wbf_surv, gts, args.iou_match)
        for cls, (tp, fp, fn) in n_pc.items():
            nms_pc[cls][0] += tp; nms_pc[cls][1] += fp; nms_pc[cls][2] += fn
        for cls, (tp, fp, fn) in w_pc.items():
            wbf_pc[cls][0] += tp; wbf_pc[cls][1] += fp; wbf_pc[cls][2] += fn
        for cls, conf, is_tp in n_ap:
            nms_ap[cls].append((conf, is_tp))
        for cls, conf, is_tp in w_ap:
            wbf_ap[cls].append((conf, is_tp))

        # qualitative: did the two methods produce different counts on this image?
        if abs(len(nms_surv) - len(wbf_surv)) > 0:
            examples.append({
                "image": rec["chip_file"], "size": [W, H], "chips": n_chips,
                "raw": sum(len(c) for c in per_chip_global),
                "nms_kept": len(nms_surv), "wbf_kept": len(wbf_surv),
                "edge_merges_nms": nms_meta["edge_merges"],
            })

    def prf(pc):
        out = {}
        TP = FP = FN = 0
        for cls, (tp, fp, fn) in sorted(pc.items()):
            p = tp / (tp + fp) if (tp + fp) else 0.0
            r = tp / (tp + fn) if (tp + fn) else 0.0
            f1 = 2 * p * r / (p + r) if (p + r) else 0.0
            out[cls] = {"tp": tp, "fp": fp, "fn": fn,
                        "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}
            TP += tp; FP += fp; FN += fn
        P = TP / (TP + FP) if (TP + FP) else 0.0
        R = TP / (TP + FN) if (TP + FN) else 0.0
        F = 2 * P * R / (P + R) if (P + R) else 0.0
        out["__overall__"] = {"tp": TP, "fp": FP, "fn": FN,
                              "precision": round(P, 4), "recall": round(R, 4), "f1": round(F, 4)}
        return out

    def map_at(apdict):
        aps = {}
        for cls, recs in apdict.items():
            n_gt = gt_counts.get(cls, 0)
            if n_gt == 0:
                continue  # only DOTA-annotated classes contribute to mAP
            aps[cls] = round(average_precision([(cls, c, t) for c, t in recs], n_gt), 4)
        valid = [v for v in aps.values() if v == v]  # drop nan
        return aps, (round(sum(valid) / len(valid), 4) if valid else 0.0)

    nms_prf = prf(nms_pc)
    wbf_prf = prf(wbf_pc)
    nms_ap_per, nms_map = map_at(nms_ap)
    wbf_ap_per, wbf_map = map_at(wbf_ap)

    result = {
        "parameters": {
            "endpoint": args.endpoint, "chip_size": args.chip_size, "overlap": args.overlap,
            "max_chips": args.max_chips, "iou_match": args.iou_match,
            "wbf_iou": args.wbf_iou, "wbf_expected_models": args.wbf_expected_models,
            "num_images": len(labels),
        },
        "sample": {
            "images_evaluated": len(labels), "total_chips": total_chips,
            "multichip_images": multichip_images, "total_gt_boxes": int(sum(gt_counts.values())),
            "raw_detections": raw_total, "nms_kept": nms_total_dets, "wbf_kept": wbf_total_dets,
            "nms_edge_merges": edge_merges_total,
        },
        "gt_counts": dict(sorted(gt_counts.items())),
        "nms": {"per_class": nms_prf, "ap": nms_ap_per, "mAP@%.2f" % args.iou_match: nms_map},
        "wbf": {"per_class": wbf_prf, "ap": wbf_ap_per, "mAP@%.2f" % args.iou_match: wbf_map},
        "qualitative_count_diffs": examples,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, indent=2))

    # Console summary
    print(f"\n=== Sample ===")
    for k, v in result["sample"].items():
        print(f"  {k}: {v}")
    print(f"\n=== Overall (IoU={args.iou_match}) ===")
    print(f"  NMS  P/R/F1 = {nms_prf['__overall__']['precision']} / "
          f"{nms_prf['__overall__']['recall']} / {nms_prf['__overall__']['f1']}   "
          f"mAP@{args.iou_match} = {nms_map}")
    print(f"  WBF  P/R/F1 = {wbf_prf['__overall__']['precision']} / "
          f"{wbf_prf['__overall__']['recall']} / {wbf_prf['__overall__']['f1']}   "
          f"mAP@{args.iou_match} = {wbf_map}")
    print(f"\n=== Per-class (only DOTA-annotated, sorted) ===")
    print(f"  {'class':<20} {'GT':>5} | {'NMS P/R/F1':>22} | {'WBF P/R/F1':>22}")
    for cls in sorted(gt_counts):
        n = nms_prf.get(cls); w = wbf_prf.get(cls)
        if not n and not w:
            continue
        nn = n or {"precision": 0, "recall": 0, "f1": 0}
        ww = w or {"precision": 0, "recall": 0, "f1": 0}
        print(f"  {cls:<20} {gt_counts[cls]:>5} | "
              f"{nn['precision']:.2f}/{nn['recall']:.2f}/{nn['f1']:.2f}".rjust(22) + " | "
              f"{ww['precision']:.2f}/{ww['recall']:.2f}/{ww['f1']:.2f}".rjust(22))
    print(f"\nWrote {args.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

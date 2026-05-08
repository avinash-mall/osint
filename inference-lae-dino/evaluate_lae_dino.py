#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import requests


def norm_label(value: str) -> str:
    return " ".join(str(value or "").replace("_", " ").replace("-", " ").lower().split())


def iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter = max(0.0, inter_x2 - inter_x1) * max(0.0, inter_y2 - inter_y1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union else 0.0


def normalized_to_xyxy(det: dict[str, Any], width: int, height: int) -> list[float]:
    cx, cy, bw, bh = [float(value) for value in det["bbox"][:4]]
    return [
        (cx - bw / 2) * width,
        (cy - bh / 2) * height,
        (cx + bw / 2) * width,
        (cy + bh / 2) * height,
    ]


def load_eval_items(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "images" in data:
        return list(data["images"])
    if isinstance(data, list):
        return data
    raise SystemExit("Expected JSON list or object with an 'images' array.")


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as image:
        return image.size


def post_image(url: str, image_path: Path, threshold: float, prompt_profile: str) -> dict[str, Any]:
    metadata = {
        "prompt_profile": prompt_profile,
        "confidence_threshold": threshold,
    }
    with image_path.open("rb") as handle:
        response = requests.post(
            f"{url.rstrip('/')}/detect",
            files={"image": (image_path.name, handle, "image/png")},
            data={"metadata": json.dumps(metadata)},
            timeout=120,
        )
    response.raise_for_status()
    return response.json()


def score_image(predictions: list[dict[str, Any]], annotations: list[dict[str, Any]], width: int, height: int, iou_threshold: float) -> dict[str, Any]:
    pred_rows = [
        {
            "label": norm_label(det.get("original_class") or det.get("class")),
            "bbox": normalized_to_xyxy(det, width, height),
            "confidence": float(det.get("confidence") or 0.0),
        }
        for det in predictions
    ]
    gt_rows = [
        {
            "label": norm_label(ann.get("class") or ann.get("label") or ann.get("category")),
            "bbox": [float(value) for value in ann["bbox"][:4]],
        }
        for ann in annotations
    ]

    matched_gt: set[int] = set()
    tp = 0
    fp = 0
    confusion: Counter[tuple[str, str]] = Counter()
    for pred in sorted(pred_rows, key=lambda row: row["confidence"], reverse=True):
        best_index = None
        best_iou = 0.0
        for index, gt in enumerate(gt_rows):
            if index in matched_gt:
                continue
            overlap = iou(pred["bbox"], gt["bbox"])
            if overlap > best_iou:
                best_iou = overlap
                best_index = index
        if best_index is not None and best_iou >= iou_threshold:
            matched_gt.add(best_index)
            gt_label = gt_rows[best_index]["label"]
            if pred["label"] == gt_label:
                tp += 1
            else:
                fp += 1
                confusion[(gt_label, pred["label"])] += 1
        else:
            fp += 1

    fn = len(gt_rows) - len(matched_gt)
    return {"tp": tp, "fp": fp, "fn": fn, "confusion": confusion}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate LAE-DINO /detect on a small labeled chip set.")
    parser.add_argument("--annotations", type=Path, required=True, help="JSON list or {'images': [...]} with file + annotations.")
    parser.add_argument("--image-root", type=Path, default=Path("."), help="Base directory for relative image paths.")
    parser.add_argument("--url", default="http://localhost:8004", help="LAE-DINO service URL.")
    parser.add_argument("--thresholds", default="0.30,0.40,0.50", help="Comma-separated confidence thresholds.")
    parser.add_argument("--prompt-profile", default="official_lae80c")
    parser.add_argument("--iou", type=float, default=0.5)
    args = parser.parse_args()

    items = load_eval_items(args.annotations)
    thresholds = [float(item.strip()) for item in args.thresholds.split(",") if item.strip()]
    for threshold in thresholds:
        totals = Counter()
        confusion: Counter[tuple[str, str]] = Counter()
        for item in items:
            image_path = args.image_root / item["file"]
            width, height = image_size(image_path)
            response = post_image(args.url, image_path, threshold, args.prompt_profile)
            result = score_image(response.get("detections", []), item.get("annotations", []), width, height, args.iou)
            totals.update({key: result[key] for key in ("tp", "fp", "fn")})
            confusion.update(result["confusion"])

        precision = totals["tp"] / max(1, totals["tp"] + totals["fp"])
        recall = totals["tp"] / max(1, totals["tp"] + totals["fn"])
        print(json.dumps({
            "threshold": threshold,
            "iou": args.iou,
            "tp": totals["tp"],
            "fp": totals["fp"],
            "fn": totals["fn"],
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "confusion": {f"{gt}->{pred}": count for (gt, pred), count in confusion.items()},
        }, sort_keys=True))


if __name__ == "__main__":
    main()

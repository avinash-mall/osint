#!/usr/bin/env python3
"""Audit a YOLO training run for the optical-defense detection workflow.

This script reads Ultralytics run artifacts such as args.yaml and results.csv.
It does not require the original dataset, so it can be used on copied
training_dataset/runs directories.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


DEFAULT_RUN = Path(__file__).resolve().parents[1] / "training_dataset" / "runs" / "geoint_yolov8"
BASELINE_RECALL = 0.525


def parse_results_csv(path: Path) -> list[dict[str, float]]:
    if not path.exists():
        return []
    rows: list[dict[str, float]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            parsed: dict[str, float] = {}
            for key, value in row.items():
                clean_key = key.strip().replace("metrics/", "").replace("(B)", "").replace("(M)", "")
                try:
                    parsed[clean_key] = float(value)
                except (TypeError, ValueError):
                    continue
            rows.append(parsed)
    return rows


def best_row(rows: list[dict[str, float]], metric: str) -> dict[str, Any]:
    if not rows:
        return {"epoch": None, metric: None}
    row = max(rows, key=lambda item: item.get(metric, float("-inf")))
    return {"epoch": int(row.get("epoch", 0)), **row}


def run_audit(run_dir: Path, baseline_recall: float) -> dict[str, Any]:
    rows = parse_results_csv(run_dir / "results.csv")
    final = rows[-1] if rows else {}
    required_files = [
        "args.yaml",
        "results.csv",
        "confusion_matrix.png",
        "confusion_matrix_normalized.png",
        "labels.jpg",
        "BoxF1_curve.png",
        "BoxPR_curve.png",
        "weights/best.pt",
    ]
    missing = [item for item in required_files if not (run_dir / item).exists()]
    return {
        "run_dir": str(run_dir),
        "status": "ok" if rows and not missing else "incomplete",
        "missing_artifacts": missing,
        "final_metrics": final,
        "best_map50_95": best_row(rows, "mAP50-95"),
        "best_recall": best_row(rows, "recall"),
        "best_precision": best_row(rows, "precision"),
        "promotion_baseline_recall": baseline_recall,
        "promotion_recall_pass": bool(final.get("recall", 0.0) > baseline_recall),
        "recommendations": [
            "Recover or regenerate training_dataset/yolo/data.yaml, classes.json, taxonomy.json, manifest.jsonl, and split reports before retraining.",
            "Use optical-defense taxonomy collapse and hard-negative distractor tiles.",
            "Do not promote by all-class mAP alone; promote by defense-core recall and failure-benchmark performance.",
            "Inspect labels.jpg and confusion matrices for long-tail classes, distractor bias, and HRSC numeric class leakage.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit a YOLO OBB training run.")
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--baseline-recall", type=float, default=BASELINE_RECALL)
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    audit = run_audit(args.run_dir.resolve(), args.baseline_recall)
    output = json.dumps(audit, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)
    return 0 if audit["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

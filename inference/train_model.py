#!/usr/bin/env python3
"""
Train the GEOINT YOLOv8 detector and publish the best checkpoint for inference.

Default input:
  training_dataset/yolo/data.yaml

Default promoted output:
  inference/models/geoint_yolov8.pt
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = REPO_ROOT / "training_dataset" / "yolo" / "data.yaml"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "models" / "geoint_yolov8.pt"
DEFAULT_PROJECT = REPO_ROOT / "training_dataset" / "runs"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train YOLOv8 for SentinelOS GEOINT inference.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="YOLO data.yaml path.")
    parser.add_argument("--base-model", default="yolov8n.pt", help="Base checkpoint, e.g. yolov8n.pt/yolov8s.pt.")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", default="auto", help="Batch size or auto.")
    parser.add_argument("--device", default=None, help="Ultralytics device, e.g. cpu, 0, 0,1.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--patience", type=int, default=25)
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--name", default="geoint_yolov8")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Promoted model path used by inference.")
    parser.add_argument("--no-promote", action="store_true", help="Do not copy best.pt to the inference models directory.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    data_path = args.data.resolve()
    if not data_path.exists():
        raise SystemExit(f"Dataset config not found: {data_path}\nRun inference/prepare_datasets.py first.")

    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit("ultralytics is required for training. Install inference/requirements.txt first.") from exc

    model = YOLO(args.base_model)
    train_kwargs = {
        "data": str(data_path),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "workers": args.workers,
        "patience": args.patience,
        "project": str(args.project.resolve()),
        "name": args.name,
        "exist_ok": True,
    }
    if args.device:
        train_kwargs["device"] = args.device

    results = model.train(**train_kwargs)
    save_dir = Path(getattr(results, "save_dir", args.project / args.name))
    best_model = save_dir / "weights" / "best.pt"
    if not best_model.exists():
        raise SystemExit(f"Training finished but best.pt was not found at {best_model}")

    if not args.no_promote:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best_model, args.output)
        metadata = {
            "promoted_at": datetime.now(timezone.utc).isoformat(),
            "source_checkpoint": str(best_model.resolve()),
            "data": str(data_path),
            "base_model": args.base_model,
            "epochs": args.epochs,
            "imgsz": args.imgsz,
        }
        args.output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(f"Promoted trained model to {args.output}")

    print(f"Training run: {save_dir}")
    print(f"Best checkpoint: {best_model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

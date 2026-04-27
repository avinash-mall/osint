#!/usr/bin/env python3
"""
Train the GEOINT YOLOv8 detector and publish the best checkpoint for inference.

Default input:
  training_dataset/yolo/data.yaml

Default promoted output:
  inference/models/geoint_yolov8_obb.pt
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = REPO_ROOT / "training_dataset" / "yolo" / "data.yaml"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "models" / "geoint_yolov8_obb.pt"
DEFAULT_PROJECT = REPO_ROOT / "training_dataset" / "runs"


def parse_batch(value: str) -> int | float:
    normalized = value.strip().lower()
    if normalized == "auto":
        return -1
    try:
        if any(char in normalized for char in (".", "e")):
            return float(normalized)
        return int(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("batch must be 'auto', an int, or a float fraction") from exc


def resolve_device(requested: str | None) -> str | None:
    if requested and requested.lower() != "auto":
        return requested
    try:
        import torch
    except ImportError:
        if requested == "auto":
            print("WARNING: torch is not installed, leaving Ultralytics device selection unchanged.")
        return None

    cuda_available = torch.cuda.is_available()
    cuda_version = getattr(torch.version, "cuda", None)
    if cuda_available:
        device_name = torch.cuda.get_device_name(0)
        print(f"Using CUDA device 0: {device_name} (torch CUDA {cuda_version})")
        return "0"

    if requested == "auto" or requested is None:
        print(
            "WARNING: PyTorch reports CUDA is unavailable; training will run on CPU. "
            f"torch={torch.__version__}, torch CUDA={cuda_version}"
        )
    return "cpu" if requested == "auto" else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train YOLOv8 for SentinelOS GEOINT inference.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="YOLO data.yaml path.")
    parser.add_argument("--base-model", default="yolov8n-obb.pt", help="Base OBB checkpoint, e.g. yolov8n-obb.pt/yolov8s-obb.pt.")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=parse_batch, default=-1, help="Batch size, GPU memory fraction, or auto.")
    parser.add_argument("--device", default="auto", help="Ultralytics device, e.g. auto, cpu, 0, 0,1.")
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
    resolved_device = resolve_device(args.device)

    train_kwargs: dict[str, Any] = {
        "data": str(data_path),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "workers": args.workers,
        "patience": args.patience,
        "project": str(args.project.resolve()),
        "name": args.name,
        "exist_ok": True,
        "task": "obb",
    }
    if resolved_device:
        train_kwargs["device"] = resolved_device

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
            "batch": args.batch,
            "device": resolved_device or args.device,
        }
        args.output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(f"Promoted trained model to {args.output}")

    print(f"Training run: {save_dir}")
    print(f"Best checkpoint: {best_model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

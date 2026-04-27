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
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = REPO_ROOT / "training_dataset" / "yolo" / "data.yaml"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "models" / "geoint_yolov8_obb.pt"
DEFAULT_PROJECT = REPO_ROOT / "training_dataset" / "runs"


def available_cpu_count() -> int:
    if hasattr(os, "sched_getaffinity"):
        try:
            return len(os.sched_getaffinity(0))
        except OSError:
            pass
    return os.cpu_count() or 1


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


def nvidia_smi_summary() -> str | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return "; ".join(lines) if lines else None


def device_count(device: str | None) -> int:
    if not device or device == "cpu":
        return 0
    return len([part for part in device.split(",") if part.strip().isdigit()])


def auto_worker_count(device: str | None) -> int:
    cores = available_cpu_count()
    gpus = device_count(device)
    if gpus:
        return max(1, min(16, cores // max(1, gpus * 2)))
    return max(1, min(8, cores // 4))


def configure_cpu_threads(device: str | None) -> int:
    cores = available_cpu_count()
    gpus = device_count(device)
    threads = max(1, min(cores, 8 if gpus else max(1, cores // 2)))
    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    os.environ.setdefault("MKL_NUM_THREADS", str(threads))
    try:
        import torch
        torch.set_num_threads(threads)
        torch.set_num_interop_threads(max(1, min(4, threads // 2)))
    except (ImportError, RuntimeError):
        pass
    return threads


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
        devices = ",".join(str(index) for index in range(torch.cuda.device_count()))
        names = [torch.cuda.get_device_name(index) for index in range(torch.cuda.device_count())]
        print(f"Using CUDA devices {devices}: {', '.join(names)} (torch CUDA {cuda_version})")
        return devices

    gpu_summary = nvidia_smi_summary()
    if not gpu_summary and (requested == "auto" or requested is None):
        print(f"No CUDA GPU detected; using CPU training. torch={torch.__version__}, torch CUDA={cuda_version}")
        return "cpu"

    message = (
        "PyTorch reports CUDA is unavailable, so GPU training cannot start.\n"
        f"  torch: {torch.__version__}\n"
        f"  torch CUDA: {cuda_version}\n"
    )
    if gpu_summary:
        message += f"  nvidia-smi: {gpu_summary}\n"
    message += (
        "\nFix the CUDA/driver mismatch, then rerun training. "
        "Use --device cpu only if you intentionally want CPU training."
    )
    if requested == "auto" or requested is None:
        raise SystemExit(message)
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train YOLOv8 for SentinelOS GEOINT inference.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="YOLO data.yaml path.")
    parser.add_argument("--base-model", default="yolov8n-obb.pt", help="Base OBB checkpoint, e.g. yolov8n-obb.pt/yolov8s-obb.pt.")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=parse_batch, default=-1, help="Batch size, GPU memory fraction, or auto.")
    parser.add_argument("--device", default="auto", help="Ultralytics device, e.g. auto, cpu, 0, 0,1.")
    parser.add_argument("--workers", type=int, default=None, help="Dataloader workers. Defaults to an automatic CPU/GPU-aware value.")
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
    batch = args.batch
    gpu_count = device_count(resolved_device)
    if gpu_count > 1 and batch == -1:
        batch = 16 * gpu_count
        print(f"Auto batch is not used for multi-GPU training; using batch={batch}. Override with --batch if needed.")
    cpu_threads = configure_cpu_threads(resolved_device)
    workers = args.workers if args.workers is not None else auto_worker_count(resolved_device)
    print(f"Using {cpu_threads} CPU compute threads and {workers} dataloader workers.")

    train_kwargs: dict[str, Any] = {
        "data": str(data_path),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": batch,
        "workers": workers,
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
            "batch": batch,
            "device": resolved_device or args.device,
            "cpu_threads": cpu_threads,
            "workers": workers,
        }
        args.output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(f"Promoted trained model to {args.output}")

    print(f"Training run: {save_dir}")
    print(f"Best checkpoint: {best_model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

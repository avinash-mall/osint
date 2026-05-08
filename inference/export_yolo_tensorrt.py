#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path


DEFAULT_MODEL_PATH = Path(os.getenv("MODEL_PATH") or os.getenv("TRAINED_MODEL_PATH") or "/app/models/geoint_yolov8_obb.pt")
DEFAULT_ENGINE_PATH = Path(os.getenv("YOLO_ENGINE_PATH") or DEFAULT_MODEL_PATH.with_suffix(".engine"))


def file_signature(path: Path) -> dict:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "exists": True,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def gpu_signature() -> dict:
    try:
        import torch
        if not torch.cuda.is_available():
            return {}
        return {
            "name": torch.cuda.get_device_name(0),
            "capability": list(torch.cuda.get_device_capability(0)),
            "torch_cuda": getattr(torch.version, "cuda", None),
        }
    except Exception as exc:
        return {"error": str(exc)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a YOLO checkpoint to a TensorRT engine on the target GPU host.")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH, help="Input .pt checkpoint.")
    parser.add_argument("--engine", type=Path, default=DEFAULT_ENGINE_PATH, help="Output .engine path.")
    parser.add_argument("--imgsz", type=int, default=int(os.getenv("YOLO_IMGSZ", "1024")), help="Export image size.")
    parser.add_argument("--batch", type=int, default=int(os.getenv("YOLO_BATCH_MAX_SIZE", "8")), help="Maximum dynamic batch size.")
    parser.add_argument("--workspace", type=float, default=float(os.getenv("YOLO_TRT_WORKSPACE", "4")), help="TensorRT workspace size in GiB.")
    parser.add_argument(
        "--precision",
        choices=("fp16", "int8"),
        default=os.getenv("YOLO_TRT_PRECISION", "fp16").strip().lower(),
        help="TensorRT precision. INT8 requires calibration data.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=Path(os.getenv("YOLO_TRT_CALIBRATION_DATA")) if os.getenv("YOLO_TRT_CALIBRATION_DATA") else None,
        help="YOLO data.yaml for INT8 calibration.",
    )
    parser.add_argument("--static", action="store_true", help="Export a fixed-shape engine instead of dynamic batch/input axes.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.model.exists():
        raise SystemExit(f"Model checkpoint does not exist: {args.model}")
    if args.precision == "int8" and not args.data:
        raise SystemExit("INT8 export requires --data or YOLO_TRT_CALIBRATION_DATA for calibration.")
    if args.data and not args.data.exists():
        raise SystemExit(f"Calibration data file does not exist: {args.data}")

    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit("ultralytics is required for TensorRT export.") from exc

    args.engine.parent.mkdir(parents=True, exist_ok=True)
    export_kwargs = {
        "format": "engine",
        "imgsz": args.imgsz,
        "batch": args.batch,
        "dynamic": not args.static,
        "workspace": args.workspace,
        "half": args.precision == "fp16",
        "int8": args.precision == "int8",
        "verbose": False,
    }
    if args.data:
        export_kwargs["data"] = str(args.data)

    print(
        "Exporting YOLO TensorRT engine: "
        f"model={args.model} engine={args.engine} precision={args.precision} "
        f"imgsz={args.imgsz} batch={args.batch} dynamic={not args.static}"
    )
    exported = Path(YOLO(str(args.model), task="obb").export(**export_kwargs))
    if exported.resolve() != args.engine.resolve():
        shutil.move(str(exported), str(args.engine))
    metadata_path = Path(f"{args.engine}.json")
    metadata_path.write_text(
        json.dumps(
            {
                "model": str(args.model),
                "engine": str(args.engine),
                "task": "obb",
                "model_signature": file_signature(args.model),
                "precision": args.precision,
                "imgsz": args.imgsz,
                "batch": args.batch,
                "dynamic": not args.static,
                "workspace": args.workspace,
                "data": str(args.data) if args.data else None,
                "gpu": gpu_signature(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"TensorRT engine ready: {args.engine}")
    print(f"TensorRT metadata ready: {metadata_path}")


if __name__ == "__main__":
    main()

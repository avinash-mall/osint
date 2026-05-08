#!/usr/bin/env python3
from __future__ import annotations

import os
import argparse
from pathlib import Path


def available_cpu_count() -> int:
    if hasattr(os, "sched_getaffinity"):
        try:
            return len(os.sched_getaffinity(0))
        except OSError:
            pass
    return os.cpu_count() or 1


def cuda_device_count() -> int:
    requested = os.getenv("DEVICE", "auto").strip().lower()
    if requested and requested not in {"auto", "cpu"}:
        return len([item for item in requested.split(",") if item.strip()])
    if requested == "cpu":
        return 0
    try:
        import torch
    except ImportError:
        return 0
    return torch.cuda.device_count() if torch.cuda.is_available() else 0


def auto_worker_count() -> int:
    requested = os.getenv("WEB_CONCURRENCY") or os.getenv("UVICORN_WORKERS")
    if requested:
        return max(1, int(requested))
    gpus = cuda_device_count()
    if gpus:
        return 1
    return max(1, min(available_cpu_count() // 4, 8))


def configure_thread_env(workers: int) -> int:
    requested = os.getenv("CPU_THREADS", "auto").strip().lower()
    if requested not in {"", "auto"}:
        threads = max(1, int(requested))
    else:
        threads = max(1, available_cpu_count() // max(1, workers))
    os.environ.setdefault("WEB_CONCURRENCY", str(workers))
    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    os.environ.setdefault("MKL_NUM_THREADS", str(threads))
    return threads


def default_app_module() -> str:
    return "main:app" if (Path.cwd() / "main.py").exists() else "inference.main:app"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the inference API with automatic CPU/GPU worker sizing.")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8001")))
    parser.add_argument("--workers", type=int, default=None, help="Override automatic Uvicorn worker count.")
    parser.add_argument("--app", default=os.getenv("APP_MODULE", default_app_module()))
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("uvicorn is required to serve inference. Install inference/requirements.txt first.") from exc

    workers = args.workers or auto_worker_count()
    threads = configure_thread_env(workers)
    print(f"[INFERENCE] Starting {workers} worker process(es), {threads} CPU thread(s) per process.")
    uvicorn.run(args.app, host=args.host, port=args.port, workers=workers)


if __name__ == "__main__":
    main()

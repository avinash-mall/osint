"""Benchmark /detect: latency p50/p95/p99, peak VRAM, throughput.

Usage:
    python benchmark_detect.py --url http://localhost:8001 \
        --chip path/to/chip.png --iters 100 --warmup 5 \
        --prompts "ship,plane,vehicle" --out bench/run.json

Reports:
  * Per-iteration latency (ms), throughput (img/s)
  * p50/p95/p99 latency
  * GPU peak allocated / reserved (queried via /health/memory)
  * Detection count statistics (mean, stdev) - sanity check that
    optimization didn't silently drop detections
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import requests


def run(args) -> dict:
    chip_bytes = Path(args.chip).read_bytes()
    if args.prompts_file:
        prompt_list = json.loads(Path(args.prompts_file).read_text())
        if not isinstance(prompt_list, list):
            raise ValueError(f"{args.prompts_file} must contain a JSON list of strings")
    else:
        prompt_list = [p.strip() for p in (args.prompts or "").split(",") if p.strip()]
    metadata = json.dumps({"modality": "rgb", "prompts": prompt_list})

    latencies_ms: list[float] = []
    detection_counts: list[int] = []

    for _ in range(args.warmup):
        files = {"image": ("chip.png", chip_bytes, "image/png")}
        data = {"metadata": metadata}
        r = requests.post(f"{args.url}/detect", files=files, data=data, timeout=120)
        r.raise_for_status()

    try:
        requests.post(f"{args.url}/health/memory/reset", timeout=5)
    except requests.RequestException:
        pass

    t0 = time.perf_counter()
    for _ in range(args.iters):
        files = {"image": ("chip.png", chip_bytes, "image/png")}
        data = {"metadata": metadata}
        t_start = time.perf_counter()
        r = requests.post(f"{args.url}/detect", files=files, data=data, timeout=120)
        latency_ms = (time.perf_counter() - t_start) * 1000
        r.raise_for_status()
        body = r.json()
        latencies_ms.append(latency_ms)
        detection_counts.append(len(body.get("detections", [])))
    elapsed = time.perf_counter() - t0

    try:
        mem = requests.get(f"{args.url}/health/memory", timeout=5).json()
    except requests.RequestException:
        mem = {"cuda": False, "devices": []}

    sorted_lat = sorted(latencies_ms)

    def pct(p: float) -> float:
        if not sorted_lat:
            return 0.0
        idx = min(len(sorted_lat) - 1, int(round(p / 100.0 * len(sorted_lat))))
        return sorted_lat[idx]

    summary = {
        "iters": args.iters,
        "warmup": args.warmup,
        "latency_ms": {
            "p50": pct(50),
            "p95": pct(95),
            "p99": pct(99),
            "mean": statistics.fmean(latencies_ms) if latencies_ms else 0.0,
            "stdev": statistics.pstdev(latencies_ms) if len(latencies_ms) > 1 else 0.0,
        },
        "throughput_img_per_s": args.iters / elapsed if elapsed > 0 else 0.0,
        "detection_count": {
            "mean": statistics.fmean(detection_counts) if detection_counts else 0.0,
            "min": min(detection_counts) if detection_counts else 0,
            "max": max(detection_counts) if detection_counts else 0,
        },
        "memory": mem,
        "chip_path": args.chip,
        "prompts": prompt_list,
    }
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8001")
    p.add_argument("--chip", required=True, help="path to PNG/JPEG chip")
    p.add_argument("--iters", type=int, default=100)
    p.add_argument("--warmup", type=int, default=5)
    p.add_argument("--prompts", default="ship,plane,vehicle,building")
    p.add_argument("--prompts-file", default=None,
                   help="JSON file containing a list of prompt strings; overrides --prompts")
    p.add_argument("--out", default=None, help="write JSON summary here")
    args = p.parse_args()

    summary = run(args)
    print(json.dumps(summary, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

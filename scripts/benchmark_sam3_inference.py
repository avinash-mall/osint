#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import statistics
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw


def _chip_png(size: int) -> bytes:
    img = Image.new("RGB", (size, size), (28, 34, 42))
    draw = ImageDraw.Draw(img)
    pad = size // 4
    draw.rectangle([pad, pad, size - pad, size - pad], outline=(220, 220, 210), width=max(2, size // 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _post_detect(url: str, chip: bytes, prompts: list[str], timeout: int) -> dict:
    started = time.perf_counter()
    resp = requests.post(
        f"{url.rstrip('/')}/detect",
        files={"image": ("bench.png", io.BytesIO(chip), "image/png")},
        data={"metadata": json.dumps({"modality": "rgb", "text_prompts": prompts, "max_prompts": len(prompts)})},
        timeout=timeout,
    )
    elapsed = time.perf_counter() - started
    payload = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return {
        "status_code": resp.status_code,
        "seconds": elapsed,
        "detections": len(payload.get("detections", [])),
        "service_timings_ms": payload.get("timings_ms", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark SAM3 /detect prompt latency.")
    parser.add_argument("--url", default="http://localhost:8001", help="Inference service base URL.")
    parser.add_argument("--chip-size", type=int, default=1024)
    parser.add_argument("--prompt-counts", default="1,8,32,128,512")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    chip = _chip_png(args.chip_size)
    prompt_counts = [int(item.strip()) for item in args.prompt_counts.split(",") if item.strip()]
    report = {
        "url": args.url,
        "chip_size": args.chip_size,
        "repeats": args.repeats,
        "results": [],
    }

    for count in prompt_counts:
        prompts = [f"object {idx}" for idx in range(count)]
        samples = [_post_detect(args.url, chip, prompts, args.timeout) for _ in range(args.repeats)]
        seconds = [sample["seconds"] for sample in samples]
        row = {
            "prompts": count,
            "p50_seconds": round(statistics.median(seconds), 3),
            "p95_seconds": round(max(seconds) if len(seconds) < 20 else statistics.quantiles(seconds, n=20)[18], 3),
            "samples": samples,
        }
        report["results"].append(row)
        print(json.dumps(row, default=str))

    if args.output:
        args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

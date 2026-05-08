#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests


def post_detect(url: str, image_path: Path, metadata: dict) -> dict:
    with image_path.open("rb") as handle:
        response = requests.post(
            f"{url.rstrip('/')}/detect",
            files={"image": (image_path.name, handle, "image/png")},
            data={"metadata": json.dumps(metadata)},
            timeout=120,
        )
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test a running Grounding DINO service.")
    parser.add_argument("--url", default="http://localhost:8004")
    parser.add_argument("--probe", type=Path, default=Path(__file__).resolve().parent / "probes" / "probe_chip.png")
    args = parser.parse_args()

    health = requests.get(f"{args.url.rstrip('/')}/health", timeout=10)
    health.raise_for_status()
    health_json = health.json()
    required = {
        "model_loaded": True,
        "processor_loaded": True,
    }
    for key, expected in required.items():
        if health_json.get(key) is not expected:
            raise SystemExit(f"health check failed: {key}={health_json.get(key)!r}")
    if not health_json.get("model_id"):
        raise SystemExit("health check failed: model_id is missing")
    if not health_json.get("transformers_version"):
        raise SystemExit("health check failed: transformers_version is missing")
    if health_json.get("prompt_profile") != "official_lae80c":
        raise SystemExit(f"expected prompt_profile=official_lae80c, got {health_json.get('prompt_profile')!r}")

    official = post_detect(args.url, args.probe, {"prompt_profile": "official_lae80c"})
    if official.get("prompt_profile") != "official_lae80c":
        raise SystemExit("official probe response did not include prompt_profile=official_lae80c")
    if int(official.get("prompt_total_chunks") or 0) < 1:
        raise SystemExit("official probe response did not include prompt chunks")
    if len(official.get("detections") or []) < 1:
        raise SystemExit("official probe response did not include detections")

    custom = post_detect(args.url, args.probe, {"text_prompt": "building"})
    if custom.get("prompt_profile") != "custom":
        raise SystemExit("custom probe response did not include prompt_profile=custom")
    if len(custom.get("detections") or []) < 1:
        raise SystemExit("custom probe response did not include detections")

    print(json.dumps({
        "status": "ok",
        "official_detections": len(official.get("detections") or []),
        "custom_detections": len(custom.get("detections") or []),
        "prompt_total_chunks": official.get("prompt_total_chunks"),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

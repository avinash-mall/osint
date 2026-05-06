#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

from gpu_profiles import resolve_gpu_profile


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DEFAULT_HEALTH_URL = "http://127.0.0.1:8005/health"
DEFAULT_DETECT_URL = "http://127.0.0.1:8005/detect"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def detect_gpu_model() -> str | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            check=True,
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    for line in result.stdout.splitlines():
        model = line.strip()
        if model:
            return model
    return None


def compose_env(gpu_model: str) -> dict[str, str]:
    profile = resolve_gpu_profile(gpu_model)
    env = os.environ.copy()
    env.update(read_env_file(ENV_PATH))
    env["GPU_MODEL"] = gpu_model
    env["MMROTATE_GPU_PROFILE"] = profile.name
    env.update(profile.build_env())
    return env


def run_command(command: Iterable[str], env: dict[str, str]) -> None:
    printable = " ".join(command)
    print(f"[mmrotate-build] {printable}", flush=True)
    subprocess.run(list(command), check=True, cwd=ROOT, env=env)


def wait_for_health(url: str, timeout_s: int) -> dict:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, socket.timeout, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def extract_sample_chip(sample_path: Path, chip_size: int) -> Path:
    try:
        import numpy as np
        import rasterio
        from PIL import Image
        from rasterio.windows import Window
    except ImportError as exc:
        raise RuntimeError("Validation requires rasterio, numpy, and pillow in the host Python environment.") from exc

    with rasterio.open(sample_path) as src:
        width = min(chip_size, src.width)
        height = min(chip_size, src.height)
        left = max(0, (src.width - width) // 2)
        top = max(0, (src.height - height) // 2)
        indexes = list(range(1, min(src.count, 3) + 1))
        if not indexes:
            raise RuntimeError(f"{sample_path} has no readable raster bands")
        data = src.read(indexes, window=Window(left, top, width, height), out_shape=(len(indexes), height, width))

    if data.shape[0] == 1:
        data = np.repeat(data, 3, axis=0)
    elif data.shape[0] > 3:
        data = data[:3]

    data = data.astype("float32")
    finite = np.isfinite(data)
    if not finite.any():
        raise RuntimeError(f"{sample_path} chip contains no finite raster values")
    min_value = float(data[finite].min())
    max_value = float(data[finite].max())
    if max_value > min_value:
        data = (data - min_value) * (255.0 / (max_value - min_value))
    else:
        data = np.zeros_like(data)
    data = np.clip(data, 0, 255).astype("uint8")
    image_array = np.transpose(data, (1, 2, 0))

    output = Path(tempfile.gettempdir()) / f"mmrotate_sample_chip_{sample_path.stem}.png"
    Image.fromarray(image_array, mode="RGB").save(output)
    return output


def post_detection(url: str, image_path: Path) -> dict:
    boundary = "----mmrotate-gpu-validation"
    metadata = json.dumps({"source": str(image_path), "validation": "gpu-smoke"})
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="metadata"\r\n\r\n',
            metadata.encode(),
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="image"; filename="{image_path.name}"\r\n'.encode(),
            b"Content-Type: image/png\r\n\r\n",
            image_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def validate_service(health_url: str, detect_url: str, sample_path: Path | None, chip_size: int) -> None:
    health = wait_for_health(health_url, timeout_s=180)
    print(json.dumps({"health": health}, indent=2), flush=True)
    if health.get("device") != "cuda:0":
        raise RuntimeError(f"Expected /health device cuda:0, got {health.get('device')!r}")
    if sample_path is None:
        return
    chip_path = extract_sample_chip(sample_path, chip_size)
    result = post_detection(detect_url, chip_path)
    print(json.dumps({"detect": result}, indent=2), flush=True)
    if result.get("status") != "success":
        raise RuntimeError(f"Expected /detect status success, got {result.get('status')!r}")
    if result.get("device") != "cuda:0":
        raise RuntimeError(f"Expected /detect device cuda:0, got {result.get('device')!r}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and validate MMRotate for the GPU model in .env.")
    parser.add_argument("--gpu-model", help="Override GPU_MODEL from .env. Use 'auto' to read nvidia-smi.")
    parser.add_argument("--no-cache", action="store_true", help="Pass --no-cache to docker compose build.")
    parser.add_argument("--skip-build", action="store_true", help="Skip docker compose build; useful for validation only.")
    parser.add_argument("--up", action="store_true", help="Recreate and start inference-mmrotate after building.")
    parser.add_argument("--validate", type=Path, help="Sample TIFF/image path used for post-build /detect validation.")
    parser.add_argument("--chip-size", type=int, default=1024, help="Validation chip size extracted from large rasters.")
    parser.add_argument("--print-env", action="store_true", help="Print resolved build environment and exit.")
    parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    parser.add_argument("--detect-url", default=DEFAULT_DETECT_URL)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    file_env = read_env_file(ENV_PATH)
    gpu_model = args.gpu_model or file_env.get("GPU_MODEL") or os.environ.get("GPU_MODEL")
    if gpu_model == "auto":
        gpu_model = detect_gpu_model()
    if not gpu_model:
        raise SystemExit("GPU_MODEL is not set and no GPU was detected with nvidia-smi.")

    env = compose_env(gpu_model)
    resolved = {key: env[key] for key in sorted(env) if key.startswith("MMROTATE_") or key == "GPU_MODEL"}
    print(json.dumps({"resolved": resolved}, indent=2), flush=True)
    if args.print_env:
        return 0

    if not args.skip_build:
        build_command = ["docker", "compose", "build"]
        if args.no_cache:
            build_command.append("--no-cache")
        build_command.append("inference-mmrotate")
        run_command(build_command, env)

    if args.up and not args.skip_build:
        run_command(["docker", "compose", "up", "-d", "--force-recreate", "inference-mmrotate"], env)

    if args.validate:
        validate_service(args.health_url, args.detect_url, args.validate, args.chip_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

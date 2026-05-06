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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from gpu_profiles import GpuBuildProfile, resolve_gpu_profile


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


@dataclass(frozen=True)
class InferenceService:
    name: str
    env_prefix: str
    health_url: str
    detect_url: str
    expected_device: str = "cuda"


SERVICES: dict[str, InferenceService] = {
    "inference": InferenceService(
        name="inference",
        env_prefix="INFERENCE_",
        health_url="http://127.0.0.1:8002/health",
        detect_url="http://127.0.0.1:8002/detect",
    ),
    "inference-lae-dino": InferenceService(
        name="inference-lae-dino",
        env_prefix="LAE_DINO_",
        health_url="http://127.0.0.1:8004/health",
        detect_url="http://127.0.0.1:8004/detect",
    ),
    "inference-lsknet": InferenceService(
        name="inference-lsknet",
        env_prefix="LSKNET_",
        health_url="http://127.0.0.1:8006/health",
        detect_url="http://127.0.0.1:8006/detect",
    ),
    "inference-sam2": InferenceService(
        name="inference-sam2",
        env_prefix="SAM2_",
        health_url="http://127.0.0.1:8007/health",
        detect_url="http://127.0.0.1:8007/detect",
    ),
    "inference-mmrotate": InferenceService(
        name="inference-mmrotate",
        env_prefix="MMROTATE_",
        health_url="http://127.0.0.1:8005/health",
        detect_url="http://127.0.0.1:8005/detect",
    ),
}

DEFAULT_SERVICES = ["inference", "inference-lae-dino", "inference-lsknet", "inference-sam2"]


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
    return next((line.strip() for line in result.stdout.splitlines() if line.strip()), None)


def service_build_env(profile: GpuBuildProfile, service: InferenceService) -> dict[str, str]:
    env = profile.build_env(service.env_prefix)
    env[f"{service.env_prefix}GPU_PROFILE"] = profile.name
    return env


def compose_env(gpu_model: str, services: list[InferenceService]) -> dict[str, str]:
    profile = resolve_gpu_profile(gpu_model)
    env = os.environ.copy()
    env.update(read_env_file(ENV_PATH))
    env["GPU_MODEL"] = gpu_model
    for service in services:
        env.update(service_build_env(profile, service))
    return env


def run_command(command: Iterable[str], env: dict[str, str]) -> None:
    printable = " ".join(command)
    print(f"[inference-build] {printable}", flush=True)
    subprocess.run(list(command), check=True, cwd=ROOT, env=env)


def wait_for_health(service: InferenceService, timeout_s: int) -> dict:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(service.health_url, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, socket.timeout, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {service.name} health at {service.health_url}: {last_error}")


def health_uses_cuda(health: dict) -> bool:
    device = str(health.get("device") or "")
    if "cuda" in device:
        return True
    devices = health.get("devices")
    if isinstance(devices, list) and any("cuda" in str(device) for device in devices):
        return True
    return False


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

    output = Path(tempfile.gettempdir()) / f"inference_sample_chip_{sample_path.stem}_{chip_size}.png"
    Image.fromarray(image_array, mode="RGB").save(output)
    return output


def post_detection(service: InferenceService, image_path: Path, timeout_s: int) -> dict:
    boundary = "----inference-gpu-validation"
    metadata = json.dumps({"source": str(image_path), "validation": "gpu-smoke", "service": service.name})
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
        service.detect_url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


def validate_services(services: list[InferenceService], sample_path: Path, chip_size: int, timeout_s: int) -> None:
    chip_path = extract_sample_chip(sample_path, chip_size)
    print(f"[inference-build] validation chip: {chip_path}", flush=True)
    for service in services:
        health = wait_for_health(service, timeout_s=240)
        if not health_uses_cuda(health):
            raise RuntimeError(f"{service.name} is not using CUDA according to /health: {health.get('device')!r}")
        result = post_detection(service, chip_path, timeout_s=timeout_s)
        if result.get("status") != "success":
            raise RuntimeError(f"{service.name} /detect did not return success: {result.get('status')!r}")
        if "cuda" not in str(result.get("device") or health.get("device") or ""):
            raise RuntimeError(f"{service.name} /detect did not report CUDA: {result.get('device')!r}")
        print(
            json.dumps(
                {
                    "service": service.name,
                    "health_device": health.get("device"),
                    "detect_device": result.get("device"),
                    "detections": len(result.get("detections") or []),
                    "processing_time_ms": result.get("processing_time_ms"),
                    "gpu_model": health.get("gpu_model") or result.get("gpu_model"),
                    "gpu_profile": health.get("gpu_profile") or result.get("gpu_profile"),
                },
                indent=2,
            ),
            flush=True,
        )


def resolve_services(names: list[str]) -> list[InferenceService]:
    services: list[InferenceService] = []
    for name in names:
        if name == "all":
            services.extend(SERVICES[item] for item in DEFAULT_SERVICES)
            continue
        if name not in SERVICES:
            raise SystemExit(f"Unknown service {name!r}. Choices: {', '.join(sorted(SERVICES))}, all")
        services.append(SERVICES[name])
    deduped: list[InferenceService] = []
    seen: set[str] = set()
    for service in services:
        if service.name not in seen:
            deduped.append(service)
            seen.add(service.name)
    return deduped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and validate GPU inference services from GPU_MODEL.")
    parser.add_argument("--gpu-model", help="Override GPU_MODEL from .env. Use 'auto' to read nvidia-smi.")
    parser.add_argument("--services", nargs="+", default=["all"], help="Services to build/validate, or all.")
    parser.add_argument("--no-cache", action="store_true", help="Pass --no-cache to docker compose build.")
    parser.add_argument("--skip-build", action="store_true", help="Skip docker compose build.")
    parser.add_argument("--up", action="store_true", help="Recreate and start selected services after building.")
    parser.add_argument("--validate", type=Path, help="Sample TIFF/image path used for /detect validation.")
    parser.add_argument("--chip-size", type=int, default=1024, help="Validation chip size extracted from large rasters.")
    parser.add_argument("--detect-timeout", type=int, default=600, help="Seconds to allow each /detect request.")
    parser.add_argument("--print-env", action="store_true", help="Print resolved build environment and exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    services = resolve_services(args.services)
    file_env = read_env_file(ENV_PATH)
    gpu_model = args.gpu_model or file_env.get("GPU_MODEL") or os.environ.get("GPU_MODEL")
    if gpu_model == "auto":
        gpu_model = detect_gpu_model()
    if not gpu_model:
        raise SystemExit("GPU_MODEL is not set and no GPU was detected with nvidia-smi.")

    env = compose_env(gpu_model, services)
    resolved = {
        key: env[key]
        for key in sorted(env)
        if key == "GPU_MODEL" or any(key.startswith(service.env_prefix) for service in services)
    }
    print(json.dumps({"services": [service.name for service in services], "resolved": resolved}, indent=2), flush=True)
    if args.print_env:
        return 0

    service_names = [service.name for service in services]
    if not args.skip_build:
        build_command = ["docker", "compose", "build"]
        if args.no_cache:
            build_command.append("--no-cache")
        build_command.extend(service_names)
        run_command(build_command, env)

    if args.up and not args.skip_build:
        run_command(["docker", "compose", "up", "-d", "--force-recreate", *service_names], env)

    if args.validate:
        validate_services(services, args.validate, args.chip_size, args.detect_timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

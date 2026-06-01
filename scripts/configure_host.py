#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from gpu_profiles import GpuBuildProfile, resolve_gpu_profile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT / ".env"

BEGIN_MARKER = "# BEGIN SENTINEL GENERATED GPU CONFIG"
END_MARKER = "# END SENTINEL GENERATED GPU CONFIG"

SERVICE_PREFIXES = ("SAM3_",)

# Co-tenant detection / VRAM-ceiling tuning (see generated_env_values).
COTENANT_MIN_USED_MIB = 2048      # below this, the card is effectively idle (driver/Xorg only)
COTENANT_SAFETY_MIB = 4096        # slack left unallocated below the physical free memory
COTENANT_MIN_FRACTION = 0.20      # never cap the inference pool below this share of the card
COTENANT_EMBED_BATCH_SIZE = 16    # frugal DINOv3 crop batch when sharing a card
COTENANT_TEXT_CHUNK_SIZE = 8      # frugal SAM3 prompt chunk when sharing a card


@dataclass(frozen=True)
class HostGpu:
    name: str
    memory_mib: int = 0
    memory_used_mib: int = 0


@dataclass(frozen=True)
class HostGpuInfo:
    driver_version: str
    gpus: tuple[HostGpu, ...]


def parse_version(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value or "")
    return tuple(int(part) for part in parts)


def version_at_least(actual: str, minimum: str) -> bool:
    actual_parts = parse_version(actual)
    minimum_parts = parse_version(minimum)
    width = max(len(actual_parts), len(minimum_parts))
    return actual_parts + (0,) * (width - len(actual_parts)) >= minimum_parts + (0,) * (width - len(minimum_parts))


def parse_nvidia_smi_header(output: str) -> str:
    driver_match = re.search(r"Driver Version:\s*([0-9.]+)", output)
    if not driver_match:
        raise RuntimeError("Could not parse NVIDIA driver version from nvidia-smi output.")
    return driver_match.group(1)


def parse_gpu_query(output: str) -> tuple[HostGpu, ...]:
    gpus: list[HostGpu] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]

        def _mib(value: str) -> int:
            try:
                return int(re.sub(r"[^0-9]", "", value) or "0")
            except ValueError:
                return 0

        # Query format: "name, memory_total[, memory_used]". The third column
        # (used) lets configure_host detect a GPU co-tenant; one- and two-column
        # rows stay supported for older recordings/tests.
        if len(parts) == 1:
            gpus.append(HostGpu(name=parts[0]))
        elif len(parts) == 2:
            gpus.append(HostGpu(name=parts[0], memory_mib=_mib(parts[1])))
        elif len(parts) == 3:
            gpus.append(HostGpu(name=parts[0], memory_mib=_mib(parts[1]), memory_used_mib=_mib(parts[2])))
        else:
            raise RuntimeError(f"Unexpected nvidia-smi GPU query row: {line!r}")
    if not gpus:
        raise RuntimeError("nvidia-smi did not report any GPUs.")
    return tuple(gpus)


def detect_host_gpu_info() -> HostGpuInfo:
    try:
        header = subprocess.run(
            ["nvidia-smi"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        query = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
    except FileNotFoundError as exc:
        raise RuntimeError("nvidia-smi was not found. Install NVIDIA drivers/tooling before configuring GPU builds.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"nvidia-smi failed: {exc.stderr or exc.stdout}") from exc

    driver_version = parse_nvidia_smi_header(header)
    return HostGpuInfo(
        driver_version=driver_version,
        gpus=parse_gpu_query(query),
    )


def validate_driver(profile: GpuBuildProfile, driver_version: str) -> None:
    if not version_at_least(driver_version, profile.min_driver_version):
        raise RuntimeError(
            f"Host NVIDIA driver {driver_version} is too old for profile {profile.name} "
            f"(CUDA image {profile.cuda_version}); requires >= {profile.min_driver_version}. "
            "Update the host driver or use a lower CUDA/PyTorch profile."
        )


def sam3_build_env(profile: GpuBuildProfile) -> dict[str, str]:
    return profile.build_env(prefix="SAM3_")


def generated_env_values(info: HostGpuInfo) -> dict[str, str]:
    primary_gpu = info.gpus[0]
    profile = resolve_gpu_profile(primary_gpu.name)
    validate_driver(profile, info.driver_version)

    values = {
        "GPU_MODEL": primary_gpu.name,
        "NVIDIA_VISIBLE_DEVICES": "all",
        "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
    }

    for prefix in SERVICE_PREFIXES:
        values[f"{prefix}GPU_PROFILE"] = profile.name
    values.update(sam3_build_env(profile))

    # Runtime tuning knobs derived from the GPU profile + live VRAM.
    # Multiplex / TF32 / compile_video / FMV window sizing all flow from
    # `gpu_profiles.GpuBuildProfile.runtime_env(...)` so every host gets a
    # baseline matched to its architecture without code edits.
    values.update(profile.runtime_env(vram_mib=primary_gpu.memory_mib or None))

    # Multi-GPU chip dispatch (GPU-count derived, not VRAM/arch). The inference
    # service runs one model replica per visible GPU and serves /detect_raw
    # lock-free, so to actually use every GPU the worker's poster pool must be
    # at least as wide as the GPU count, and the adaptive back-off must never
    # drop below it (otherwise it collapses onto one GPU under latency variance).
    # The per-profile INFERENCE_CHIP_CONCURRENCY is a VRAM/arch baseline; raise
    # it to the GPU count when there are more GPUs than that baseline.
    gpu_count = len(info.gpus)
    profile_concurrency = int(values.get("INFERENCE_CHIP_CONCURRENCY", "1") or "1")
    values["INFERENCE_CHIP_CONCURRENCY"] = str(max(profile_concurrency, gpu_count))
    values["INFERENCE_MIN_PENDING_CHIPS"] = str(gpu_count)

    # Co-tenant VRAM ceiling (depends on live free VRAM, so it belongs here, not
    # in the per-arch profile). If another process already holds significant
    # memory on the cards at configure time — e.g. a vLLM server sharing the
    # GPUs — the inference replicas must not assume the whole card is theirs:
    # torch's free-memory estimate is blind to other tenants, so an unbounded
    # pool lets a fused cuBLAS/SDPA workspace alloc collide with the neighbour
    # and surface as a context-poisoning "illegal memory access" instead of a
    # clean OOM. We cap torch to the headroom that's actually free (minus a
    # safety margin) so over-budget allocs fail gracefully, and shrink the two
    # largest activation knobs so the per-chip peak stays inside the cap on the
    # common path (result-preserving — fewer crops/prompts per forward, identical
    # outputs). On dedicated cards (no co-tenant) nothing is emitted → no cap.
    # NOTE: run configure_host.py with co-tenants up but the Sentinel stack DOWN,
    # so memory.used reflects only the neighbour, not our own replicas.
    total_mib = primary_gpu.memory_mib or 0
    max_used_mib = max((g.memory_used_mib for g in info.gpus), default=0)
    if total_mib and max_used_mib >= COTENANT_MIN_USED_MIB:
        headroom_mib = total_mib - max_used_mib - COTENANT_SAFETY_MIB
        fraction = max(COTENANT_MIN_FRACTION, min(0.95, headroom_mib / total_mib))
        values["SAM3_GPU_MEMORY_FRACTION"] = f"{fraction:.2f}"
        embed = int(values.get("SAM3_EMBED_BATCH_SIZE", "32") or "32")
        chunk = int(values.get("SAM3_BATCHED_TEXT_CHUNK_SIZE", "8") or "8")
        values["SAM3_EMBED_BATCH_SIZE"] = str(min(embed, COTENANT_EMBED_BATCH_SIZE))
        values["SAM3_BATCHED_TEXT_CHUNK_SIZE"] = str(min(chunk, COTENANT_TEXT_CHUNK_SIZE))

    # Build flash-attn-3 + cc_torch into the inference image by default.
    # The runtime has a torch-SDPA fallback in sam3_runner.py if these
    # are missing, but fa3 is meaningfully faster on Ampere/Ada/Hopper
    # and SAM3's vitdet path expects it. Operators on memory-constrained
    # build hosts can override to 0 after running configure_host.py.
    values["SAM3_INSTALL_FAST_DEPS"] = "1"

    return values


def render_generated_block(values: dict[str, str]) -> str:
    lines = [
        BEGIN_MARKER,
        "# Generated by python scripts/configure_host.py. Do not edit this block by hand.",
    ]
    for key in sorted(values):
        lines.append(f"{key}={values[key]}")
    lines.append(END_MARKER)
    return "\n".join(lines)


def replace_generated_block(existing: str, block: str) -> str:
    generated_keys = {
        line.split("=", 1)[0]
        for line in block.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    pattern = re.compile(
        rf"(?:^|\n){re.escape(BEGIN_MARKER)}\n.*?\n{re.escape(END_MARKER)}(?:\n|$)",
        re.DOTALL,
    )
    without_block = pattern.sub("\n", existing)
    preserved_lines = []
    for line in without_block.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in generated_keys:
            continue
        preserved_lines.append(line)
    stripped = "\n".join(preserved_lines).rstrip()
    if pattern.search(existing):
        return (stripped + "\n\n" + block).strip() + "\n"
    if stripped:
        return stripped + "\n\n" + block + "\n"
    return block + "\n"


def configure_env_file(env_path: Path, info: HostGpuInfo) -> dict[str, str]:
    values = generated_env_values(info)
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    env_path.write_text(replace_generated_block(existing, render_generated_block(values)), encoding="utf-8")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect host GPU/driver and write Sentinel Docker build settings to .env.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH, help="Path to .env to update.")
    parser.add_argument("--dry-run", action="store_true", help="Print generated settings without writing .env.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    info = detect_host_gpu_info()
    values = generated_env_values(info)
    block = render_generated_block(values)
    if args.dry_run:
        print(block)
        return 0

    existing = args.env_file.read_text(encoding="utf-8") if args.env_file.exists() else ""
    args.env_file.write_text(replace_generated_block(existing, block), encoding="utf-8")
    print(
        f"Wrote GPU config for {values['GPU_MODEL']} "
        f"({values['SAM3_GPU_PROFILE']}, driver {info.driver_version}) to {args.env_file}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from configure_host import (  # noqa: E402
    BEGIN_MARKER,
    END_MARKER,
    HostGpu,
    HostGpuInfo,
    generated_env_values,
    parse_gpu_query,
    parse_nvidia_smi_header,
    replace_generated_block,
)


def test_parse_nvidia_smi_header_extracts_driver_and_cuda():
    output = "NVIDIA-SMI 550.163.01 Driver Version: 550.163.01 CUDA Version: 12.4"

    assert parse_nvidia_smi_header(output) == "550.163.01"


def test_parse_gpu_query_extracts_gpu_rows():
    gpus = parse_gpu_query("NVIDIA A100 80GB PCIe\nNVIDIA A100 80GB PCIe\n")

    assert len(gpus) == 2
    assert gpus[0].name == "NVIDIA A100 80GB PCIe"


def test_a100_compatible_driver_generates_datacenter_profile_values():
    """A100 → ampere_sm80_86_datacenter profile (cu130 / torch 2.10.0).

    Profile minimum driver is 575.51 (set when the profile was upgraded to
    cu130). The test driver here exceeds that so the call succeeds.
    """
    info = HostGpuInfo(
        driver_version="595.58.03",
        gpus=(HostGpu("NVIDIA A100 80GB PCIe"),),
    )

    values = generated_env_values(info)

    assert values["GPU_MODEL"] == "NVIDIA A100 80GB PCIe"
    assert values["NVIDIA_VISIBLE_DEVICES"] == "all"
    assert values["NVIDIA_DRIVER_CAPABILITIES"] == "compute,utility"
    assert values["SAM3_GPU_PROFILE"] == "ampere_sm80_86_datacenter"
    assert values["SAM3_CUDA_VERSION"] == "13.2.0"
    assert values["SAM3_UBUNTU_VERSION"] == "24.04"
    assert values["SAM3_TORCH_INDEX_URL"].endswith("/cu130")
    assert values["SAM3_TORCH_VERSION"] == "2.10.0+cu130"
    assert values["SAM3_TORCHVISION_VERSION"] == "0.25.0+cu130"
    # cu130-stack profile DOES set torchaudio (older cu126 stack did not).
    assert values["SAM3_TORCHAUDIO_VERSION"] == "2.10.0+cu130"


def test_multi_gpu_scales_chip_dispatch_to_gpu_count():
    """A 4-GPU host feeds one replica per GPU: concurrency >= GPU count and the
    adaptive back-off floor == GPU count. VRAM tier sets the embed batch size."""
    info = HostGpuInfo(
        driver_version="595.58.03",
        gpus=tuple(HostGpu("NVIDIA A100-SXM4-80GB", memory_mib=81920) for _ in range(4)),
    )

    values = generated_env_values(info)

    assert values["INFERENCE_MIN_PENDING_CHIPS"] == "4"
    # datacenter profile baseline is 2; raised to the GPU count (4).
    assert int(values["INFERENCE_CHIP_CONCURRENCY"]) >= 4
    # datacenter Ampere VRAM tier.
    assert values["SAM3_EMBED_BATCH_SIZE"] == "64"


def test_single_gpu_keeps_profile_chip_dispatch():
    """A single consumer GPU keeps the profile baseline (no GPU-count bump)."""
    info = HostGpuInfo(
        driver_version="595.58.03",
        gpus=(HostGpu("NVIDIA GeForce RTX 3090", memory_mib=24576),),
    )

    values = generated_env_values(info)

    assert values["INFERENCE_MIN_PENDING_CHIPS"] == "1"
    assert values["INFERENCE_CHIP_CONCURRENCY"] == "1"
    assert values["SAM3_EMBED_BATCH_SIZE"] == "32"


def test_parse_gpu_query_extracts_total_and_used_columns():
    gpus = parse_gpu_query("NVIDIA A100 80GB PCIe, 81920, 41424\n")

    assert len(gpus) == 1
    assert gpus[0].memory_mib == 81920
    assert gpus[0].memory_used_mib == 41424


def test_cotenant_usage_caps_memory_fraction_and_shrinks_batches():
    """A co-tenant (e.g. vLLM) already holding ~40 GiB/card at configure time
    → cap the inference pool to the free headroom and shrink the peak knobs."""
    info = HostGpuInfo(
        driver_version="595.58.03",
        gpus=tuple(
            HostGpu("NVIDIA A100 80GB PCIe", memory_mib=81920, memory_used_mib=41424)
            for _ in range(4)
        ),
    )

    values = generated_env_values(info)

    # headroom = 81920 - 41424 - 4096 = 36400 → 36400/81920 = 0.44
    assert values["SAM3_GPU_MEMORY_FRACTION"] == "0.44"
    # datacenter baseline 64 / 16 shrinks to the frugal co-tenant values.
    assert values["SAM3_EMBED_BATCH_SIZE"] == "16"
    assert values["SAM3_BATCHED_TEXT_CHUNK_SIZE"] == "8"


def test_dedicated_card_emits_no_memory_cap():
    """No co-tenant (cards idle at configure time) → no cap, full batch sizes."""
    info = HostGpuInfo(
        driver_version="595.58.03",
        gpus=tuple(
            HostGpu("NVIDIA A100 80GB PCIe", memory_mib=81920, memory_used_mib=312)
            for _ in range(4)
        ),
    )

    values = generated_env_values(info)

    assert "SAM3_GPU_MEMORY_FRACTION" not in values
    assert values["SAM3_EMBED_BATCH_SIZE"] == "64"


def test_blackwell_compatible_driver_generates_cu130_values():
    """RTX 5070 Ti → blackwell_sm120 profile (cu130 / torch 2.10.0)."""
    info = HostGpuInfo(
        driver_version="595.58.03",
        gpus=(HostGpu("NVIDIA GeForce RTX 5070 Ti"),),
    )

    values = generated_env_values(info)

    assert values["SAM3_GPU_PROFILE"] == "blackwell_sm120"
    assert values["SAM3_CUDA_VERSION"] == "13.2.0"
    assert values["SAM3_UBUNTU_VERSION"] == "24.04"
    assert values["SAM3_TORCH_INDEX_URL"].endswith("/cu130")
    assert values["SAM3_TORCH_VERSION"] == "2.10.0+cu130"


def test_blackwell_incompatible_driver_fails_fast():
    info = HostGpuInfo(
        driver_version="550.163.01",
        gpus=(HostGpu("NVIDIA GeForce RTX 5070 Ti"),),
    )

    with pytest.raises(RuntimeError, match="too old for profile blackwell_sm120"):
        generated_env_values(info)


def test_replace_generated_block_preserves_unrelated_keys_and_is_idempotent():
    original = (
        "GPU_MODEL=stale manual value\n"
        "POSTGIS_URI=postgresql://example\n"
        "OPENAI_API_BASE=http://host.docker.internal:8000/v1\n"
    )
    block_v1 = f"{BEGIN_MARKER}\nGPU_MODEL=NVIDIA A100 80GB PCIe\n{END_MARKER}"
    block_v2 = f"{BEGIN_MARKER}\nGPU_MODEL=NVIDIA L40S\n{END_MARKER}"

    once = replace_generated_block(original, block_v1)
    twice = replace_generated_block(once, block_v1)
    replaced = replace_generated_block(twice, block_v2)

    assert once == twice
    assert "POSTGIS_URI=postgresql://example" in replaced
    assert "OPENAI_API_BASE=http://host.docker.internal:8000/v1" in replaced
    assert "GPU_MODEL=NVIDIA L40S" in replaced
    assert "GPU_MODEL=NVIDIA A100 80GB PCIe" not in replaced
    assert "GPU_MODEL=stale manual value" not in replaced

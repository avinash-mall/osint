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


def test_a100_driver_550_generates_sam3_cu126_torch_values():
    info = HostGpuInfo(
        driver_version="550.163.01",
        gpus=(HostGpu("NVIDIA A100 80GB PCIe"),),
    )

    values = generated_env_values(info)

    assert values["GPU_MODEL"] == "NVIDIA A100 80GB PCIe"
    assert values["NVIDIA_VISIBLE_DEVICES"] == "all"
    assert values["NVIDIA_DRIVER_CAPABILITIES"] == "compute,utility"
    assert "SAM3_TORCHAUDIO_VERSION" not in values
    assert "GPU_DRIVER_VERSION" not in values
    assert values["SAM3_GPU_PROFILE"] == "ampere_sm80_86"
    assert values["SAM3_CUDA_VERSION"] == "12.4.1"
    assert values["SAM3_UBUNTU_VERSION"] == "22.04"
    assert values["SAM3_TORCH_INDEX_URL"].endswith("/cu126")
    assert values["SAM3_TORCH_VERSION"] == "2.7.1"
    assert values["SAM3_TORCHVISION_VERSION"] == "0.22.1"


def test_blackwell_compatible_driver_generates_cu128_values():
    info = HostGpuInfo(
        driver_version="570.86.10",
        gpus=(HostGpu("NVIDIA GeForce RTX 5070 Ti"),),
    )

    values = generated_env_values(info)

    assert values["SAM3_GPU_PROFILE"] == "blackwell_sm120"
    assert values["SAM3_CUDA_VERSION"] == "12.8.1"
    assert values["SAM3_UBUNTU_VERSION"] == "24.04"
    assert values["SAM3_TORCH_INDEX_URL"].endswith("/cu128")
    assert values["SAM3_TORCH_VERSION"] == "2.7.1+cu128"


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

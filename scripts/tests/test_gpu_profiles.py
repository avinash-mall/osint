from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gpu_profiles import UnsupportedGpuError, normalize_gpu_model, resolve_gpu_profile  # noqa: E402


def test_normalize_gpu_model_removes_marks_and_extra_space():
    assert normalize_gpu_model(" NVIDIA GeForce RTX™ 5070 Ti  ") == "nvidia geforce rtx 5070 ti"


def test_resolve_rtx_5070_ti_uses_blackwell_profile():
    profile = resolve_gpu_profile("NVIDIA GeForce RTX 5070 Ti")

    assert profile.name == "blackwell_sm120"
    assert profile.cuda_version == "12.8.1"
    assert profile.torch_index_url.endswith("/cu128")
    assert profile.torch_version == "2.7.1+cu128"
    assert profile.torchvision_version == "0.22.1+cu128"
    assert profile.torchaudio_version == "2.7.1+cu128"
    assert "12.0+PTX" in profile.torch_cuda_arch_list


def test_resolve_common_datacenter_gpu():
    profile = resolve_gpu_profile("NVIDIA L40S")

    assert profile.name == "ada_sm89"
    assert "8.9" in profile.torch_cuda_arch_list


def test_resolve_a100_pcie_uses_ampere_profile():
    profile = resolve_gpu_profile("NVIDIA A100 80GB PCIe")

    assert profile.name == "ampere_sm80_86"
    assert profile.cuda_version == "12.4.1"
    assert profile.torch_index_url.endswith("/cu124")
    assert profile.torch_version == "2.6.0+cu124"
    assert profile.torchvision_version == "0.21.0+cu124"
    assert profile.torchaudio_version == "2.6.0+cu124"
    assert "8.0" in profile.torch_cuda_arch_list


def test_resolve_blackwell_datacenter_gpu():
    profile = resolve_gpu_profile("NVIDIA B200")

    assert profile.name == "blackwell_sm100"
    assert profile.compute_capability == "10.0"


def test_rejects_known_gpu_below_supported_floor():
    with pytest.raises(UnsupportedGpuError, match="below the supported MMRotate GPU build floor"):
        resolve_gpu_profile("NVIDIA Tesla V100")


def test_rejects_unknown_gpu_model():
    with pytest.raises(ValueError, match="Unsupported or unknown GPU_MODEL"):
        resolve_gpu_profile("NVIDIA Mystery Accelerator")

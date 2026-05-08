from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


class UnsupportedGpuError(ValueError):
    """Raised when a GPU model is known but below the supported inference floor."""


@dataclass(frozen=True)
class GpuBuildProfile:
    name: str
    cuda_version: str
    torch_index_url: str
    torch_version: str
    torchvision_version: str
    torchaudio_version: str
    torch_cuda_arch_list: str
    compute_capability: str
    min_driver_version: str

    def build_env(self, prefix: str = "SAM3_") -> dict[str, str]:
        return {
            f"{prefix}CUDA_VERSION": self.cuda_version,
            f"{prefix}TORCH_INDEX_URL": self.torch_index_url,
            f"{prefix}TORCH_VERSION": self.torch_version,
            f"{prefix}TORCHVISION_VERSION": self.torchvision_version,
            f"{prefix}TORCHAUDIO_VERSION": self.torchaudio_version,
            f"{prefix}TORCH_CUDA_ARCH_LIST": self.torch_cuda_arch_list,
        }


GPU_BUILD_PROFILES: dict[str, GpuBuildProfile] = {
    "turing_sm75": GpuBuildProfile(
        name="turing_sm75",
        cuda_version="12.4.1",
        torch_index_url="https://download.pytorch.org/whl/cu124",
        torch_version="2.6.0+cu124",
        torchvision_version="0.21.0+cu124",
        torchaudio_version="2.6.0+cu124",
        torch_cuda_arch_list="7.5;8.0;8.6;8.9;9.0+PTX",
        compute_capability="7.5",
        min_driver_version="550.54.14",
    ),
    "ampere_sm80_86": GpuBuildProfile(
        name="ampere_sm80_86",
        cuda_version="12.4.1",
        torch_index_url="https://download.pytorch.org/whl/cu124",
        torch_version="2.6.0+cu124",
        torchvision_version="0.21.0+cu124",
        torchaudio_version="2.6.0+cu124",
        torch_cuda_arch_list="8.0;8.6;8.9;9.0+PTX",
        compute_capability="8.x",
        min_driver_version="550.54.14",
    ),
    "ada_sm89": GpuBuildProfile(
        name="ada_sm89",
        cuda_version="12.4.1",
        torch_index_url="https://download.pytorch.org/whl/cu124",
        torch_version="2.6.0+cu124",
        torchvision_version="0.21.0+cu124",
        torchaudio_version="2.6.0+cu124",
        torch_cuda_arch_list="8.9;9.0+PTX",
        compute_capability="8.9",
        min_driver_version="550.54.14",
    ),
    "hopper_sm90": GpuBuildProfile(
        name="hopper_sm90",
        cuda_version="12.4.1",
        torch_index_url="https://download.pytorch.org/whl/cu124",
        torch_version="2.6.0+cu124",
        torchvision_version="0.21.0+cu124",
        torchaudio_version="2.6.0+cu124",
        torch_cuda_arch_list="9.0+PTX",
        compute_capability="9.0",
        min_driver_version="550.54.14",
    ),
    "blackwell_sm100": GpuBuildProfile(
        name="blackwell_sm100",
        cuda_version="12.8.1",
        torch_index_url="https://download.pytorch.org/whl/cu128",
        torch_version="2.7.1+cu128",
        torchvision_version="0.22.1+cu128",
        torchaudio_version="2.7.1+cu128",
        torch_cuda_arch_list="9.0;10.0;12.0+PTX",
        compute_capability="10.0",
        min_driver_version="570.26",
    ),
    "blackwell_sm120": GpuBuildProfile(
        name="blackwell_sm120",
        cuda_version="12.8.1",
        torch_index_url="https://download.pytorch.org/whl/cu128",
        torch_version="2.7.1+cu128",
        torchvision_version="0.22.1+cu128",
        torchaudio_version="2.7.1+cu128",
        torch_cuda_arch_list="8.0;8.6;8.9;9.0;12.0+PTX",
        compute_capability="12.0",
        min_driver_version="570.26",
    ),
}


GPU_MODELS: Mapping[str, str] = {
    # NVIDIA Turing.
    "nvidia tesla t4": "turing_sm75",
    "tesla t4": "turing_sm75",
    "nvidia t4": "turing_sm75",
    "nvidia quadro rtx 4000": "turing_sm75",
    "nvidia quadro rtx 5000": "turing_sm75",
    "nvidia quadro rtx 6000": "turing_sm75",
    "nvidia quadro rtx 8000": "turing_sm75",
    "nvidia geforce rtx 2060": "turing_sm75",
    "nvidia geforce rtx 2070": "turing_sm75",
    "nvidia geforce rtx 2070 super": "turing_sm75",
    "nvidia geforce rtx 2080": "turing_sm75",
    "nvidia geforce rtx 2080 super": "turing_sm75",
    "nvidia geforce rtx 2080 ti": "turing_sm75",
    # NVIDIA Ampere.
    "nvidia a10": "ampere_sm80_86",
    "nvidia a10g": "ampere_sm80_86",
    "nvidia a30": "ampere_sm80_86",
    "nvidia a40": "ampere_sm80_86",
    "nvidia a100": "ampere_sm80_86",
    "nvidia a100 40gb pcie": "ampere_sm80_86",
    "nvidia a100 80gb pcie": "ampere_sm80_86",
    "nvidia a100-pcie-40gb": "ampere_sm80_86",
    "nvidia a100-pcie-80gb": "ampere_sm80_86",
    "nvidia a100-sxm4-40gb": "ampere_sm80_86",
    "nvidia a100-sxm4-80gb": "ampere_sm80_86",
    "nvidia geforce rtx 3050": "ampere_sm80_86",
    "nvidia geforce rtx 3060": "ampere_sm80_86",
    "nvidia geforce rtx 3060 ti": "ampere_sm80_86",
    "nvidia geforce rtx 3070": "ampere_sm80_86",
    "nvidia geforce rtx 3070 ti": "ampere_sm80_86",
    "nvidia geforce rtx 3080": "ampere_sm80_86",
    "nvidia geforce rtx 3080 ti": "ampere_sm80_86",
    "nvidia geforce rtx 3090": "ampere_sm80_86",
    "nvidia geforce rtx 3090 ti": "ampere_sm80_86",
    "nvidia rtx a2000": "ampere_sm80_86",
    "nvidia rtx a4000": "ampere_sm80_86",
    "nvidia rtx a4500": "ampere_sm80_86",
    "nvidia rtx a5000": "ampere_sm80_86",
    "nvidia rtx a6000": "ampere_sm80_86",
    # NVIDIA Ada.
    "nvidia l4": "ada_sm89",
    "nvidia l40": "ada_sm89",
    "nvidia l40s": "ada_sm89",
    "nvidia rtx 4000 ada generation": "ada_sm89",
    "nvidia rtx 4500 ada generation": "ada_sm89",
    "nvidia rtx 5000 ada generation": "ada_sm89",
    "nvidia rtx 6000 ada generation": "ada_sm89",
    "nvidia geforce rtx 4060": "ada_sm89",
    "nvidia geforce rtx 4060 ti": "ada_sm89",
    "nvidia geforce rtx 4070": "ada_sm89",
    "nvidia geforce rtx 4070 super": "ada_sm89",
    "nvidia geforce rtx 4070 ti": "ada_sm89",
    "nvidia geforce rtx 4070 ti super": "ada_sm89",
    "nvidia geforce rtx 4080": "ada_sm89",
    "nvidia geforce rtx 4080 super": "ada_sm89",
    "nvidia geforce rtx 4090": "ada_sm89",
    # NVIDIA Hopper.
    "nvidia h100": "hopper_sm90",
    "nvidia h100 80gb hbm3": "hopper_sm90",
    "nvidia h100 nvl": "hopper_sm90",
    "nvidia h200": "hopper_sm90",
    # NVIDIA Blackwell.
    "nvidia b200": "blackwell_sm100",
    "nvidia gb200": "blackwell_sm100",
    "nvidia geforce rtx 5060": "blackwell_sm120",
    "nvidia geforce rtx 5060 ti": "blackwell_sm120",
    "nvidia geforce rtx 5070": "blackwell_sm120",
    "nvidia geforce rtx 5070 ti": "blackwell_sm120",
    "nvidia geforce rtx 5080": "blackwell_sm120",
    "nvidia geforce rtx 5090": "blackwell_sm120",
}

UNSUPPORTED_GPU_MODELS: Mapping[str, str] = {
    "nvidia geforce gtx 1080": "sm_61",
    "nvidia geforce gtx 1080 ti": "sm_61",
    "nvidia tesla p100": "sm_60",
    "nvidia tesla v100": "sm_70",
}


def normalize_gpu_model(value: str) -> str:
    normalized = value.strip().lower()
    normalized = normalized.replace("(r)", "").replace("(tm)", "")
    normalized = normalized.replace("™", "").replace("®", "")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def resolve_gpu_profile(gpu_model: str) -> GpuBuildProfile:
    normalized = normalize_gpu_model(gpu_model)
    if not normalized:
        raise ValueError("GPU_MODEL is empty. Set GPU_MODEL in .env or pass --gpu-model.")
    if normalized in UNSUPPORTED_GPU_MODELS:
        arch = UNSUPPORTED_GPU_MODELS[normalized]
        raise UnsupportedGpuError(
            f"{gpu_model!r} ({arch}) is below the supported SAM3 GPU build floor. "
            "Use a Turing/sm_75 or newer NVIDIA GPU."
        )
    profile_name = GPU_MODELS.get(normalized)
    if profile_name is None:
        raise ValueError(
            f"Unsupported or unknown GPU_MODEL {gpu_model!r}. Add it to scripts/gpu_profiles.py "
            "with its compute capability and build profile."
        )
    return GPU_BUILD_PROFILES[profile_name]

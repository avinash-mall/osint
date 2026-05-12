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

    # ------------------------------------------------------------------
    # Runtime-tuning defaults for the inference / worker containers.
    # These get written into the generated `.env` block by
    # `scripts/configure_host.py` so each host (T4, RTX 5070 Ti, H100, …)
    # picks up the appropriate values without code changes. Operators can
    # still override any of them per-host in `.env` after generation.
    # ------------------------------------------------------------------
    # TF32 matmul: supported sm_80 and above (Ampere/Ada/Hopper/Blackwell).
    enable_tf32: bool = True
    # Object Multiplex: requires both the profile to permit it AND the live
    # VRAM headroom check at runtime to pass (~20 GiB free at model-load
    # time on the installed SAM3 build). The profile flag is the *policy*;
    # the runtime VRAM gate is the *measurement*.
    use_multiplex: bool = False
    # torch.compile() of the video predictor — risky default-on because
    # SAM3's branchy text/box paths sometimes trip the compiler; only
    # enable on datacenter cards where the win is worth the failure mode.
    compile_video: bool = False
    # SAM3 video session sizing — the prep-clip height (px) and the max
    # number of frames per SAM3 session. Smaller GPUs need smaller windows
    # to fit decoded-frame tensors + activations during propagation.
    fmv_track_height: int = 540
    fmv_track_frames_per_window: int = 48

    def build_env(self, prefix: str = "SAM3_") -> dict[str, str]:
        return {
            f"{prefix}CUDA_VERSION": self.cuda_version,
            f"{prefix}TORCH_INDEX_URL": self.torch_index_url,
            f"{prefix}TORCH_VERSION": self.torch_version,
            f"{prefix}TORCHVISION_VERSION": self.torchvision_version,
            f"{prefix}TORCHAUDIO_VERSION": self.torchaudio_version,
            f"{prefix}TORCH_CUDA_ARCH_LIST": self.torch_cuda_arch_list,
        }

    def runtime_env(self, vram_mib: int | None = None) -> dict[str, str]:
        """Profile-driven runtime knobs, written into .env by configure_host.

        ``vram_mib`` is the live `nvidia-smi --query-gpu=memory.total` value;
        passed in so we can gate multiplex on actual hardware regardless of
        what the profile permits (e.g. a profile that says
        ``use_multiplex=True`` will still emit ``SAM3_USE_MULTIPLEX=0`` on
        an undersized card)."""
        multiplex_ok = self.use_multiplex and (vram_mib is None or vram_mib >= 20_000)
        env: dict[str, str] = {
            "SAM3_ENABLE_TF32": "1" if self.enable_tf32 else "0",
            "SAM3_USE_MULTIPLEX": "1" if multiplex_ok else "0",
            "SAM3_COMPILE_VIDEO": "1" if self.compile_video else "0",
            "FMV_TRACK_HEIGHT": str(self.fmv_track_height),
            "FMV_TRACK_FRAMES_PER_WINDOW": str(self.fmv_track_frames_per_window),
        }
        if vram_mib is not None:
            env["SAM3_GPU_VRAM_GIB"] = f"{vram_mib / 1024:.1f}"
        return env


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
        # sm_75 has no native TF32 tensor cores; smaller working set fits 16 GiB T4.
        enable_tf32=False,
        use_multiplex=False,
        compile_video=False,
        fmv_track_height=360,
        fmv_track_frames_per_window=24,
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
        # Ampere = TF32 capable. Multiplex permitted at profile level; the
        # runtime VRAM gate in configure_host downgrades to base predictor
        # on Ampere cards with < 20 GiB (e.g. RTX 3080/3090 16-24 GiB).
        enable_tf32=True,
        use_multiplex=True,
        compile_video=False,
        fmv_track_height=540,
        fmv_track_frames_per_window=48,
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
        enable_tf32=True,
        use_multiplex=True,
        compile_video=False,
        fmv_track_height=540,
        fmv_track_frames_per_window=48,
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
        # H100 / H200: 80 GiB+ datacenter cards; multiplex + compile pay off.
        enable_tf32=True,
        use_multiplex=True,
        compile_video=True,
        fmv_track_height=720,
        fmv_track_frames_per_window=96,
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
        # B100 / B200 datacenter Blackwell — same generous budget as Hopper.
        enable_tf32=True,
        use_multiplex=True,
        compile_video=True,
        fmv_track_height=720,
        fmv_track_frames_per_window=96,
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
        # Consumer Blackwell (RTX 5070/5080/5090). TF32 = yes. Multiplex
        # permitted at profile level but the runtime VRAM check will gate
        # off on 16 GiB cards (RTX 5070 Ti) and gate on for 24+ GiB cards.
        # compile_video stays off — SAM3's branchy paths still trip the
        # compiler on this arch in the installed build.
        enable_tf32=True,
        use_multiplex=True,
        compile_video=False,
        fmv_track_height=540,
        fmv_track_frames_per_window=48,
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

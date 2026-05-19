"""Unit tests for inference_utils — no GPU required.

The OOM retry path is tested by injecting a fake torch.cuda.OutOfMemoryError
on the first call; the optimization helpers are tested by verifying they
no-op safely when given a non-cuda or non-Ultralytics model.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure inference-sam3 root is importable regardless of cwd.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from inference_utils import (  # noqa: E402
    apply_yolo_optimizations,
    safe_predict,
)


class _FakeOOMError(Exception):
    """Stand-in for torch.cuda.OutOfMemoryError to avoid CUDA dependency."""


def test_safe_predict_returns_value_on_success():
    model = MagicMock()
    model.predict.return_value = ["ok"]
    fn = lambda: model.predict()
    assert safe_predict(fn, on_oom=lambda: None, oom_types=(_FakeOOMError,)) == ["ok"]
    assert model.predict.call_count == 1


def test_safe_predict_retries_on_oom_once():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _FakeOOMError("simulated OOM")
        return "recovered"

    cleanups = {"n": 0}

    def on_oom():
        cleanups["n"] += 1

    result = safe_predict(fn, on_oom=on_oom, oom_types=(_FakeOOMError,))
    assert result == "recovered"
    assert calls["n"] == 2
    assert cleanups["n"] == 1


def test_safe_predict_gives_up_after_max_retries():
    def fn():
        raise _FakeOOMError("persistent OOM")

    with pytest.raises(_FakeOOMError):
        safe_predict(fn, on_oom=lambda: None, oom_types=(_FakeOOMError,), max_retries=2)


def test_safe_predict_returns_fallback_when_provided():
    def fn():
        raise _FakeOOMError("persistent OOM")

    result = safe_predict(
        fn,
        on_oom=lambda: None,
        oom_types=(_FakeOOMError,),
        max_retries=1,
        fallback=lambda: "fallback_value",
    )
    assert result == "fallback_value"


def test_apply_yolo_optimizations_no_op_on_none():
    # Passing None must not raise — used to be a footgun.
    apply_yolo_optimizations(None, half=True, fuse=True, channels_last=True)


def test_apply_yolo_optimizations_no_op_when_all_flags_false():
    model = MagicMock()
    apply_yolo_optimizations(model, half=False, fuse=False, channels_last=False)
    model.fuse.assert_not_called()
    model.half.assert_not_called()


def test_apply_yolo_optimizations_calls_fuse_and_half():
    model = MagicMock()
    apply_yolo_optimizations(model, half=True, fuse=True, channels_last=False)
    model.fuse.assert_called_once()
    model.half.assert_called_once()


def test_apply_yolo_optimizations_swallows_fuse_failure():
    # Some YOLOE variants raise on .fuse() — should not abort the optimization
    # pipeline; remaining flags should still apply.
    model = MagicMock()
    model.fuse.side_effect = RuntimeError("fuse not supported")
    apply_yolo_optimizations(model, half=True, fuse=True, channels_last=False)
    model.half.assert_called_once()


def test_gpu_profile_emits_yolo_flags():
    """Verify the new fields are wired through GpuBuildProfile.runtime_env."""
    import importlib.util

    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    spec = importlib.util.spec_from_file_location(
        "gpu_profiles_test_mod",
        scripts_dir / "gpu_profiles.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before exec so the dataclass machinery (which looks up
    # cls.__module__ in sys.modules during _is_type introspection on
    # Python 3.14+) can resolve typing references.
    sys.modules["gpu_profiles_test_mod"] = mod
    spec.loader.exec_module(mod)

    ampere = mod.GPU_BUILD_PROFILES["ampere_sm80_86"]
    env = ampere.runtime_env(vram_mib=24576)
    assert env["SAM3_YOLO_HALF"] == "1"
    assert env["SAM3_YOLO_FUSE"] == "1"
    assert env["SAM3_YOLO_CHANNELS_LAST"] == "1"
    assert env["SAM3_CUDNN_BENCHMARK"] == "1"

    turing = mod.GPU_BUILD_PROFILES["turing_sm75"]
    env_t = turing.runtime_env(vram_mib=16384)
    assert env_t["SAM3_YOLO_HALF"] == "0"
    assert env_t["SAM3_YOLO_CHANNELS_LAST"] == "0"
    assert env_t["SAM3_CUDNN_BENCHMARK"] == "0"
    assert env_t["SAM3_YOLO_FUSE"] == "1"


def test_sam3_perf_knobs_per_profile():
    """Pin the expected SAM3 perf-knob matrix across every GPU profile.

    Catches accidental drift when adding new arch profiles or rebalancing
    VRAM tiers — every profile must explicitly answer:

      * SAM3_NATIVE_BF16        — currently 0 everywhere (deferred due to
                                  upstream fp32 buffers in geometry encoder).
      * SAM3_SDPA_BACKEND       — "efficient" on Turing (no FA), "flash"
                                  everywhere else (PyTorch picks best).
      * SAM3_DECODER_TOPK       — 32 for ≤32 GiB profiles, 64 for ≥40 GiB
                                  datacenter profiles.
      * SAM3_COMPILE_VISION_ENCODER — only sm_90/sm_100 datacenter (Hopper +
                                  Blackwell datacenter) where compile path
                                  is stable.
      * SAM3_BATCHED_TEXT_CHUNK_SIZE — VRAM-tiered: 4 (T4), 8 (consumer
                                  Ampere/Ada/Blackwell), 16 (Ampere
                                  datacenter), 32 (Hopper, datacenter
                                  Blackwell).
    """
    import importlib.util

    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    spec = importlib.util.spec_from_file_location(
        "gpu_profiles_perf_test_mod",
        scripts_dir / "gpu_profiles.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["gpu_profiles_perf_test_mod"] = mod
    spec.loader.exec_module(mod)

    expected = {
        # name                          bf16 sdpa         topk compile chunk
        "turing_sm75":                  ("0", "efficient", "32", "0", "4"),
        "ampere_sm80_86":               ("0", "flash",     "32", "0", "8"),
        "ampere_sm80_86_datacenter":    ("0", "flash",     "64", "0", "16"),
        "ada_sm89":                     ("0", "flash",     "32", "0", "8"),
        "hopper_sm90":                  ("0", "flash",     "64", "1", "32"),
        "blackwell_sm100":              ("0", "flash",     "64", "1", "32"),
        "blackwell_sm120":              ("0", "flash",     "32", "0", "8"),
    }

    for name, (bf16, sdpa, topk, compile_ve, chunk) in expected.items():
        profile = mod.GPU_BUILD_PROFILES[name]
        env = profile.runtime_env(vram_mib=16384)
        assert env["SAM3_NATIVE_BF16"] == bf16, f"{name}: bf16"
        assert env["SAM3_SDPA_BACKEND"] == sdpa, f"{name}: sdpa"
        assert env["SAM3_DECODER_TOPK"] == topk, f"{name}: topk"
        assert env["SAM3_COMPILE_VISION_ENCODER"] == compile_ve, f"{name}: compile_ve"
        assert env["SAM3_BATCHED_TEXT_CHUNK_SIZE"] == chunk, f"{name}: chunk"

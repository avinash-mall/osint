"""Unit tests for the poisoned-CUDA-context self-heal backstop.

A `cudaErrorIllegalAddress` poisons the whole process context, so the next
request usually dies *outside* the text-chunk loop (in encode_image, the
batched forward, a specialist, or embedding) and escapes the per-chunk
self-heal. `main._detect_pipeline_guarded` is the boundary that catches a
poisoned-context error from any GPU path and os._exit(1)s instead of letting
the container serve 500s forever.

See docs/decisions/why-exit-on-poisoned-cuda-context.md.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Stub heavy optional deps so `import main` works on a CPU-only test host.
if "psutil" not in sys.modules:
    sys.modules["psutil"] = types.SimpleNamespace(
        cpu_percent=lambda interval=None: 0.0,
        virtual_memory=lambda: types.SimpleNamespace(percent=0.0, available=0, total=0),
        disk_usage=lambda path: types.SimpleNamespace(percent=0.0, free=0, total=0),
    )
if "torch" not in sys.modules:
    _cuda = types.SimpleNamespace(
        is_available=lambda: False,
        current_device=lambda: 0,
        reset_peak_memory_stats=lambda *_a, **_k: None,
        max_memory_allocated=lambda *_a, **_k: 0,
    )
    sys.modules["torch"] = types.SimpleNamespace(
        cuda=_cuda,
        backends=types.SimpleNamespace(
            cuda=types.SimpleNamespace(matmul=types.SimpleNamespace(allow_tf32=False)),
            cudnn=types.SimpleNamespace(allow_tf32=False, benchmark=False),
        ),
        set_float32_matmul_precision=lambda *_a, **_k: None,
        get_float32_matmul_precision=lambda: "highest",
    )

import main  # noqa: E402
import sam3_runner  # noqa: E402


def test_cuda_context_poisoned_classifier():
    poison = sam3_runner._cuda_context_poisoned
    assert poison(RuntimeError("CUDA error: an illegal memory access was encountered"))
    assert poison(RuntimeError("device-side assert triggered"))
    assert poison(RuntimeError("CUBLAS_STATUS_EXECUTION_FAILED"))
    assert poison(RuntimeError("cuDNN error: CUDNN_STATUS_NOT_INITIALIZED"))
    # OOM is recoverable — must NOT be treated as a poisoned context.
    assert not poison(RuntimeError("CUDA out of memory. Tried to allocate ..."))
    # Non-CUDA errors are ordinary per-chip failures.
    assert not poison(ValueError("bad prompt"))


def test_guarded_pipeline_self_heals_on_poison(monkeypatch):
    """A poisoned-context error from anywhere in the pipeline triggers os._exit."""
    exited = {"code": None}

    def _fake_exit(code):
        exited["code"] = code
        raise SystemExit(code)  # stand in for the real process death

    monkeypatch.setattr(main.os, "_exit", _fake_exit)

    async def _boom(*_a, **_k):
        raise RuntimeError("CUDA error: an illegal memory access was encountered")

    monkeypatch.setattr(main, "_detect_pipeline", _boom)

    with pytest.raises(SystemExit):
        asyncio.run(main._detect_pipeline_guarded("bundle", {}, "rgb", None, None, None))
    assert exited["code"] == 1


def test_guarded_pipeline_passes_through_normal_errors(monkeypatch):
    """A non-poison error propagates unchanged (one chip fails, container lives)."""
    called = {"exit": False}
    monkeypatch.setattr(main.os, "_exit", lambda code: called.__setitem__("exit", True))

    async def _value_error(*_a, **_k):
        raise ValueError("decode failed for this chip")

    monkeypatch.setattr(main, "_detect_pipeline", _value_error)

    with pytest.raises(ValueError):
        asyncio.run(main._detect_pipeline_guarded("bundle", {}, "rgb", None, None, None))
    assert called["exit"] is False


def test_guarded_pipeline_returns_result_on_success(monkeypatch):
    async def _ok(*_a, **_k):
        return {"detections": [1, 2, 3]}

    monkeypatch.setattr(main, "_detect_pipeline", _ok)
    out = asyncio.run(main._detect_pipeline_guarded("bundle", {}, "rgb", None, None, None))
    assert out == {"detections": [1, 2, 3]}

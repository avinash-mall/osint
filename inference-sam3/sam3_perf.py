"""SAM3-specific perf helpers. Pure Python, no torch at module level.

Imported by sam3_runner.py and (for is_blackwell_consumer) the FA3 fallback
installer. Kept tiny on purpose so we can unit-test without a GPU.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator, MutableMapping


@contextmanager
def stage_timer(timings: MutableMapping[str, float], name: str) -> Iterator[None]:
    """Record wall-clock for a SAM3 sub-stage into ``timings[name]`` (ms).

    Accumulates across calls so loops (e.g. multi-chunk batched inference)
    report cumulative cost rather than just the last chunk. Synchronizes
    CUDA before reading so the number reflects actual GPU work, not just
    kernel launch time. No-op if torch isn't importable.
    """
    sync = False
    try:
        import torch
        sync = torch.cuda.is_available()
    except ImportError:
        sync = False
    t0 = time.perf_counter()
    try:
        yield
    finally:
        if sync:
            import torch
            torch.cuda.synchronize()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        timings[name] = round(timings.get(name, 0.0) + elapsed_ms, 3)


def is_blackwell_consumer() -> bool:
    """sm_120 — RTX 5060/5070/5080/5090. FA3 is dead here; FA4 pending.

    Returns False on CPU-only environments and on any GPU with compute
    capability major < 12.
    """
    try:
        import torch
        if not torch.cuda.is_available():
            return False
        major, _ = torch.cuda.get_device_capability(0)
        return major >= 12
    except Exception:
        return False


def pin_sdpa_backend(prefer_flash: bool = True):
    """Context manager forcing SDPA to use FLASH_ATTENTION+EFFICIENT_ATTENTION.

    Drops the MATH backend so attention always picks the best available
    accelerated kernel. Falls back gracefully if torch.nn.attention.sdpa_kernel
    is not present (older PyTorch).
    """
    try:
        from torch.nn.attention import SDPBackend, sdpa_kernel
    except ImportError:
        from contextlib import nullcontext
        return nullcontext()
    backends = (
        [SDPBackend.FLASH_ATTENTION, SDPBackend.EFFICIENT_ATTENTION]
        if prefer_flash
        else [SDPBackend.EFFICIENT_ATTENTION]
    )
    return sdpa_kernel(backends)

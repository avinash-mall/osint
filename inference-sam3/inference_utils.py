"""Shared inference utilities: OOM-aware retry, YOLO knobs, memory guard.

Imported by yoloe.py, dota_obb.py, grounding_dino.py, sam3_runner.py.

Design notes:
  * ``safe_predict`` is the *only* place we catch OutOfMemoryError. Every
    caller passes its own cleanup callback so it can shrink batch size,
    halve imgsz, etc. before retry.
  * ``apply_yolo_optimizations`` runs once at model load. It is intentionally
    forgiving: any optimization that throws is logged and skipped so a
    single broken kernel never bricks model loading.
  * No CUDA imports at module level — keeps the file unit-testable without
    a GPU runtime.
"""
from __future__ import annotations

import gc
import logging
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, TypeVar

logger = logging.getLogger("inference_utils")

T = TypeVar("T")


def _cuda_oom_types() -> tuple[type, ...]:
    """Resolve OOM exception types lazily.

    Returns a tuple suitable for ``except`` — empty if torch isn't installed.
    Keeping torch out of module top-level avoids GPU init in test runs.
    """
    try:
        import torch
        oom = getattr(torch.cuda, "OutOfMemoryError", None)
        if oom is not None:
            return (oom, RuntimeError)
        return (RuntimeError,)
    except ImportError:
        return ()


def _is_oom_runtime_error(exc: BaseException) -> bool:
    """Distinguish OOM RuntimeErrors from other RuntimeErrors.

    Older PyTorch builds raise plain RuntimeError with "out of memory" in
    the message instead of torch.cuda.OutOfMemoryError. We only want to
    retry on the OOM-flavoured ones; everything else propagates.
    """
    if isinstance(exc, RuntimeError) and type(exc).__name__ == "RuntimeError":
        msg = str(exc).lower()
        return "out of memory" in msg or "cuda oom" in msg
    return True


def safe_predict(
    fn: Callable[[], T],
    *,
    on_oom: Callable[[], None],
    oom_types: Iterable[type] | None = None,
    max_retries: int = 1,
    fallback: Callable[[], T] | None = None,
    name: str = "predict",
) -> T:
    """Run ``fn``, recovering from CUDA OOM via ``on_oom`` + retry.

    ``on_oom`` is called between attempts — typical body is
    ``torch.cuda.empty_cache(); gc.collect()`` plus model-specific knob
    shrinking (halve batch, lower imgsz).

    If retries are exhausted and ``fallback`` is provided, the fallback's
    return value is returned (e.g. an empty detection list). Otherwise the
    last exception propagates.
    """
    types = tuple(oom_types) if oom_types is not None else _cuda_oom_types()
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except types as exc:  # type: ignore[misc]
            if not _is_oom_runtime_error(exc):
                raise
            last_exc = exc
            logger.warning(
                "OOM during %s (attempt %d/%d): %s",
                name, attempt + 1, max_retries + 1, exc,
            )
            try:
                on_oom()
            except Exception as cleanup_exc:
                logger.exception("on_oom cleanup raised: %s", cleanup_exc)
    if fallback is not None:
        logger.warning("%s exhausted retries; using fallback", name)
        return fallback()
    assert last_exc is not None
    raise last_exc


def apply_yolo_optimizations(
    model: Any,
    *,
    half: bool = False,
    fuse: bool = False,
    channels_last: bool = False,
) -> None:
    """Apply Ultralytics-friendly speedups in-place. Safe to call once at load.

    Each flag is independent and individually swallowed on failure so a
    single broken op (e.g. fuse on a model that doesn't support it) does
    not abort the whole pipeline.
    """
    if model is None:
        return
    if fuse:
        try:
            model.fuse()
            logger.info("yolo_optimizations: fuse() applied")
        except Exception as exc:
            logger.warning("yolo_optimizations: fuse() failed: %s", exc)
    if half:
        try:
            model.half()
            logger.info("yolo_optimizations: half() applied")
        except Exception as exc:
            logger.warning("yolo_optimizations: half() failed: %s", exc)
    if channels_last:
        try:
            import torch
            inner = getattr(model, "model", model)
            inner.to(memory_format=torch.channels_last)
            logger.info("yolo_optimizations: channels_last applied")
        except Exception as exc:
            logger.warning("yolo_optimizations: channels_last failed: %s", exc)


@contextmanager
def device_ctx(device) -> Iterator[None]:
    """Pin PyTorch's thread-local current CUDA device for the enclosed GPU work.

    Any model forward that runs in the anyio worker threadpool must pin the
    current device: worker threads start on ``cuda:0``, so a forward whose
    weights/inputs live on ``cuda:N`` issues cross-device kernels (cuBLAS /
    cuDNN workspace on the *current* device) and can hit an illegal memory
    access — especially under multi-request concurrency. Mirrors
    ``sam3_runner._device_ctx``; shared here so the embedding + specialist
    runners pin the same way SAM3 does. No-op on CPU / when torch is absent.
    """
    dev = str(device) if device is not None else ""
    if not dev.startswith("cuda"):
        yield
        return
    import torch
    with torch.cuda.device(device):
        yield


@contextmanager
def memory_guard(label: str = "inference") -> Iterator[None]:
    """Reset peaks on entry, log peak + fragmentation on exit.

    Used to instrument individual model calls in sam3_runner / specialists.
    Cheap enough for hot paths (two PyTorch calls), and the log line is
    DEBUG so it doesn't spam at INFO.
    """
    try:
        import torch
        has_cuda = torch.cuda.is_available()
    except ImportError:
        has_cuda = False
    if not has_cuda:
        yield
        return
    import torch  # already verified above
    dev = torch.cuda.current_device()
    torch.cuda.reset_peak_memory_stats(dev)
    try:
        yield
    finally:
        peak = torch.cuda.max_memory_allocated(dev) / (1024 * 1024)
        reserved = torch.cuda.memory_reserved(dev) / (1024 * 1024)
        allocated = torch.cuda.memory_allocated(dev) / (1024 * 1024)
        logger.debug(
            "%s: peak=%.1f MiB allocated=%.1f MiB reserved=%.1f MiB frag=%.1f MiB",
            label, peak, allocated, reserved, reserved - allocated,
        )


def cuda_cleanup() -> None:
    """Best-effort cache flush. Call from ``on_oom`` callbacks, not hot paths."""
    try:
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass

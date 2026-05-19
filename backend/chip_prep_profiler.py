"""Per-stage timing for the raster chip-prep loop.

Pure Python, thread-safe, no torch. Gated by env `CHIP_PREP_PROFILE=1` —
when disabled, `stage_timer` is a near-zero-cost no-op context manager
and `record()` does nothing, so production paths pay only one env-var read
per process at module import.

Two callers:
- `scripts/benchmark_chip_prep.py` flips the env on, runs `slice_and_infer`,
  then reads `snapshot()` to write the bench JSON.
- `scripts/profile_chip_prep.py` does the same but also tails per-chip
  segments into a CSV via `record_event()` for offline analysis.

Stage vocabulary (the canonical keys):
- ``valid_mask``      : `valid_data_mask` per window
- ``read_probe``      : the all-zero / nodata probe ``src.read`` before encode
- ``encode``          : `_emit_chip_payload` (window read + encode to PNG/TIFF)
- ``encode_png``      : ``Image.save`` PNG inside the RGB branch
- ``encode_geotiff``  : `_geotiff_window_file` MemoryFile path for MSI/SAR
- ``submit``          : `executor.submit(_post_chip_to_sam3, ...)`
- ``post_roundtrip``  : wall time from submit to ``fut.result()`` (HTTP + server)
- ``apply_response``  : `_apply_chip_response` per-chip detection projection
- ``dedupe``          : `dedupe_idx.add` (NMS / WBF)
"""
from __future__ import annotations

import csv
import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator


def is_enabled() -> bool:
    """True when env CHIP_PREP_PROFILE=1.

    Read once per process — caller code paths should not check this in the
    hot loop; `stage_timer` already short-circuits when disabled.
    """
    return (os.getenv("CHIP_PREP_PROFILE") or "").strip() == "1"


_ENABLED = is_enabled()

_lock = threading.Lock()
_histograms: dict[str, list[float]] = {}
_event_writer: "csv.writer | None" = None
_event_file = None


@contextmanager
def stage_timer(name: str) -> Iterator[None]:
    """Time a code segment into the ``name`` histogram.

    No-op when ``CHIP_PREP_PROFILE`` is unset (the common case): the only
    cost is the ``yield`` and a module-level boolean check.
    """
    if not _ENABLED:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        record(name, elapsed_ms)


def record(name: str, elapsed_ms: float) -> None:
    """Append a single sample to the named histogram and the CSV (if open).

    Safe to call from any thread.
    """
    if not _ENABLED:
        return
    with _lock:
        hist = _histograms.get(name)
        if hist is None:
            hist = []
            _histograms[name] = hist
        hist.append(float(elapsed_ms))
        if _event_writer is not None:
            try:
                _event_writer.writerow((time.time(), name, f"{elapsed_ms:.6f}"))
            except Exception:
                # CSV failures must never break the worker.
                pass


def snapshot() -> dict[str, list[float]]:
    """Return a copy of the current per-stage sample lists (ms)."""
    with _lock:
        return {key: list(values) for key, values in _histograms.items()}


def reset() -> None:
    """Clear all recorded samples — call between benchmark iterations."""
    with _lock:
        _histograms.clear()


def open_csv(path: str) -> None:
    """Tee every `record()` call into a CSV at ``path`` (per-chip events).

    Useful for `scripts/profile_chip_prep.py`; the bench JSON path doesn't
    need this. Caller is responsible for `close_csv()` at the end.
    """
    global _event_file, _event_writer
    if not _ENABLED:
        return
    close_csv()
    _event_file = open(path, "w", newline="", buffering=1)
    _event_writer = csv.writer(_event_file)
    _event_writer.writerow(("epoch_s", "stage", "elapsed_ms"))


def close_csv() -> None:
    global _event_file, _event_writer
    if _event_file is not None:
        try:
            _event_file.close()
        except Exception:
            pass
    _event_file = None
    _event_writer = None


def force_enable_for_tests() -> None:
    """Test-only escape hatch: flip ``_ENABLED`` after import.

    Avoids the need to monkeypatch the env before importing the module
    in pytest. Production callers must use ``CHIP_PREP_PROFILE=1`` instead.
    """
    global _ENABLED
    _ENABLED = True


def force_disable_for_tests() -> None:
    """Counterpart to ``force_enable_for_tests``; restores no-op behavior."""
    global _ENABLED
    _ENABLED = False

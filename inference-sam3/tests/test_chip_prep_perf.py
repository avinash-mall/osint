"""Smoke test the chip_prep_profiler module.

Mirrors the shape of `test_sam3_perf.py:test_stage_timer_accumulates` —
no GPU, no inference service, no real raster needed. Verifies the
no-op-when-disabled contract, accumulation, and CSV side-channel so
later phases can rely on the profiler not silently rotting.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Make backend/ importable regardless of cwd — the profiler lives there
# because the chip-prep loop is the worker's responsibility, not the
# inference service's.
_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def test_stage_timer_is_noop_when_disabled():
    """When env CHIP_PREP_PROFILE is unset, stage_timer must record nothing."""
    import chip_prep_profiler

    chip_prep_profiler.force_disable_for_tests()
    chip_prep_profiler.reset()
    with chip_prep_profiler.stage_timer("encode"):
        pass
    chip_prep_profiler.record("post_roundtrip", 5.0)
    assert chip_prep_profiler.snapshot() == {}, "disabled mode must not record"


def test_stage_timer_accumulates_when_enabled():
    """Two timed segments must produce two samples in the histogram."""
    import chip_prep_profiler

    chip_prep_profiler.force_enable_for_tests()
    chip_prep_profiler.reset()
    try:
        with chip_prep_profiler.stage_timer("read_probe"):
            pass
        with chip_prep_profiler.stage_timer("read_probe"):
            pass
        snap = chip_prep_profiler.snapshot()
        assert "read_probe" in snap
        assert len(snap["read_probe"]) == 2, "expected two samples"
        assert all(value >= 0.0 for value in snap["read_probe"]), "ms must be non-negative"
    finally:
        chip_prep_profiler.force_disable_for_tests()


def test_record_appends_into_named_histogram():
    """Direct `record()` calls (used for `post_roundtrip`) end up in the same
    histogram structure as `stage_timer`."""
    import chip_prep_profiler

    chip_prep_profiler.force_enable_for_tests()
    chip_prep_profiler.reset()
    try:
        chip_prep_profiler.record("post_roundtrip", 12.5)
        chip_prep_profiler.record("post_roundtrip", 18.0)
        chip_prep_profiler.record("dedupe", 0.7)
        snap = chip_prep_profiler.snapshot()
        assert snap["post_roundtrip"] == [12.5, 18.0]
        assert snap["dedupe"] == [0.7]
    finally:
        chip_prep_profiler.force_disable_for_tests()


def test_csv_sidecar_writes_rows_per_record():
    """`open_csv` tees every record() into a CSV; one row per event with
    columns (epoch_s, stage, elapsed_ms)."""
    import chip_prep_profiler

    chip_prep_profiler.force_enable_for_tests()
    chip_prep_profiler.reset()
    with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as fh:
        csv_path = fh.name
    try:
        chip_prep_profiler.open_csv(csv_path)
        chip_prep_profiler.record("encode", 3.0)
        chip_prep_profiler.record("submit", 0.05)
        chip_prep_profiler.close_csv()

        rows = Path(csv_path).read_text().strip().splitlines()
        assert rows[0] == "epoch_s,stage,elapsed_ms", "CSV header must match the documented shape"
        assert len(rows) == 3, "header + two data rows expected"
        assert ",encode," in rows[1]
        assert ",submit," in rows[2]
    finally:
        chip_prep_profiler.force_disable_for_tests()
        os.unlink(csv_path)


def test_force_enable_disable_round_trip():
    """The test-only flip helpers must round-trip cleanly so subsequent
    tests see a clean baseline."""
    import chip_prep_profiler

    chip_prep_profiler.force_enable_for_tests()
    chip_prep_profiler.reset()
    chip_prep_profiler.record("any", 1.0)
    assert chip_prep_profiler.snapshot() == {"any": [1.0]}
    chip_prep_profiler.force_disable_for_tests()
    chip_prep_profiler.record("any", 2.0)
    assert chip_prep_profiler.snapshot() == {"any": [1.0]}, "disabled mode must not append"
    chip_prep_profiler.reset()
    assert chip_prep_profiler.snapshot() == {}

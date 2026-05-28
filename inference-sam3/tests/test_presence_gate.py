"""Unit tests for the SegEarth-OV3-inspired presence-ratio gate.

Verifies the three modes of ``_prompt_passes_category_gate``:

* ``max``   — legacy: ``max_score >= threshold``.
* ``ratio`` — SegEarth-OV3 score-distribution shape gate.
* ``both``  — DEFAULT: both must pass.

All tests run without SAM3 / torch / GPU — the function consumes a plain
dict shaped like SAM3's ``processor.set_text_prompt`` output, so we mock
the score list directly. See ``docs/decisions/why-segearth-presence-filter.md``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure inference-sam3 root is importable regardless of cwd.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import sam3_runner  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_presence_constants(monkeypatch):
    """Pin presence-gate constants to documented defaults per test."""
    monkeypatch.setattr(sam3_runner, "SAM3_PRESENCE_MODE", "both")
    monkeypatch.setattr(sam3_runner, "SAM3_PRESENCE_RATIO_FLOOR", 1.8)
    monkeypatch.setattr(sam3_runner, "SAM3_PRESENCE_RATIO_EPS", 0.05)
    monkeypatch.setattr(sam3_runner, "SAM3_CATEGORY_THR", 0.40)
    # Wipe any per-class overrides that test environments may have set.
    monkeypatch.setattr(sam3_runner, "_PER_CLASS_CATEGORY_THR", {})
    yield


def _output(scores):
    return {"scores": list(scores)}


def test_presence_ratio_blocks_diffuse_distribution():
    # 10 scores all hovering around 0.45 — max 0.50, mean 0.452, ratio ≈ 1.11.
    # Passes the legacy 0.40 max gate, but the ratio gate kills it.
    scores = [0.50, 0.48, 0.46, 0.45, 0.44, 0.43, 0.46, 0.45, 0.44, 0.41]
    assert sam3_runner._prompt_passes_category_gate(_output(scores), label="vehicle") is False


def test_presence_ratio_passes_sharp_distribution():
    # Localised: one strong mask, rest weak — max 0.85, mean ~0.20, ratio ≈ 4.25.
    scores = [0.85, 0.10, 0.12, 0.09, 0.11, 0.15, 0.14, 0.13, 0.16, 0.12]
    assert sam3_runner._prompt_passes_category_gate(_output(scores), label="vehicle") is True


def test_presence_mode_max_skips_ratio_check(monkeypatch):
    # Diffuse distribution from the first test should now pass — ratio gate disabled.
    monkeypatch.setattr(sam3_runner, "SAM3_PRESENCE_MODE", "max")
    scores = [0.50, 0.48, 0.46, 0.45, 0.44, 0.43, 0.46, 0.45, 0.44, 0.41]
    assert sam3_runner._prompt_passes_category_gate(_output(scores), label="vehicle") is True


def test_presence_mode_ratio_skips_max_check(monkeypatch):
    # max=0.30 (below the 0.40 legacy floor) but sharply localised:
    # mean = (0.30 + 9*0.02)/10 = 0.048, ratio = 0.30/0.048 ≈ 6.25, passes ratio.
    monkeypatch.setattr(sam3_runner, "SAM3_PRESENCE_MODE", "ratio")
    scores = [0.30] + [0.02] * 9
    assert sam3_runner._prompt_passes_category_gate(_output(scores), label="vehicle") is True


def test_presence_ratio_zero_floor_disables_gate(monkeypatch):
    # Floor=0.0 effectively disables the ratio side; diffuse passes (max-gate happy).
    monkeypatch.setattr(sam3_runner, "SAM3_PRESENCE_RATIO_FLOOR", 0.0)
    scores = [0.50, 0.48, 0.46, 0.45, 0.44, 0.43, 0.46, 0.45, 0.44, 0.41]
    assert sam3_runner._prompt_passes_category_gate(_output(scores), label="vehicle") is True


def test_presence_empty_scores_returns_false():
    # No candidates → drop the prompt (preserves existing behaviour).
    assert sam3_runner._prompt_passes_category_gate(_output([]), label="vehicle") is False


def test_presence_signals_helper():
    signals = sam3_runner._presence_signals([0.80, 0.10, 0.10])
    assert signals["n"] == 3
    assert signals["max"] == pytest.approx(0.80)
    assert signals["mean"] == pytest.approx(0.3333333, rel=1e-3)
    assert signals["ratio"] == pytest.approx(0.80 / 0.3333333, rel=1e-3)

    empty = sam3_runner._presence_signals([])
    assert empty == {"max": 0.0, "mean": 0.0, "ratio": 0.0, "n": 0}


def test_batched_path_applies_presence_gate(monkeypatch):
    """`_collect_batched_candidates` (the SAM3_BATCHED_TEXT=1 production path)
    must delegate to `_prompt_passes_category_gate` so the new ratio gate
    actually fires. A diffuse score distribution (max ≈ mean) must drop the
    prompt under default mode `both` and survive under legacy mode `max`."""
    import numpy as np

    # Diffuse: max=0.47, mean=0.446, ratio≈1.05 — below the 1.8 floor.
    diffuse_scores = [0.45, 0.43, 0.47, 0.42, 0.46]
    fake_mask = np.zeros((4, 4), dtype=bool)
    fake_box = np.asarray([0.0, 0.0, 1.0, 1.0])
    processed = {
        0: {
            "masks": [fake_mask for _ in diffuse_scores],
            "boxes": [fake_box for _ in diffuse_scores],
            "scores": diffuse_scores,
        }
    }
    query_labels = {0: "vehicle"}

    # Default mode `both`: ratio gate kills the diffuse distribution → zero candidates.
    out = sam3_runner._collect_batched_candidates(processed, query_labels)
    assert out == [], "batched path must apply the ratio gate under default mode"

    # Legacy mode `max`: only max-score check (0.47 > 0.40 floor) → all kept.
    monkeypatch.setattr(sam3_runner, "SAM3_PRESENCE_MODE", "max")
    out_legacy = sam3_runner._collect_batched_candidates(processed, query_labels)
    assert len(out_legacy) == 5, "max-mode batched path must restore legacy permissive behaviour"


def test_invalid_presence_mode_falls_back_to_both(monkeypatch, caplog):
    """An invalid SAM3_PRESENCE_MODE value (typo) must warn and fall back to
    'both' rather than silently disabling both gates."""
    import importlib
    import logging

    monkeypatch.setenv("SAM3_PRESENCE_MODE", "foobar")
    with caplog.at_level(logging.WARNING, logger="sam3_runner"):
        importlib.reload(sam3_runner)
    assert sam3_runner.SAM3_PRESENCE_MODE == "both"
    assert any("SAM3_PRESENCE_MODE" in rec.message and "invalid" in rec.message for rec in caplog.records)


def test_presence_gate_logs_drop_at_debug_level(caplog):
    """A dropped prompt must emit a debug log line carrying the label +
    presence signals so operators can grep when tuning the floor."""
    import logging

    diffuse = [0.45, 0.43, 0.47, 0.42, 0.46]
    with caplog.at_level(logging.DEBUG, logger="sam3_runner"):
        result = sam3_runner._prompt_passes_category_gate(_output(diffuse), label="vehicle")
    assert result is False
    drop_logs = [r for r in caplog.records if "presence gate dropped" in r.message]
    assert drop_logs, "expected a debug log line on prompt drop"
    msg = drop_logs[-1].message
    assert "vehicle" in msg
    assert "signals=" in msg


def test_per_class_threshold_still_honored_in_both_mode(monkeypatch):
    # Per-class override raises the max gate from 0.40 to 0.60 for "vehicle".
    # Distribution: max=0.55 — fails the per-class max gate even though
    # ratio (0.55 / ~0.10 = 5.5) would otherwise pass.
    monkeypatch.setattr(
        sam3_runner,
        "_PER_CLASS_CATEGORY_THR",
        {sam3_runner._canonical_prompt_key("vehicle"): 0.60},
    )
    scores = [0.55, 0.10, 0.08, 0.09, 0.11, 0.10, 0.12, 0.10, 0.09, 0.11]
    assert sam3_runner._prompt_passes_category_gate(_output(scores), label="vehicle") is False

    # The same scores pass for an unrelated label that falls through to the
    # global 0.40 floor (and ratio is comfortable).
    assert sam3_runner._prompt_passes_category_gate(_output(scores), label="ship") is True

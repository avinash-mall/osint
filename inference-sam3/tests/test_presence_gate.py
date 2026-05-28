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

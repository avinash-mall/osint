from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import grounding_dino_gate as gate


# Mimic the legacy ground_v1_full vocabulary for tests so the gate looks like
# it did before the static JSON was deleted in favour of the backend ontology.
# In production the equivalent set is fetched live from /api/ontology/default-prompts.
_FAKE_GROUND_V1_FULL = frozenset({
    "tank",  # required by the substring-match test below
    "car", "airplane", "ship", "person", "bicycle", "motorcycle",
    "bus", "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
    "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
    "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
} | {f"placeholder_term_{i}" for i in range(500)})  # pad past 500 for size assertion


@pytest.fixture(autouse=True)
def _stub_ontology_vocab(monkeypatch):
    """Replace the live backend fetch with a static fake covering the
    legacy ground_v1_full vocabulary. Keeps tests deterministic and offline."""
    gate._ONTOLOGY_VOCAB_CACHE["ts"] = 0.0
    gate._ONTOLOGY_VOCAB_CACHE["vocab"] = frozenset()
    monkeypatch.setattr(gate, "_fetch_ontology_vocab", lambda: _FAKE_GROUND_V1_FULL)
    yield
    gate._ONTOLOGY_VOCAB_CACHE["ts"] = 0.0
    gate._ONTOLOGY_VOCAB_CACHE["vocab"] = frozenset()


def test_common_vocab_loaded():
    assert gate.common_vocab_size() > 500


def test_exact_common_prompt_is_common():
    assert gate.is_common("car") is True
    assert gate.is_common("airplane") is True
    assert gate.is_common("ship") is True


def test_dota_class_is_common():
    assert gate.is_common("plane") is True
    assert gate.is_common("storage tank") is True
    assert gate.is_common("large vehicle") is True


def test_geographic_term_is_common():
    assert gate.is_common("water") is True
    assert gate.is_common("vegetation") is True


def test_uncommon_term_is_not_common():
    assert gate.is_common("zxqkk_unicorn_battalion_3000") is False


def test_substring_match_helps_specific_phrases():
    # "main battle tank" should match because it contains "tank" — common.
    assert gate.is_common("main battle tank") is True


def test_should_run_skips_when_all_common():
    should, reason = gate.should_run_grounding_dino(["car", "ship", "plane"])
    assert should is False
    assert reason == "all_prompts_in_common_vocab"


def test_should_run_runs_when_any_uncommon():
    should, reason = gate.should_run_grounding_dino(["car", "zxqkk_unknown_thing"])
    assert should is True
    assert reason is None


def test_should_run_skips_on_empty_prompts():
    should, reason = gate.should_run_grounding_dino([])
    assert should is False
    assert reason == "no_prompts"


def test_force_overrides_gate():
    should, reason = gate.should_run_grounding_dino(["car"], force=True)
    assert should is True
    assert reason is None


def test_static_vocab_alone_when_backend_offline(monkeypatch):
    """When the backend ontology fetch fails and nothing is cached, the
    gate degrades to only the static DOTA + geo vocabulary (no exception)."""
    monkeypatch.setattr(gate, "_fetch_ontology_vocab", lambda: frozenset())
    # DOTA-static still works
    assert gate.is_common("plane") is True
    # Generic COCO term that's not in static vocab should now be uncommon
    assert gate.is_common("toothbrush") is False

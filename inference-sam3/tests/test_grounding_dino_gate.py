from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import grounding_dino_gate as gate


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

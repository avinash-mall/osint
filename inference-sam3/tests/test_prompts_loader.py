from __future__ import annotations

from prompts.loader import resolve_prompts, select_default_profile


def test_select_default_profile():
    assert select_default_profile("fmv") == "ground_v1"
    assert select_default_profile("sar") == "satellite_v1"


def test_text_prompts_override_dedupe_and_cap():
    prompts = resolve_prompts({"text_prompts": [" Ship ", "ship", "Airplane"]}, max_prompts=1)
    assert prompts == ["ship"]


def test_profile_resolution():
    prompts = resolve_prompts({"modality": "sar"}, max_prompts=3)
    assert prompts == ["fixed-wing aircraft", "small aircraft", "cargo plane"]

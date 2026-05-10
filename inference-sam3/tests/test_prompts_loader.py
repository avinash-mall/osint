from __future__ import annotations

from prompts.loader import resolve_prompts, select_default_profile


def test_select_default_profile():
    assert select_default_profile("fmv") == "ground_v1"
    assert select_default_profile("sar") == "satellite_v1"


def test_text_prompts_override_dedupes_and_normalizes():
    prompts = resolve_prompts({"text_prompts": [" Ship ", "ship", "Airplane"]})
    assert prompts == ["ship", "airplane"]


def test_profile_resolution_returns_full_profile():
    prompts = resolve_prompts({"modality": "sar"})
    assert prompts[:3] == ["airplane", "helicopter", "ship"]
    assert len(prompts) == 25

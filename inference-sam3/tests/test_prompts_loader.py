"""Tests for the resolve_prompts() helper now living in main.py.

The legacy prompts.loader module (static JSON profiles) was removed in the
ontology refactor; defaults now come from the backend ontology API.
"""
from __future__ import annotations

import pytest

import main


@pytest.fixture(autouse=True)
def _clear_prompt_cache():
    main._DEFAULT_PROMPTS_CACHE.clear()
    yield
    main._DEFAULT_PROMPTS_CACHE.clear()


def test_modality_to_sensor_mapping():
    assert main._modality_to_sensor("rgb") == "optical"
    assert main._modality_to_sensor("fmv") == "optical"
    assert main._modality_to_sensor("multispectral") == "multispectral"
    assert main._modality_to_sensor("sar") == "sar"
    assert main._modality_to_sensor("") == "optical"
    assert main._modality_to_sensor("weird") == "optical"


def test_text_prompts_override_dedupes_and_normalizes(monkeypatch):
    # If the resolver ever falls through to the backend, that would be a bug —
    # poison the fetch path so the test fails loudly in that case.
    monkeypatch.setattr(
        main, "_fetch_default_prompts",
        lambda *_a, **_k: pytest.fail("should not call backend when text_prompts present"),
    )
    prompts = main.resolve_prompts({"text_prompts": [" Ship ", "ship", "Airplane"]})
    assert prompts == ["ship", "airplane"]


def test_falls_back_to_backend_defaults(monkeypatch):
    captured = {}

    def fake_fetch(sensor, timeout=5.0):
        captured["sensor"] = sensor
        return ["car", "Car", " building "]

    monkeypatch.setattr(main, "_fetch_default_prompts", fake_fetch)
    prompts = main.resolve_prompts({"modality": "rgb"})
    assert captured["sensor"] == "optical"
    assert prompts == ["car", "building"]


def test_sar_modality_maps_to_sar_sensor(monkeypatch):
    seen = {}

    def fake_fetch(sensor, timeout=5.0):
        seen["sensor"] = sensor
        return ["__prithvi_flood__"]

    monkeypatch.setattr(main, "_fetch_default_prompts", fake_fetch)
    prompts = main.resolve_prompts({"modality": "sar"})
    assert seen["sensor"] == "sar"
    assert prompts == ["__prithvi_flood__"]


def test_backend_unavailable_raises_typed_error(monkeypatch):
    def boom(*_a, **_k):
        raise ConnectionError("no route to backend")

    monkeypatch.setattr(main, "_fetch_default_prompts", boom)
    with pytest.raises(main.OntologyBackendUnavailable):
        main.resolve_prompts({"modality": "rgb"})


def test_explicit_text_prompts_skip_backend_even_on_outage(monkeypatch):
    monkeypatch.setattr(
        main, "_fetch_default_prompts",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionError("down")),
    )
    prompts = main.resolve_prompts({
        "modality": "sar",
        "text_prompts": ["ship"],
    })
    assert prompts == ["ship"]


def test_empty_backend_response_raises_value_error(monkeypatch):
    monkeypatch.setattr(main, "_fetch_default_prompts", lambda *_a, **_k: [])
    with pytest.raises(ValueError):
        main.resolve_prompts({"modality": "rgb"})


def test_fetch_caches_per_sensor(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        return FakeResp({"prompts": ["a", "b"]})

    monkeypatch.setattr(main.requests, "get", fake_get)
    main._DEFAULT_PROMPTS_CACHE.clear()
    a = main._fetch_default_prompts("optical")
    b = main._fetch_default_prompts("optical")
    assert a == b == ["a", "b"]
    assert calls["n"] == 1  # second call served from cache

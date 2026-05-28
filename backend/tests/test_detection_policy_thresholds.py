"""Unit tests for Task 1.5 — per-class confidence floor defaults.

Pin down the precedence rules baked into ``DEFAULT_PER_CLASS_THRESHOLDS``
and ``active_detection_policy``: code-shipped defaults < env override <
DB override. See docs/decisions/why-transportation-floor-raised.md.
"""
from __future__ import annotations

import detection_policy


def _reset_policy(monkeypatch, *, env_overrides_raw: str | None = None) -> None:
    """Common fixture: clear DB overrides, set/clear env, clear lru_cache."""
    monkeypatch.delenv("GLOBAL_CONFIDENCE_FLOOR", raising=False)
    monkeypatch.delenv("HIGH_CONFIDENCE_THRESHOLD", raising=False)
    monkeypatch.delenv("DETECTION_THRESHOLD_PROFILE", raising=False)
    if env_overrides_raw is None:
        monkeypatch.delenv("PER_CLASS_CONFIDENCE_OVERRIDES", raising=False)
    else:
        monkeypatch.setenv("PER_CLASS_CONFIDENCE_OVERRIDES", env_overrides_raw)
    monkeypatch.setattr(detection_policy, "_load_db_overrides", lambda: ({}, None, None))
    detection_policy.invalidate_policy_cache()


def test_transportation_default_floor_is_055(monkeypatch) -> None:
    _reset_policy(monkeypatch)
    policy = detection_policy.active_detection_policy()
    assert detection_policy.threshold_for_parent("transportation", policy) == 0.55
    detection_policy.invalidate_policy_cache()


def test_other_default_floor_is_050(monkeypatch) -> None:
    _reset_policy(monkeypatch)
    policy = detection_policy.active_detection_policy()
    assert detection_policy.threshold_for_parent("other", policy) == 0.50
    detection_policy.invalidate_policy_cache()


def test_unconfigured_bucket_uses_global_floor(monkeypatch) -> None:
    _reset_policy(monkeypatch)
    policy = detection_policy.active_detection_policy()
    # "aircraft" is not in DEFAULT_PER_CLASS_THRESHOLDS → falls back to global floor.
    assert "aircraft" not in detection_policy.DEFAULT_PER_CLASS_THRESHOLDS
    assert detection_policy.threshold_for_parent("aircraft", policy) == 0.40
    detection_policy.invalidate_policy_cache()


def test_env_override_wins_over_default(monkeypatch) -> None:
    _reset_policy(monkeypatch, env_overrides_raw='{"transportation": 0.30}')
    policy = detection_policy.active_detection_policy()
    assert detection_policy.threshold_for_parent("transportation", policy) == 0.30
    # Other defaults still apply because env only specified transportation.
    assert detection_policy.threshold_for_parent("other", policy) == 0.50
    detection_policy.invalidate_policy_cache()


def test_db_override_wins_over_env_and_default(monkeypatch) -> None:
    monkeypatch.delenv("GLOBAL_CONFIDENCE_FLOOR", raising=False)
    monkeypatch.delenv("HIGH_CONFIDENCE_THRESHOLD", raising=False)
    monkeypatch.delenv("DETECTION_THRESHOLD_PROFILE", raising=False)
    monkeypatch.setenv("PER_CLASS_CONFIDENCE_OVERRIDES", '{"transportation": 0.30}')
    monkeypatch.setattr(
        detection_policy,
        "_load_db_overrides",
        lambda: ({"transportation": 0.70}, None, None),
    )
    detection_policy.invalidate_policy_cache()
    policy = detection_policy.active_detection_policy()
    assert detection_policy.threshold_for_parent("transportation", policy) == 0.70
    detection_policy.invalidate_policy_cache()

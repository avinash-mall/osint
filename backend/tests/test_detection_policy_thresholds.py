"""Unit tests for Task 1.5 — per-class confidence floor defaults.

Two layers of coverage:

* **Dictionary contract** — pin down ``DEFAULT_PER_CLASS_THRESHOLDS`` and the
  ``defaults < env < DB`` precedence in ``active_detection_policy``. These tests
  read the dict directly using the same magic-string key the production code
  will read.
* **End-to-end runtime chain** — feed an actual model label (with the spaces /
  capitalisation the inference service emits) through ``parent_class_for_label``
  and assert that the resulting ``parent_class`` resolves to the raised floor.
  This is the regression coverage that would have caught the original T1.5 bug,
  where the dict was keyed by the benchmark-harness bucket names
  (``"transportation"``, ``"other"``) which never appear in the runtime
  ``parent_class`` field.

See docs/decisions/why-transportation-floor-raised.md.
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


# ---------------------------------------------------------------------------
# Dictionary contract — these read the shipped dict / merged policy directly.
# They do NOT exercise the runtime label → parent_class chain.
# ---------------------------------------------------------------------------
def test_dict_contract_transportation_bucket_labels_have_055_floor(monkeypatch) -> None:
    """Every runtime canonical label that the benchmark routes to the
    ``transportation`` bucket must ship with a 0.55 floor."""
    _reset_policy(monkeypatch)
    policy = detection_policy.active_detection_policy()
    transportation_labels = (
        "expressway_service_area",
        "road_bridge",
        "railway_bridge",
        "bridge",
        "overpass",
        "port",
        "interchange",
        "roundabout",
        "toll_booth",
        "border_checkpoint",
    )
    for label in transportation_labels:
        assert detection_policy.threshold_for_parent(label, policy) == 0.55, label
    detection_policy.invalidate_policy_cache()


def test_dict_contract_other_bucket_fallback_label_has_050_floor(monkeypatch) -> None:
    """The runtime catch-all ``parent_class="unknown"`` (and the benchmark
    string ``"other"`` for symmetry) must ship with a 0.50 floor."""
    _reset_policy(monkeypatch)
    policy = detection_policy.active_detection_policy()
    assert detection_policy.threshold_for_parent("unknown", policy) == 0.50
    assert detection_policy.threshold_for_parent("other", policy) == 0.50
    detection_policy.invalidate_policy_cache()


def test_dict_contract_unconfigured_bucket_uses_global_floor(monkeypatch) -> None:
    _reset_policy(monkeypatch)
    policy = detection_policy.active_detection_policy()
    # "aircraft" is not in DEFAULT_PER_CLASS_THRESHOLDS → falls back to global floor.
    assert "aircraft" not in detection_policy.DEFAULT_PER_CLASS_THRESHOLDS
    assert detection_policy.threshold_for_parent("aircraft", policy) == 0.40
    detection_policy.invalidate_policy_cache()


def test_dict_contract_env_override_wins_over_default(monkeypatch) -> None:
    _reset_policy(monkeypatch, env_overrides_raw='{"bridge": 0.30}')
    policy = detection_policy.active_detection_policy()
    assert detection_policy.threshold_for_parent("bridge", policy) == 0.30
    # Other defaults still apply because env only specified bridge.
    assert detection_policy.threshold_for_parent("overpass", policy) == 0.55
    assert detection_policy.threshold_for_parent("unknown", policy) == 0.50
    detection_policy.invalidate_policy_cache()


def test_dict_contract_db_override_wins_over_env_and_default(monkeypatch) -> None:
    monkeypatch.delenv("GLOBAL_CONFIDENCE_FLOOR", raising=False)
    monkeypatch.delenv("HIGH_CONFIDENCE_THRESHOLD", raising=False)
    monkeypatch.delenv("DETECTION_THRESHOLD_PROFILE", raising=False)
    monkeypatch.setenv("PER_CLASS_CONFIDENCE_OVERRIDES", '{"bridge": 0.30}')
    monkeypatch.setattr(
        detection_policy,
        "_load_db_overrides",
        lambda: ({"bridge": 0.70}, None, None),
    )
    detection_policy.invalidate_policy_cache()
    policy = detection_policy.active_detection_policy()
    assert detection_policy.threshold_for_parent("bridge", policy) == 0.70
    detection_policy.invalidate_policy_cache()


# ---------------------------------------------------------------------------
# End-to-end runtime chain — these are the regression tests that would have
# caught the original T1.5 bug, where the dict was keyed by benchmark bucket
# names that never appear as runtime ``parent_class``.
#
# ``parent_class_for_label`` calls ``ontology.normalize(...).parent_class`` when
# PostGIS is reachable, and falls back to ``normalize_label`` (a pure-Python
# canonicaliser) when it is not. Either path produces the same canonical
# label string for the inputs below (``bridge`` / ``overpass`` /
# ``expressway service area`` / ``unknown``), because the seed object label
# matches the canonicalised input exactly. So the test is deterministic
# whether the harness has a DB or not.
# ---------------------------------------------------------------------------
def test_runtime_bridge_label_resolves_to_transport_floor(monkeypatch) -> None:
    """An inference call labelled "bridge" must flow end-to-end into the 0.55 floor.

    This is the regression that would have caught the original T1.5 bug — the
    shipped dict used the benchmark alias ``"transportation"`` which never
    appears as runtime ``parent_class``."""
    _reset_policy(monkeypatch)
    parent = detection_policy.parent_class_for_label("bridge")
    assert parent == "bridge"
    policy = detection_policy.active_detection_policy()
    assert detection_policy.threshold_for_parent(parent, policy) == 0.55
    detection_policy.invalidate_policy_cache()


def test_runtime_overpass_label_resolves_to_transport_floor(monkeypatch) -> None:
    _reset_policy(monkeypatch)
    parent = detection_policy.parent_class_for_label("overpass")
    assert parent == "overpass"
    policy = detection_policy.active_detection_policy()
    assert detection_policy.threshold_for_parent(parent, policy) == 0.55
    detection_policy.invalidate_policy_cache()


def test_runtime_expressway_service_area_resolves_to_transport_floor(monkeypatch) -> None:
    """Input arrives with spaces (e.g. from a SAM3 text prompt) and must still
    normalise to the canonical underscored label the dict is keyed by."""
    _reset_policy(monkeypatch)
    parent = detection_policy.parent_class_for_label("expressway service area")
    assert parent == "expressway_service_area"
    policy = detection_policy.active_detection_policy()
    assert detection_policy.threshold_for_parent(parent, policy) == 0.55
    detection_policy.invalidate_policy_cache()


def test_runtime_unknown_parent_class_uses_other_floor(monkeypatch) -> None:
    """The ontology fallback ``parent_class="unknown"`` must hit the 0.50 floor."""
    _reset_policy(monkeypatch)
    # ``parent_class_for_label("unknown")`` round-trips to "unknown" because the
    # canonicaliser is idempotent and no ontology branch matches a bare
    # "unknown" string.
    parent = detection_policy.parent_class_for_label("unknown")
    assert parent == "unknown"
    policy = detection_policy.active_detection_policy()
    assert detection_policy.threshold_for_parent(parent, policy) == 0.50
    detection_policy.invalidate_policy_cache()

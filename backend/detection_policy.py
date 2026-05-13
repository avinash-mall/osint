"""Open-vocabulary detection policy.

This module replaces the previous defense-only taxonomy with an open-vocabulary
policy: every label a model emits is accepted as a first-class object class.
There is **no per-class confidence threshold, no enabled/disabled distinction,
and no distractor suppression** — the only filter is the optional
``GLOBAL_CONFIDENCE_FLOOR`` env var (default ``0.0``: accept everything).

The public surface (function names + return-shapes) is unchanged so existing
callers in ``backend/worker.py``, ``backend/main.py``, ``backend/tracker.py``
and the four ``inference-*`` services continue to work without edits.

What changed conceptually:

* ``DEFENSE_PARENT_CLASSES`` / ``DISTRACTOR_PARENT_CLASSES`` are gone.
* ``parent_class_for_label`` returns a broad-but-open category (aircraft,
  vessel, vehicle, building, …, **or the normalized label itself** when no
  cluster matches). Nothing is collapsed to ``"unknown"`` any more.
* ``detection_decision`` always returns ``class_enabled=True`` and a
  ``review_status`` of either ``"high_confidence"`` (≥ ``HIGH_CONFIDENCE_THRESHOLD``)
  or ``"review_candidate"`` — never ``"below_class_threshold"`` /
  ``"disabled_distractor"`` unless the operator explicitly raises the floor.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any


TAXONOMY_VERSION = os.getenv("DETECTION_TAXONOMY_VERSION", "open-world-v1")
DEFAULT_MODEL_VERSION = os.getenv("MODEL_VERSION", "open-vocab-multi-model")


SOURCE_PREFIXES = (
    "xview", "dota", "fair1m", "fmow", "rareplanes", "dior",
    "sodaa", "hrsc", "hrsc2016", "lvis", "coco", "objects365", "local",
)


def normalize_label(value: Any) -> str:
    text = str(value or "object").strip().lower()
    text = re.sub(r"[^a-z0-9:]+", "_", text)        # keep ":" so "crop:corn" survives
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "object"


def strip_source_prefix(label: str) -> str:
    normalized = normalize_label(label)
    for prefix in SOURCE_PREFIXES:
        marker = f"{prefix}_"
        if normalized.startswith(marker):
            return normalized[len(marker):]
    return normalized


def parent_class_for_label(label: Any) -> str:
    """Backwards-compat wrapper around backend.ontology.normalize().

    The canonical normalizer lives in backend/ontology.py and reads from the
    DB ontology. This function is kept so existing callers (older code paths,
    tests) continue to work; new code should call ontology.normalize() directly.

    Falls back to the local ``normalize_label`` cleanup if ``ontology`` cannot
    be imported (e.g. when this module is loaded by ``inference-sam3`` via
    ``importlib`` without the backend directory on sys.path) or when the DB
    is unreachable from a stand-alone tool.
    """
    try:
        # Ensure the sibling ``ontology`` module is importable even when this
        # file is loaded via importlib from outside the backend directory.
        import sys as _sys
        from pathlib import Path as _Path
        _here = _Path(__file__).resolve().parent
        if str(_here) not in _sys.path:
            _sys.path.insert(0, str(_here))
        from ontology import normalize as _normalize
        return _normalize(label or "").parent_class
    except Exception:
        return normalize_label(label)


# ---------------------------------------------------------------------------
# Threshold policy — single global floor, no per-class shaping.
# ---------------------------------------------------------------------------
def _parse_csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw:
        return default
    values = tuple(normalize_label(item) for item in raw.split(",") if item.strip())
    return values or default


def _load_json_thresholds(name: str) -> dict[str, float]:
    """Optional per-class overrides for callers that *want* a custom floor.

    The default is empty — i.e. no per-class thresholds.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in parsed.items():
        try:
            out[normalize_label(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def _load_db_overrides() -> tuple[dict[str, float], float | None, float | None]:
    """Read confidence overrides from the inference_config row, if present.

    Returns ``(per_class_thresholds, global_floor, high_threshold)``. All three
    can be ``None`` / empty when the row doesn't exist or the DB is unreachable,
    in which case the env-var fallback is used.
    """
    try:
        from database import postgis_db  # local import — keeps tests that stub the env from needing a DB
        with postgis_db.get_cursor() as cur:
            cur.execute("SELECT config FROM inference_config WHERE id = 1")
            row = cur.fetchone()
    except Exception:
        return {}, None, None
    if not row:
        return {}, None, None
    cfg = row[0] if not isinstance(row, dict) else row.get("config")
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except json.JSONDecodeError:
            return {}, None, None
    if not isinstance(cfg, dict):
        return {}, None, None
    raw_overrides = cfg.get("per_class_confidence_overrides") or {}
    out: dict[str, float] = {}
    if isinstance(raw_overrides, dict):
        for key, value in raw_overrides.items():
            try:
                out[normalize_label(key)] = float(value)
            except (TypeError, ValueError):
                continue
    g = cfg.get("global_floor")
    h = cfg.get("high_confidence_threshold")
    try:
        g = float(g) if g is not None else None
    except (TypeError, ValueError):
        g = None
    try:
        h = float(h) if h is not None else None
    except (TypeError, ValueError):
        h = None
    return out, g, h


@lru_cache(maxsize=1)
def active_detection_policy() -> dict[str, Any]:
    """Open-vocab policy: DB-backed overrides win, env values are the fallback."""
    profile_name = os.getenv("DETECTION_THRESHOLD_PROFILE", "open").strip() or "open"
    global_floor = float(os.getenv("GLOBAL_CONFIDENCE_FLOOR", "0.0"))
    high_threshold = float(os.getenv("HIGH_CONFIDENCE_THRESHOLD", "0.5"))
    env_overrides = _load_json_thresholds("PER_CLASS_CONFIDENCE_OVERRIDES")
    db_overrides, db_global, db_high = _load_db_overrides()
    merged = {**env_overrides, **db_overrides}  # DB wins on collisions
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "model_version": DEFAULT_MODEL_VERSION,
        "threshold_profile": profile_name,
        "global_confidence_floor": db_global if db_global is not None else global_floor,
        "high_confidence_threshold": db_high if db_high is not None else high_threshold,
        "enabled_parent_classes": [],  # open-vocab: no closed set; informational only
        "disabled_parent_classes": [],
        "class_thresholds": merged,
    }


def invalidate_policy_cache() -> None:
    """Drop the cached policy so the next ``active_detection_policy()`` call
    re-reads from the DB. Called by ``PUT /api/inference/confidence-overrides``."""
    active_detection_policy.cache_clear()


def threshold_for_parent(parent_class: str, policy: dict[str, Any] | None = None) -> float:
    policy = policy or active_detection_policy()
    parent = normalize_label(parent_class)
    return float(
        policy.get("class_thresholds", {}).get(parent, policy.get("global_confidence_floor", 0.0))
    )


def detection_decision(
    label: Any,
    confidence: float | int | None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate ``label`` at ``confidence`` against the open-vocab policy.

    Returns a record compatible with the previous closed-taxonomy contract,
    but ``class_enabled`` is always ``True`` and ``review_status`` is never
    ``"below_class_threshold"`` / ``"disabled_distractor"`` unless the operator
    explicitly raised ``GLOBAL_CONFIDENCE_FLOOR`` or set
    ``PER_CLASS_CONFIDENCE_OVERRIDES``.
    """
    policy = policy or active_detection_policy()
    original_class = normalize_label(label)
    parent_class = parent_class_for_label(original_class)
    try:
        conf = max(0.0, min(1.0, float(confidence or 0.0)))
    except (TypeError, ValueError):
        conf = 0.0

    threshold = threshold_for_parent(parent_class, policy)
    if conf < threshold:
        review_status = "below_class_threshold"
    elif conf >= float(policy["high_confidence_threshold"]):
        review_status = "high_confidence"
    else:
        review_status = "review_candidate"

    return {
        "original_class": original_class,
        "parent_class": parent_class,
        "calibrated_confidence": conf,
        "class_threshold": threshold,
        "class_enabled": True,                  # open-vocab → always enabled
        "review_status": review_status,
        "threshold_profile": policy["threshold_profile"],
        "taxonomy_version": policy["taxonomy_version"],
        "model_version": policy["model_version"],
    }


def should_emit_detection(
    label: Any, confidence: float | int | None, policy: dict[str, Any] | None = None
) -> bool:
    """Open-vocab: emit unless the operator explicitly raised the floor."""
    decision = detection_decision(label, confidence, policy)
    return decision["review_status"] != "below_class_threshold"

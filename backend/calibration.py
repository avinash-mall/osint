"""Phase 2.5: lightweight per-model confidence calibration.

Implements single-parameter temperature scaling (Guo et al. 2017,
``On Calibration of Modern Neural Networks``) so different detectors'
confidence distributions can be made comparable before they're fed into NMS
and the per-class confidence floor.

Each detector ships an uncalibrated logit distribution: SAM3 mask scores sit
high (0.6-0.9 routinely), DOTA-OBB sits lower (0.3-0.6 typical), grounding
DINO is wide-tailed. Without calibration, NMS sort-by-confidence systematically
favours SAM3 over DOTA-OBB even when DOTA-OBB is the better detector for that
class.

**This module is intentionally minimal and opt-in.** The actual temperature
values must be computed offline from a held-out validation slice (see
``scripts/measure_calibration_ece.py`` in the plan); this module just applies
them. When no temperature is configured for a model, the function is the
identity transform (T=1.0).

Config sources, in precedence order:
    1. JSON env ``MODEL_TEMPERATURES`` — dict mapping model_version /
       source_layer (substring, case-insensitive) → temperature scalar.
    2. File ``MODEL_TEMPERATURES_FILE`` (default ``/data/calibration/model_temperatures.json``).
    3. No config → identity transform.

Usage::

    from calibration import calibrate_confidence
    cal = calibrate_confidence(raw_score=0.75, model_tag="dota_obb")  # → 0.68
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_FILE = "/data/calibration/model_temperatures.json"


def _load_temperatures() -> tuple[dict[str, float], dict[str, Any]]:
    """Load per-model temperature map + provenance metadata.

    Returns ``(temperatures, metadata)``. Metadata carries the
    ``_measured_at`` / ``_measured_against`` wrapper fields when present so
    callers (admin dashboard) can surface when the active calibration was
    measured. Temperatures are clamped to ``[0.05, 20.0]`` to avoid blow-ups.
    """
    out: dict[str, float] = {}
    meta: dict[str, Any] = {"measured_at": None, "measured_against": None, "source": None}

    def _ingest(raw: Any, source: str) -> None:
        if not isinstance(raw, dict):
            return
        # Accept both shapes:
        #   { "temperatures": { "sam3": 0.8, ... }, "_measured_at": ... }
        #   { "sam3": 0.8, "dota_obb": 1.1, ... }
        # The wrapped form (as shipped from assets/static/calibration/) wins
        # when both keys exist — it lets us carry _README / _measured_at
        # metadata alongside the actual map without polluting the lookup.
        inner = raw.get("temperatures") if isinstance(raw.get("temperatures"), dict) else raw
        for key, value in inner.items():
            if str(key).startswith("_"):
                continue  # metadata key (e.g. _README) in flat-shape case
            try:
                t = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(t):
                continue
            t = max(0.05, min(20.0, t))
            out[str(key).strip().lower()] = t
        if out:
            meta["measured_at"] = raw.get("_measured_at") or meta["measured_at"]
            meta["measured_against"] = raw.get("_measured_against") or meta["measured_against"]
            meta["source"] = source
            logger.info("calibration: loaded %d model temperatures from %s", len(out), source)

    env_raw = (os.getenv("MODEL_TEMPERATURES") or "").strip()
    if env_raw:
        try:
            _ingest(json.loads(env_raw), "MODEL_TEMPERATURES env")
        except json.JSONDecodeError:
            logger.warning("MODEL_TEMPERATURES is not valid JSON; ignoring")

    if not out:
        path = os.getenv("MODEL_TEMPERATURES_FILE", _DEFAULT_FILE)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                _ingest(json.load(fh), path)
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("calibration: failed to read %s: %s", path, exc)

    return out, meta


_TEMPERATURES, _METADATA = _load_temperatures()


def reload_temperatures() -> dict[str, float]:
    """Re-read from env / file. Useful for admin endpoints that update the
    sidecar at runtime."""
    global _TEMPERATURES, _METADATA
    _TEMPERATURES, _METADATA = _load_temperatures()
    return dict(_TEMPERATURES)


def temperature_for(model_tag: str | None) -> float:
    """Return the temperature for the given model tag, or ``1.0`` (identity).

    Lookup is case-insensitive and substring-based so callers can pass either
    a precise model version (``sam3:v1.2``) or a coarse layer tag (``dota_obb``).
    """
    if not model_tag or not _TEMPERATURES:
        return 1.0
    key = str(model_tag).strip().lower()
    if key in _TEMPERATURES:
        return _TEMPERATURES[key]
    for candidate, value in _TEMPERATURES.items():
        if candidate and candidate in key:
            return value
    return 1.0


def calibrate_confidence(raw_score: float | int | None, model_tag: str | None) -> float:
    """Apply temperature scaling to a raw detector score in ``[0, 1]``.

    Formula: ``calibrated = sigmoid(logit(raw) / T)`` where ``T`` is the
    per-model temperature. T=1.0 is identity. T>1 flattens overconfident
    distributions; T<1 sharpens underconfident ones.

    Defensive about edge cases:
      * ``raw_score`` outside [0,1] is clamped before logit
      * ``raw == 0`` and ``raw == 1`` map to themselves (avoids ±∞ logit)
      * non-numeric / NaN / None → 0.0
    """
    try:
        raw = float(raw_score) if raw_score is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(raw):
        return 0.0
    if raw <= 0.0:
        return 0.0
    if raw >= 1.0:
        return 1.0
    t = temperature_for(model_tag)
    if t == 1.0:
        return raw
    # logit / sigmoid pair
    logit = math.log(raw / (1.0 - raw))
    z = logit / t
    return 1.0 / (1.0 + math.exp(-z))


def status() -> dict[str, Any]:
    """Diagnostic summary for ``/health``-style endpoints."""
    return {
        "model_count": len(_TEMPERATURES),
        "models": sorted(_TEMPERATURES.keys()),
        "measured_at": _METADATA.get("measured_at"),
        "measured_against": _METADATA.get("measured_against"),
        "source": _METADATA.get("source"),
    }

"""Open-vocabulary detection policy.

This module keeps open-vocabulary labels, but the default profile is now
precision-first for analyst review: every label can still be represented, while
a nonzero ``GLOBAL_CONFIDENCE_FLOOR`` suppresses low-confidence noise before it
reaches the map. Operators can lower the floor or add per-class overrides.

The public surface (function names + return-shapes) is unchanged so existing
callers in ``backend/worker.py``, ``backend/main.py``, ``backend/tracker.py``
and the four ``inference-*`` services continue to work without edits.

What changed conceptually:

* ``DEFENSE_PARENT_CLASSES`` / ``DISTRACTOR_PARENT_CLASSES`` are gone.
* ``parent_class_for_label`` returns a broad-but-open category (aircraft,
  vessel, vehicle, building, …, **or the normalized label itself** when no
  cluster matches). Nothing is collapsed to ``"unknown"`` any more.
* ``detection_decision`` always returns ``class_enabled=True``. Low-confidence
  rows are marked ``"below_class_threshold"`` when they fall below the active
  global/per-class floor.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any


TAXONOMY_VERSION = os.getenv("DETECTION_TAXONOMY_VERSION", "open-world-v1")
DEFAULT_MODEL_VERSION = os.getenv("MODEL_VERSION", "open-vocab-multi-model")


# Per-parent-class confidence floors. Defaults override the GLOBAL_CONFIDENCE_FLOOR
# for buckets whose measured precision is unacceptable at the global default.
# Source: docs/benchmarks/detection-quality-ontology-mode-2026-05-22.md.
# See docs/decisions/why-transportation-floor-raised.md for the rationale.
#
# Keys are **runtime canonical labels** (output of ``backend.ontology.normalize``,
# i.e. ``_canonical(object.label)`` for object matches or ``_canonical(branch.label)``
# for branch-matcher fallbacks). They are NOT the benchmark's collapsed bucket
# names (``"transportation"``, ``"other"``); those exist only in the offline
# evaluator at ``scripts/eval_metrics/label_normalizer.py``
# (``_BRANCH_ID_TO_CANONICAL``) and never appear as the runtime ``parent_class``.
#
# To populate this dict we (a) enumerate the seed objects under the
# ``Transportation_Terrain`` branch (whose ``parent_class`` is the object's own
# canonical label) and (b) include ``"unknown"`` as the runtime catch-all that
# the benchmark collapses to ``"other"``. See the decision doc for the full
# bucket → runtime-label mapping.
DEFAULT_PER_CLASS_THRESHOLDS: dict[str, float] = {
    # === Benchmark "transportation" bucket — 100% recall / 3.5% precision @ 0.40 ===
    # Runtime canonical labels of every object under the ``Transportation_Terrain``
    # seed branch. The benchmark's ``label_normalizer.normalize`` routes each of
    # these to the ``"transportation"`` bucket via ``_BRANCH_ID_TO_CANONICAL``.
    "expressway_service_area": 0.55,
    "road_bridge":             0.55,
    "railway_bridge":          0.55,
    "bridge":                  0.55,
    "overpass":                0.55,
    "port":                    0.55,
    "interchange":             0.55,
    "roundabout":              0.55,
    "toll_booth":              0.55,
    "border_checkpoint":       0.55,

    # === Benchmark "other" bucket — 22% recall / 27% precision @ 0.40 ===
    # Runtime catch-all is ``parent_class="unknown"`` (the fallback in
    # ``ontology.normalize`` when no branch/object matches AND the input is
    # empty). Raising the unknown floor reduces noise from unmatched
    # open-vocab prompts. Note: the seed normalizer also returns ``"other"``
    # for empty input, so we include it for symmetry/defence-in-depth.
    "unknown": 0.50,
    "other":   0.50,
}


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
    profile_name = os.getenv("DETECTION_THRESHOLD_PROFILE", "defence_precision").strip() or "defence_precision"
    global_floor = float(os.getenv("GLOBAL_CONFIDENCE_FLOOR", "0.40"))
    high_threshold = float(os.getenv("HIGH_CONFIDENCE_THRESHOLD", "0.65"))
    env_overrides = _load_json_thresholds("PER_CLASS_CONFIDENCE_OVERRIDES")
    db_overrides, db_global, db_high = _load_db_overrides()
    # Merge order: code-shipped defaults < env overrides < DB overrides.
    # Operators can lower any default via env or the admin matrix.
    merged = {**DEFAULT_PER_CLASS_THRESHOLDS, **env_overrides, **db_overrides}
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


# ---------------------------------------------------------------------------
# Task 1.2 — generic vs specific label quality.
#
# DOTA-OBB's 18 classes are deliberately generic (e.g. "plane", "ship",
# "large vehicle"). When such a label arrives, ``ontology.normalize`` may
# tie-break the generic prompt to a *specific* defence ontology object label
# (e.g. "Fighter Aircraft"). That promotion is unsafe without a verifier —
# the model only said "plane". This helper triplet lets the persistence layer
# tag detections as ``verified`` / ``inferred`` / ``generic`` so the UI can
# render an honest label instead of a fabricated specific one.
#
# See docs/decisions/why-generic-labels-when-unverified.md.
# ---------------------------------------------------------------------------
# Source of truth: inference-sam3/dota_obb.py module docstring (the 18 DOTA-v1
# class names emitted by the Ultralytics yolo26m-obb checkpoint). We mirror
# the list here because the backend cannot import from the inference service
# (separate container, separate Python env). Keep in sync if the OBB
# checkpoint is swapped or a 19th class lands.
DOTA_OBB_GENERIC_CLASSES: frozenset[str] = frozenset(
    normalize_label(label) for label in (
        "plane",
        "ship",
        "storage tank",
        "baseball diamond",
        "tennis court",
        "basketball court",
        "ground track field",
        "harbor",
        "bridge",
        "large vehicle",
        "small vehicle",
        "helicopter",
        "roundabout",
        "soccer ball field",
        "swimming pool",
        "container crane",
        "airport",
        "helipad",
    )
)


LABEL_VERIFIER_MARGIN_FLOOR = float(os.getenv("LABEL_VERIFIER_MARGIN_FLOOR", "0.10"))


def label_quality_for(detection: dict[str, Any]) -> str:
    """Classify a detection's label confidence as verified / inferred / generic.

    * ``"verified"`` — a verifier (RemoteCLIP or a future fine-grained
      classifier) confirmed the specific label with
      ``semantic_margin >= LABEL_VERIFIER_MARGIN_FLOOR``.
    * ``"generic"``  — the underlying ``source_layer`` is ``"dota_obb"`` and
      ``original_class`` is one of the 18 DOTA-OBB generic classes, and the
      detection is not verified. Promoting the label to a specific defence
      object would be fabrication.
    * ``"inferred"`` — everything else (SAM3 text-prompt detections without
      verifier confirmation, or any other unverified case). The operator typed
      the prompt, so the label is honest, but it's "inferred" until verified.

    Missing fields default to safe values; this function never raises.
    """
    if not isinstance(detection, dict):
        return "inferred"

    try:
        margin = float(detection.get("semantic_margin") or 0.0)
    except (TypeError, ValueError):
        margin = 0.0
    if margin >= LABEL_VERIFIER_MARGIN_FLOOR:
        return "verified"

    source_layer = str(detection.get("source_layer") or "").strip().lower()
    if source_layer == "dota_obb":
        original = normalize_label(detection.get("original_class") or detection.get("class") or "")
        if original in DOTA_OBB_GENERIC_CLASSES:
            return "generic"

    return "inferred"


def display_label_for(
    detection: dict[str, Any],
    normalized: Any,
) -> tuple[str, str]:
    """Resolve the display label + label_quality the UI should render.

    ``normalized`` is a ``backend.ontology.NormalizedLabel`` (or any object
    exposing ``canonical_label`` / ``parent_class`` attributes); accepting
    ``Any`` keeps this module free of an ontology import cycle.

    Returns ``(display_label, label_quality)``:

    * ``verified`` → trust the specific ontology label
      (``normalized.canonical_label``), fall back to ``original_class``.
    * ``generic``  → "{parent_class.title()} (generic)" when a parent bucket
      exists, otherwise the title-cased ``original_class``. We deliberately
      DO NOT use ``normalized.canonical_label`` — that's the fabrication
      this helper exists to suppress.
    * ``inferred`` → prefer ``canonical_label`` (the SAM3 text-prompt match
      is what the operator typed), fall back to the parent bucket or the
      original class. The UI flags this state separately.
    """
    quality = label_quality_for(detection if isinstance(detection, dict) else {})

    det = detection if isinstance(detection, dict) else {}
    original_raw = str(det.get("original_class") or det.get("class") or "").strip()
    parent_raw = str(det.get("parent_class") or "").strip()

    canonical = ""
    parent_from_norm = ""
    if normalized is not None:
        canonical = str(getattr(normalized, "canonical_label", "") or "").strip()
        parent_from_norm = str(getattr(normalized, "parent_class", "") or "").strip()

    parent = parent_raw or parent_from_norm

    if quality == "verified":
        display = canonical or original_raw or "Unknown"
        return display, quality

    if quality == "generic":
        if parent:
            display = f"{parent.replace('_', ' ').title()} (generic)"
        else:
            display = original_raw.replace("_", " ").title() or "Unknown"
        return display, quality

    # inferred
    if canonical:
        display = canonical
    elif parent:
        display = parent.replace("_", " ").title()
    elif original_raw:
        display = original_raw.replace("_", " ").title()
    else:
        display = "Unknown"
    return display, quality

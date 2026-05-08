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


# ---------------------------------------------------------------------------
# Coarse open-vocabulary categories.
#
# These are *clusters* used only for UI grouping / dedupe / threat-routing —
# they are NOT a closed taxonomy. ``parent_class_for_label`` falls back to
# returning the normalized label itself when no cluster matches.
# ---------------------------------------------------------------------------
PARENT_CLASSES: tuple[str, ...] = (
    # Geospatial
    "aircraft", "vessel", "vehicle", "train",
    "building", "infrastructure", "storage_tank",
    "bridge", "harbor", "airfield",
    "recreation", "vegetation", "water",
    # Ground / FMV
    "person", "animal", "food", "furniture", "household",
    "electronic", "tool", "clothing", "plant", "sport",
    # Generic fall-throughs
    "segment", "object",
)


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


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


# Cluster signatures. Order matters — first match wins.
_CLUSTER_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("aircraft", (
        "aircraft", "airplane", "plane", "helicopter", "fixed_wing",
        "boeing", "airbus", "a220", "a321", "a330", "a350", "arj21",
        "c919", "drone", "uav", "glider", "biplane", "jet",
    )),
    ("vessel", (
        "ship", "boat", "vessel", "tanker", "barge", "tug", "ferry",
        "yacht", "hovercraft", "cargo_ship", "fishing_boat",
        "motorboat", "sailboat", "engineering_ship",
    )),
    ("airfield", ("airport", "runway", "airstrip", "airfield", "helipad", "hangar")),
    ("harbor",   ("harbor", "harbour", "port", "shipyard", "dry_dock", "container_crane")),
    ("bridge",   ("bridge", "overpass", "viaduct", "aqueduct")),
    ("storage_tank", ("storage_tank", "storagetank", "oil_tank", "fuel_tank")),
    ("train", (
        "locomotive", "trainstation", "railway", "passenger_car",
        "cargo_car", "flat_car", "train",
    )),
    ("vehicle", (
        "vehicle", "truck", "car", "bus", "van", "trailer", "tractor",
        "excavator", "grader", "bulldozer", "loader", "mixer",
        "stacker", "carrier", "mobile_crane", "haul_truck",
        "pickup_truck", "utility_truck", "small_car", "passenger_car",
        "minivan", "motorcycle", "bicycle",
    )),
    ("building", (
        "building", "facility", "depot", "shed", "hut", "tent",
        "terminal", "warehouse", "house", "residential", "factory",
        "school", "hospital", "office",
    )),
    ("infrastructure", (
        "container", "crane", "chimney", "windmill", "tower", "pylon",
        "substation", "powerplant", "plant", "construction", "roundabout",
        "intersection", "expressway", "pipeline", "antenna", "dish",
        "solar", "transmission",
    )),
    ("recreation", (
        "baseball", "basketball", "tennis", "soccer", "football",
        "golf", "stadium", "swimming_pool", "swimming", "ground_track",
        "groundtrack", "groundtrackfield", "amusement", "park", "zoo",
        "race_track", "court", "field", "playground",
    )),
    ("vegetation", ("forest", "tree", "grassland", "vegetation", "wetlands", "shrub", "hedge")),
    ("water", ("lake", "pond", "river", "stream", "canal", "reservoir", "flood", "water")),
    ("person", ("person", "people", "pedestrian", "rider", "child", "man", "woman")),
    ("animal", (
        "dog", "cat", "horse", "cow", "sheep", "elephant", "bear",
        "zebra", "giraffe", "bird", "animal",
    )),
    ("food", (
        "banana", "apple", "sandwich", "orange", "broccoli", "carrot",
        "hot_dog", "pizza", "donut", "cake", "food", "fruit",
    )),
    ("furniture", (
        "chair", "couch", "potted_plant", "bed", "dining_table",
        "toilet", "tv", "table", "desk", "bench",
    )),
    ("electronic", (
        "laptop", "mouse", "remote", "keyboard", "cell_phone",
        "tablet", "monitor", "printer",
    )),
    ("tool", ("microwave", "oven", "toaster", "sink", "refrigerator", "tool", "scissors")),
    ("clothing", ("hat", "shirt", "jacket", "shoe", "dress", "pants", "tie", "backpack", "umbrella")),
    ("sport", ("ball", "bat", "racket", "skateboard", "surfboard", "skis", "snowboard", "kite", "frisbee")),
)


def parent_class_for_label(label: Any) -> str:
    """Return a coarse cluster for ``label`` or the normalized label itself.

    Open-vocabulary: any prompt SAM3 (or another open-vocab model) emits is
    valid. We never collapse to ``"unknown"``.
    """
    raw = normalize_label(label)
    text = strip_source_prefix(raw)

    if raw in PARENT_CLASSES:
        return raw
    if raw in {"mask", "region"}:
        return "segment"
    if raw == "track":
        return "track"
    if raw.startswith("crop:"):
        return "vegetation"
    if raw in {"flood", "water"}:
        return "water"
    if raw == "burn_scar":
        return "vegetation"

    for parent, terms in _CLUSTER_RULES:
        if text in terms or _has_any(text, terms):
            return parent

    # Open-vocab default: keep the label itself as the class. This is the
    # whole point of "all possible labels".
    return raw


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


@lru_cache(maxsize=1)
def active_detection_policy() -> dict[str, Any]:
    """Open-vocab policy: single global floor, no enabled/disabled lists."""
    profile_name = os.getenv("DETECTION_THRESHOLD_PROFILE", "open").strip() or "open"
    global_floor = float(os.getenv("GLOBAL_CONFIDENCE_FLOOR", "0.0"))
    high_threshold = float(os.getenv("HIGH_CONFIDENCE_THRESHOLD", "0.5"))
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "model_version": DEFAULT_MODEL_VERSION,
        "threshold_profile": profile_name,
        "global_confidence_floor": global_floor,
        "high_confidence_threshold": high_threshold,
        "enabled_parent_classes": list(PARENT_CLASSES),  # purely informational
        "disabled_parent_classes": [],
        "class_thresholds": _load_json_thresholds("PER_CLASS_CONFIDENCE_OVERRIDES"),
    }


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

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any


TAXONOMY_VERSION = os.getenv("DETECTION_TAXONOMY_VERSION", "optical-defense-v1")
DEFAULT_MODEL_VERSION = os.getenv("MODEL_VERSION", "geoint-yolov8-obb-optical-defense")

DEFENSE_PARENT_CLASSES = (
    "aircraft",
    "ship",
    "vehicle",
    "military_vehicle",
    "storage_tank",
    "bridge",
    "harbor",
    "airfield",
    "building",
    "infrastructure",
)

DISTRACTOR_PARENT_CLASSES = ("dam", "recreation", "water", "unknown")
ALL_PARENT_CLASSES = DEFENSE_PARENT_CLASSES + DISTRACTOR_PARENT_CLASSES

SOURCE_PREFIXES = (
    "xview",
    "dota",
    "fair1m",
    "fmow",
    "rareplanes",
    "dior",
    "sodaa",
    "hrsc",
    "hrsc2016",
    "local",
)

RECREATION_TERMS = (
    "baseball",
    "basketball",
    "tennis",
    "soccer",
    "football",
    "golf",
    "stadium",
    "swimming_pool",
    "swimming",
    "ground_track",
    "groundtrack",
    "groundtrackfield",
    "baseballfield",
    "basketballcourt",
    "tenniscourt",
    "golffield",
    "soccer_ball_field",
    "amusement",
    "park",
    "zoo",
    "race_track",
)


THRESHOLD_PROFILES: dict[str, dict[str, Any]] = {
    "recall_review": {
        "global_confidence_floor": 0.10,
        "high_confidence_threshold": 0.55,
        "enabled_parent_classes": DEFENSE_PARENT_CLASSES,
        "class_thresholds": {
            "aircraft": 0.12,
            "ship": 0.12,
            "vehicle": 0.06,
            "military_vehicle": 0.10,
            "storage_tank": 0.15,
            "bridge": 0.18,
            "harbor": 0.18,
            "airfield": 0.20,
            "building": 0.25,
            "infrastructure": 0.25,
        },
    },
    "balanced": {
        "global_confidence_floor": 0.18,
        "high_confidence_threshold": 0.65,
        "enabled_parent_classes": DEFENSE_PARENT_CLASSES,
        "class_thresholds": {
            "aircraft": 0.22,
            "ship": 0.22,
            "vehicle": 0.20,
            "military_vehicle": 0.18,
            "storage_tank": 0.25,
            "bridge": 0.28,
            "harbor": 0.30,
            "airfield": 0.30,
            "building": 0.35,
            "infrastructure": 0.35,
        },
    },
    "high_precision": {
        "global_confidence_floor": 0.35,
        "high_confidence_threshold": 0.75,
        "enabled_parent_classes": DEFENSE_PARENT_CLASSES,
        "class_thresholds": {
            "aircraft": 0.45,
            "ship": 0.45,
            "vehicle": 0.42,
            "military_vehicle": 0.38,
            "storage_tank": 0.48,
            "bridge": 0.50,
            "harbor": 0.52,
            "airfield": 0.52,
            "building": 0.60,
            "infrastructure": 0.60,
        },
    },
}


def normalize_label(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"


def strip_source_prefix(label: str) -> str:
    normalized = normalize_label(label)
    for prefix in SOURCE_PREFIXES:
        marker = f"{prefix}_"
        if normalized.startswith(marker):
            return normalized[len(marker):]
    return normalized


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def parent_class_for_label(label: Any) -> str:
    raw = normalize_label(label)
    text = strip_source_prefix(raw)
    if raw in ALL_PARENT_CLASSES:
        return raw

    if raw.startswith("hrsc") or _has_any(text, ("warship", "warcraft", "destroyer", "frigate", "cruiser", "submarine")):
        return "ship"
    if text == "dam" or text.startswith("dam_") or text.endswith("_dam"):
        return "dam"
    if _has_any(text, RECREATION_TERMS):
        return "recreation"
    if _has_any(text, ("lake", "pond", "water_treatment", "flooded")):
        return "water"
    if _has_any(text, ("missile", "launcher", "artillery", "sam", "armored", "armoured")):
        return "military_vehicle"
    if text == "tank" or text.endswith("_tank") and "storage" not in text and "oil" not in text and "tank_car" not in text:
        return "military_vehicle"
    if _has_any(text, ("storage_tank", "storagetank")):
        return "storage_tank"
    if _has_any(text, ("shipping_container", "container_lot", "container_crane")):
        return "infrastructure"
    if (
        _has_any(text, ("aircraft_carrier", "vessel", "boat", "tanker", "barge", "tug", "ferry", "yacht", "hovercraft", "warship", "cargo_ship", "engineering_ship", "fishing_boat", "motorboat", "sailboat"))
        or text == "ship"
        or text.startswith("ship_")
        or text.endswith("_ship")
    ):
        return "ship"
    if _has_any(text, ("aircraft", "airplane", "plane", "helicopter", "fixed_wing", "boeing", "airbus", "a220", "a321", "a330", "a350", "arj21", "c919")):
        return "aircraft"
    if _has_any(text, ("airport", "runway", "airstrip", "airfield", "helipad", "hangar")):
        return "airfield"
    if _has_any(text, ("harbor", "harbour", "port", "shipyard", "dry_dock")):
        return "harbor"
    if _has_any(text, ("bridge", "overpass")):
        return "bridge"
    if _has_any(text, ("vehicle", "truck", "car", "bus", "van", "trailer", "tractor", "locomotive", "railway", "excavator", "grader", "bulldozer", "loader", "mixer", "stacker", "carrier", "mobile_crane")):
        return "vehicle"
    if _has_any(text, ("building", "facility", "depot", "bunker", "shed", "hut", "tent", "terminal")):
        return "building"
    if _has_any(text, ("container", "crane", "chimney", "windmill", "tower", "pylon", "substation", "powerplant", "factory", "plant", "construction", "roundabout", "intersection", "trainstation", "expressway")):
        return "infrastructure"
    return "unknown"


def _parse_csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw:
        return default
    values = tuple(normalize_label(item) for item in raw.split(",") if item.strip())
    return values or default


def _load_json_thresholds(name: str) -> dict[str, float]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, float] = {}
    for key, value in parsed.items():
        try:
            result[normalize_label(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return result


@lru_cache(maxsize=1)
def active_detection_policy() -> dict[str, Any]:
    profile_name = os.getenv("DETECTION_THRESHOLD_PROFILE", "recall_review").strip() or "recall_review"
    profile = THRESHOLD_PROFILES.get(profile_name, THRESHOLD_PROFILES["recall_review"])
    enabled = set(_parse_csv_env("ENABLED_PARENT_CLASSES", tuple(profile["enabled_parent_classes"])))
    disabled = set(_parse_csv_env("DISABLED_PARENT_CLASSES", DISTRACTOR_PARENT_CLASSES))
    thresholds = {normalize_label(k): float(v) for k, v in profile.get("class_thresholds", {}).items()}
    thresholds.update(_load_json_thresholds("PER_CLASS_CONFIDENCE_OVERRIDES"))
    global_floor_env = os.getenv("GLOBAL_CONFIDENCE_FLOOR")
    global_floor = float(global_floor_env) if global_floor_env else profile["global_confidence_floor"]
    high_threshold_env = os.getenv("HIGH_CONFIDENCE_THRESHOLD")
    high_threshold = float(high_threshold_env) if high_threshold_env else profile["high_confidence_threshold"]
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "model_version": DEFAULT_MODEL_VERSION,
        "threshold_profile": profile_name,
        "global_confidence_floor": global_floor,
        "high_confidence_threshold": high_threshold,
        "enabled_parent_classes": sorted(enabled),
        "disabled_parent_classes": sorted(disabled),
        "class_thresholds": thresholds,
    }


def threshold_for_parent(parent_class: str, policy: dict[str, Any] | None = None) -> float:
    policy = policy or active_detection_policy()
    parent = normalize_label(parent_class)
    return float(policy.get("class_thresholds", {}).get(parent, policy.get("global_confidence_floor", 0.1)))


def detection_decision(label: Any, confidence: float | int | None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or active_detection_policy()
    original_class = normalize_label(label)
    parent_class = parent_class_for_label(original_class)
    try:
        conf = max(0.0, min(1.0, float(confidence or 0.0)))
    except (TypeError, ValueError):
        conf = 0.0

    enabled = parent_class in set(policy["enabled_parent_classes"]) and parent_class not in set(policy["disabled_parent_classes"])
    threshold = threshold_for_parent(parent_class, policy)
    if not enabled:
        review_status = "disabled_distractor"
    elif conf < threshold:
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
        "class_enabled": enabled,
        "review_status": review_status,
        "threshold_profile": policy["threshold_profile"],
        "taxonomy_version": policy["taxonomy_version"],
        "model_version": policy["model_version"],
    }


def should_emit_detection(label: Any, confidence: float | int | None, policy: dict[str, Any] | None = None) -> bool:
    decision = detection_decision(label, confidence, policy)
    return decision["class_enabled"] and decision["review_status"] != "below_class_threshold"

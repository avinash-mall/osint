"""Pure candidate-link scoring shared by API, worker, and eval tooling."""

from __future__ import annotations

import math
from typing import Any, Callable


W_DISTANCE = 0.30
W_COMPAT = 0.30
W_CONFIDENCE = 0.30
W_HISTORY = 0.10
DEFAULT_MAX_CANDIDATES = 5


def target_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _clean_detection_class(det_class: str) -> str:
    return " ".join(str(det_class or "").replace("_", " ").replace("-", " ").lower().split())


def _category_hints(det_text: str) -> tuple[str, ...]:
    if any(token in det_text for token in ("ship", "vessel", "frigate", "destroyer", "boat")):
        return ("naval", "maritime")
    if any(token in det_text for token in ("jet", "plane", "aircraft", "helicopter")):
        return ("aircraft", "airfield", "airbase")
    if any(token in det_text for token in ("tank", "armored", "infantry fighting")):
        return ("armored", "military forces")
    if any(token in det_text for token in ("rocket launcher", "howitzer", "artillery")):
        return ("artillery", "military forces")
    if any(token in det_text for token in ("truck", "fuel", "ammo", "ammunition")):
        return ("logistics",)
    return ()


def target_class_compatibility(det_class: str, target_props: dict[str, Any]) -> tuple[float, str]:
    det_text = _clean_detection_class(det_class)
    target_text = " ".join(
        str(target_props.get(key, "")) for key in ("name", "type", "category", "description")
    ).lower()
    if not target_text:
        return 0.40, "target context sparse"
    if any(token in target_text for token in det_text.split() if len(token) >= 4):
        return 1.00, "class/name text overlap"
    if any(hint in target_text for hint in _category_hints(det_text)):
        return 0.70, "category overlap"
    return 0.20, "generic proximity match"


def score_candidate_link(
    detection: dict[str, Any],
    target: dict[str, Any],
    *,
    max_distance_m: float,
    history_anchor: float = 0.0,
) -> dict[str, Any] | None:
    distance_m = target_distance_m(
        float(detection["lat"]),
        float(detection["lon"]),
        float(target["lat"]),
        float(target["lon"]),
    )
    if distance_m > max_distance_m:
        return None
    compatibility_score, compatibility_reason = target_class_compatibility(
        str(detection.get("class") or ""),
        target.get("props") or target,
    )
    confidence = max(0.0, min(1.0, float(detection.get("confidence") or 0.0)))
    history = max(0.0, min(1.0, float(history_anchor or 0.0)))
    distance_norm = max(0.0, 1.0 - (distance_m / max_distance_m))
    score = round(
        W_DISTANCE * distance_norm
        + W_COMPAT * compatibility_score
        + W_CONFIDENCE * confidence
        + W_HISTORY * history,
        3,
    )
    return {
        "score": score,
        "distance_m": distance_m,
        "compatibility_score": compatibility_score,
        "compatibility_reason": compatibility_reason,
        "history_anchor": history,
        "detection_confidence": confidence,
        "score_weights": {
            "distance": W_DISTANCE,
            "compatibility": W_COMPAT,
            "confidence": W_CONFIDENCE,
            "history": W_HISTORY,
        },
    }


def rank_candidate_links(
    detection: dict[str, Any],
    targets: list[dict[str, Any]],
    *,
    max_distance_m: float = 1500.0,
    max_candidates_per_detection: int = DEFAULT_MAX_CANDIDATES,
    history_lookup: Callable[[str], float] | None = None,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for target in targets:
        target_id = str(target.get("stable_id") or target.get("element_id") or "")
        if not target_id:
            continue
        result = score_candidate_link(
            detection,
            target,
            max_distance_m=max_distance_m,
            history_anchor=history_lookup(target_id) if history_lookup else 0.0,
        )
        if result is None:
            continue
        scored.append({
            "target_id": target_id,
            "target_name": target.get("name") or target_id,
            "reason": (
                f"{round(result['distance_m'])}m from target; "
                f"{result['compatibility_reason']}; "
                f"confidence {result['detection_confidence']:.2f}; "
                f"history {result['history_anchor']:.2f}"
            ),
            **result,
        })
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[: max(1, int(max_candidates_per_detection))]

"""
label_normalizer.py
===================
Maps (label, source_layer) pairs to a canonical branch string drawn from
the defence ontology's top-level branch IDs (lowercased, underscores).

Canonical branches (derived from defenceOntology.json branch IDs):
    military_forces         – general military (fallback when sub-branch unclear)
    armored_vehicle         – tanks, APCs, IFVs …
    artillery               – SPGs, towed guns, rocket launchers
    tactical_vehicle        – cargo/fuel trucks, command vehicles
    air_defense             – SAM systems, radar, AAA, EW
    missile_strategic       – TELs, ICBMs, silos
    military_installation   – bases, FOBs, training areas
    logistics               – depots, container yards, rail
    aircraft                – fixed-wing and rotary-wing aircraft + airfields
    naval                   – warships, merchant ships, ports, harbours
    fortification           – trenches, bunkers, obstacles
    activity_change         – smoke, fire, burn scars
    industrial              – factories, power plants, storage tanks
    transportation          – bridges, roads, railways, roundabouts
    urban                   – buildings, residential, public facilities
    battle_damage           – craters, damaged structures, wreckage
    auxiliary               – Prithvi EO outputs (crops, floods)
    civilian                – civilian-only features (sports courts, pools)
    other                   – catch-all

Priority order when matching:
    1. DOTA-v1.0 explicit mapping (if layer == "dota_obb")
    2. Exact match against prompt→branch lookup built from the ontology JSON
    3. Case-insensitive substring match against all prompts
    4. Fallback: "other"
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Resolve the ontology JSON relative to this file's location.
# This module lives at:   <repo_root>/scripts/eval_metrics/label_normalizer.py
# The ontology lives at:  <repo_root>/frontend/src/utils/defenceOntology.json
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_ONTOLOGY_PATH = os.path.join(
    _REPO_ROOT, "frontend", "src", "utils", "defenceOntology.json"
)

# ---------------------------------------------------------------------------
# Branch ID → canonical name mapping
# We collapse the 14 top-level branches into human-friendly snake_case names.
# Children of Military_Forces are promoted to their own canonical names so
# that sub-branch resolution is as precise as possible.
# ---------------------------------------------------------------------------
_BRANCH_ID_TO_CANONICAL: Dict[str, str] = {
    # top-level branches
    "Military_Forces": "military_forces",
    # children of Military_Forces (promoted)
    "Armored_Vehicles": "armored_vehicle",
    "Artillery": "artillery",
    "Tactical_Vehicles": "tactical_vehicle",
    # other top-level branches
    "Air_Defense_EW": "air_defense",
    "Missile_Strategic": "missile_strategic",
    "Military_Installations": "military_installation",
    "Logistics": "logistics",
    "Airfield_Aviation": "aircraft",
    "Naval_Maritime": "naval",
    "Fortifications_Obstacles": "fortification",
    "Activity_Change": "activity_change",
    "Industrial_Dual_Use": "industrial",
    "Transportation_Terrain": "transportation",
    "Urban_Tactical": "urban",
    "Battle_Damage": "battle_damage",
    "Auxiliary": "auxiliary",
}

# ---------------------------------------------------------------------------
# DOTA-v1.0 class name → canonical branch  (hardcoded)
# All 18 DOTA-v1.0 classes are covered explicitly.
# ---------------------------------------------------------------------------
_DOTA_MAP: Dict[str, str] = {
    "plane": "aircraft",
    "ship": "naval",
    "storage-tank": "logistics",
    "baseball-diamond": "civilian",
    "tennis-court": "civilian",
    "basketball-court": "civilian",
    "ground-track-field": "civilian",
    "harbor": "naval",
    "bridge": "transportation",
    "large-vehicle": "logistics",
    "small-vehicle": "logistics",
    "helicopter": "aircraft",
    "roundabout": "transportation",
    "soccer-ball-field": "civilian",
    "swimming-pool": "civilian",
    "container-crane": "logistics",
    "airport": "aircraft",
    "helipad": "aircraft",
}

# ---------------------------------------------------------------------------
# Build the prompt → canonical branch lookup from the ontology JSON at import
# time.  We walk every branch (and its children) collecting each object's
# "prompt" field.  Multiple branches may share the same prompt string; we
# keep the first mapping encountered (depth-first, children before siblings).
# ---------------------------------------------------------------------------
def _build_prompt_lookup(
    ontology_path: str,
) -> tuple[Dict[str, str], list[tuple[str, str]]]:
    """Return (exact_lookup, substring_pairs) from the ontology JSON.

    exact_lookup  : prompt (lowercase) → canonical branch
    substring_pairs: list of (prompt_lowercase, canonical_branch) for
                     substring matching (ordered, longest prompt first so
                     more-specific prompts win).
    """
    exact: Dict[str, str] = {}
    pairs: list[tuple[str, str]] = []

    try:
        with open(ontology_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return exact, pairs

    def _walk_branch(branch: dict, canonical: str) -> None:
        # Collect objects at this level
        for obj in branch.get("objects", []):
            prompt_raw: Optional[str] = obj.get("prompt")
            if not prompt_raw or prompt_raw.startswith("__prithvi_"):
                continue
            p = prompt_raw.lower().strip()
            if p and p not in exact:
                exact[p] = canonical
                pairs.append((p, canonical))

        # Recurse into children; children that have their own canonical name
        # take priority.
        for child in branch.get("children", []):
            child_canonical = _BRANCH_ID_TO_CANONICAL.get(
                child.get("id", ""), canonical
            )
            _walk_branch(child, child_canonical)

    for branch in data.get("branches", []):
        branch_id: str = branch.get("id", "")
        canonical = _BRANCH_ID_TO_CANONICAL.get(branch_id, "other")
        _walk_prompt_branch(branch, canonical, exact, pairs)

    # Sort pairs longest-first so substring matching prefers specific prompts
    pairs.sort(key=lambda t: len(t[0]), reverse=True)
    return exact, pairs


def _walk_prompt_branch(
    branch: dict,
    canonical: str,
    exact: Dict[str, str],
    pairs: list[tuple[str, str]],
) -> None:
    """Recursive helper that populates exact and pairs in-place."""
    for obj in branch.get("objects", []):
        prompt_raw: Optional[str] = obj.get("prompt")
        if not prompt_raw or prompt_raw.startswith("__prithvi_"):
            continue
        p = prompt_raw.lower().strip()
        if p and p not in exact:
            exact[p] = canonical
            pairs.append((p, canonical))

    for child in branch.get("children", []):
        child_id: str = child.get("id", "")
        child_canonical = _BRANCH_ID_TO_CANONICAL.get(child_id, canonical)
        _walk_prompt_branch(child, child_canonical, exact, pairs)


# Module-level lookup tables built once at import time.
_PROMPT_EXACT: Dict[str, str]
_PROMPT_PAIRS: list[tuple[str, str]]
_PROMPT_EXACT, _PROMPT_PAIRS = _build_prompt_lookup(_ONTOLOGY_PATH)

# ---------------------------------------------------------------------------
# Additional keyword → canonical mappings for DEFENCE_YOLO label names that
# may not appear verbatim as ontology prompts.
# ---------------------------------------------------------------------------
_KEYWORD_MAP: list[tuple[str, str]] = [
    # DEFENCE_YOLO category names (case-insensitive substring matches)
    ("tank", "armored_vehicle"),
    ("ifv", "armored_vehicle"),
    ("apc", "armored_vehicle"),
    ("mrap", "armored_vehicle"),
    ("armored", "armored_vehicle"),
    ("armoured", "armored_vehicle"),
    ("artillery", "artillery"),
    ("rocket launcher", "artillery"),
    ("howitzer", "artillery"),
    ("mortar", "artillery"),
    ("helicopter", "aircraft"),
    ("fixed-wing", "aircraft"),
    ("aircraft", "aircraft"),
    ("drone", "aircraft"),
    ("uav", "aircraft"),
    ("plane", "aircraft"),
    ("jet", "aircraft"),
    ("fighter", "aircraft"),
    ("bomber", "aircraft"),
    ("ship", "naval"),
    ("vessel", "naval"),
    ("submarine", "naval"),
    ("destroyer", "naval"),
    ("frigate", "naval"),
    ("carrier", "naval"),
    ("harbor", "naval"),
    ("harbour", "naval"),
    ("truck", "logistics"),
    ("vehicle", "military_forces"),
    ("radar", "air_defense"),
    ("missile", "missile_strategic"),
    ("launcher", "missile_strategic"),
    ("bridge", "transportation"),
    ("airport", "aircraft"),
    ("helipad", "aircraft"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(label: str, layer: str) -> str:
    """Map (label, layer) → canonical branch string.

    Never raises; always returns a non-empty string.  Falls back to "other".

    Parameters
    ----------
    label : str
        The raw label string from the detection (any casing).
    layer : str
        The inference layer that produced the detection, e.g. "dota_obb",
        "sam3", "defence_yolo", "grounding_dino".

    Returns
    -------
    str
        Canonical branch name (lowercase, underscores), e.g. "armored_vehicle".
    """
    if not label:
        return "other"

    label_lower = label.lower().strip()
    layer_lower = (layer or "").lower().strip()

    # ------------------------------------------------------------------
    # 1. DOTA-v1.0 explicit mapping
    # ------------------------------------------------------------------
    if layer_lower == "dota_obb":
        if label_lower in _DOTA_MAP:
            return _DOTA_MAP[label_lower]
        # Normalise dashes/underscores and retry
        normalised = label_lower.replace("_", "-")
        if normalised in _DOTA_MAP:
            return _DOTA_MAP[normalised]
        # Unknown DOTA class → other
        return "other"

    # ------------------------------------------------------------------
    # 2. Exact match against ontology prompt lookup
    # ------------------------------------------------------------------
    if label_lower in _PROMPT_EXACT:
        return _PROMPT_EXACT[label_lower]

    # ------------------------------------------------------------------
    # 3. Substring match: label contains a known prompt (or vice-versa)
    # ------------------------------------------------------------------
    # 3a. Does the label contain any known prompt as a substring?
    for prompt, canonical in _PROMPT_PAIRS:
        if prompt in label_lower:
            return canonical

    # 3b. Does any known prompt contain the label as a substring?
    for prompt, canonical in _PROMPT_PAIRS:
        if label_lower in prompt:
            return canonical

    # ------------------------------------------------------------------
    # 4. Keyword map (covers DEFENCE_YOLO category names and synonyms)
    # ------------------------------------------------------------------
    for keyword, canonical in _KEYWORD_MAP:
        if keyword in label_lower:
            return canonical

    # ------------------------------------------------------------------
    # 5. Fallback
    # ------------------------------------------------------------------
    return "other"

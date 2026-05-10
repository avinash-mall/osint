"""
label_normalizer.py
===================
Backwards-compat wrapper around ``backend.ontology.normalize()``.

The DB-backed ontology in ``backend/ontology.py`` is the canonical
classifier. This module exists so legacy scripts (and the eval-metrics
test fixtures) that import ``scripts.eval_metrics.label_normalizer`` keep
working — it simply delegates to the canonical normalizer and maps the
returned ``branch_id`` back to the historical lowercase canonical name
(e.g. ``aircraft``, ``naval``) used by older eval reports.

Prefer ``from backend.ontology import normalize`` in new code.
"""

from __future__ import annotations

from typing import Dict

# ---------------------------------------------------------------------------
# Branch ID -> historical lowercase canonical name. The wrapper returns these
# strings so legacy eval reports retain identical labels. Kept in sync with
# the seed ontology; unknown branch IDs fall back to a lowercased copy.
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


def normalize(label: str, layer: str = "") -> str:
    """Backwards-compat wrapper around ``backend.ontology.normalize()``.

    Returns the canonical branch_id mapped to the historical lowercase
    canonical name (e.g. ``aircraft``, ``naval``). Prefer calling
    ``backend.ontology.normalize()`` directly in new code.
    """
    # Make `backend` (package) and its sibling `database` module importable
    # from a script invoked from anywhere. backend/ontology.py uses a flat
    # ``from database import postgis_db`` so we need backend/ on sys.path too.
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent
    backend_dir = repo_root / "backend"
    for p in (str(repo_root), str(backend_dir)):
        if p not in sys.path:
            sys.path.insert(0, p)
    from backend.ontology import normalize as _normalize
    result = _normalize(label or "", layer=layer or "")
    return _BRANCH_ID_TO_CANONICAL.get(result.branch_id, result.branch_id.lower())

"""Pure, seed-backed ontology normalizer for offline evaluation tooling.

Runtime code uses :mod:`backend.ontology`, whose cache is intentionally backed
by PostGIS. Evaluation and dry-run commands need the same vocabulary without a
database, so this module reads the checked-in ontology seed and mirrors the
runtime matching order: exact object label/id, exact prompt, then ordered branch
regexes. A tiny DOTA alias table covers dataset labels that are broader than the
seed's operator-facing prompts (for example ``plane``).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

_BRANCH_ID_TO_CANONICAL: Dict[str, str] = {
    "Military_Forces": "military_forces",
    "Armored_Vehicles": "armored_vehicle",
    "Artillery": "artillery",
    "Tactical_Vehicles": "tactical_vehicle",
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
}

_DOTA_ALIASES = {
    "plane": "Airfield_Aviation",
    "ship": "Naval_Maritime",
    "large_vehicle": "Logistics",
    "small_vehicle": "Logistics",
}

_SEED_PATH = Path(__file__).resolve().parents[2] / "backend" / "scripts" / "seeds" / "defenceOntology.seed.json"


def _canonical(text: Any) -> str:
    if text is None:
        return ""
    value = str(text).strip().lower()
    value = re.sub(r"[\s\-]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_")


def _strip_source_prefix(text: str) -> str:
    if ":" in text:
        head, tail = text.split(":", 1)
        if head and tail and re.fullmatch(r"[a-z0-9_]+", head):
            return tail
    return text


@lru_cache(maxsize=1)
def _seed_index() -> dict[str, Any]:
    seed = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    objects_by_label: dict[str, str] = {}
    objects_by_prompt: dict[str, str] = {}
    branch_matchers: list[tuple[int, str, list[re.Pattern[str]]]] = []
    order = 0

    def visit(branch: dict[str, Any]) -> None:
        nonlocal order
        branch_id = str(branch["id"])
        patterns: list[re.Pattern[str]] = []
        for raw in branch.get("matchers") or []:
            try:
                patterns.append(re.compile(str(raw), re.IGNORECASE))
            except re.error:
                continue
        branch_matchers.append((order, branch_id, patterns))
        order += 1
        for obj in branch.get("objects") or []:
            for raw in (obj.get("label"), obj.get("id")):
                key = _canonical(raw)
                if key:
                    objects_by_label.setdefault(key, branch_id)
            prompt_key = _canonical(obj.get("prompt"))
            if prompt_key:
                objects_by_prompt.setdefault(prompt_key, branch_id)
        for child in branch.get("children") or []:
            visit(child)

    for branch in seed.get("branches") or []:
        visit(branch)

    return {
        "objects_by_label": objects_by_label,
        "objects_by_prompt": objects_by_prompt,
        "branch_matchers": branch_matchers,
    }


def normalize(label: str, layer: str = "") -> str:
    """Return the historical lowercase branch name without touching PostGIS."""
    canon = _canonical(label)
    if not canon:
        return "other"
    canon_no_prefix = _canonical(_strip_source_prefix(canon))

    if (layer or "").lower() == "dota_obb":
        alias = _DOTA_ALIASES.get(canon_no_prefix)
        if alias:
            return _BRANCH_ID_TO_CANONICAL[alias]

    index = _seed_index()
    for key in (canon, canon_no_prefix):
        branch_id = index["objects_by_label"].get(key) or index["objects_by_prompt"].get(key)
        if branch_id:
            return _BRANCH_ID_TO_CANONICAL.get(branch_id, branch_id.lower())

    candidates = {
        canon.replace("_", " "),
        canon_no_prefix.replace("_", " "),
        canon,
        canon_no_prefix,
    }
    for _order, branch_id, patterns in index["branch_matchers"]:
        if any(pattern.search(candidate) for pattern in patterns for candidate in candidates if candidate):
            return _BRANCH_ID_TO_CANONICAL.get(branch_id, branch_id.lower())
    return "other"

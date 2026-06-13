"""Offline unit tests for the branch-based ``category_for_class`` mapping.

Regression guard for the 2026-06-12 audit fix: ``category_for_class`` matched
``parent_class`` against tiny string sets ("aircraft", "vessel", …) but the
runtime ``parent_class`` is the ontology object's own canonical label
("destroyer", "boeing_737", …), so almost every seeded label fell through to
"object" — killing the per-category tracker V_MAX gates and Kalman noise.
The fix maps the NormalizedLabel's ``branch_id`` (seed branch ids from
``backend/scripts/seeds/defenceOntology.seed.json``) first, keeping the
parent-string sets as a fallback for unknown branches.

``ontology.normalize`` is monkeypatched so no DB is required.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import ontology  # noqa: E402
import threat_assessment  # noqa: E402
import tracker  # noqa: E402
from ontology import NormalizedLabel  # noqa: E402


def _fake_normalize(monkeypatch, mapping: dict[str, tuple[str, str]]) -> None:
    """mapping: label -> (branch_id, parent_class)."""

    def fake(label, layer=""):
        branch_id, parent = mapping.get(
            str(label), ("Other", str(label).lower().replace(" ", "_"))
        )
        return NormalizedLabel(
            branch_id=branch_id,
            parent_class=parent,
            canonical_label=str(label),
            ontology_object_id=None,
            icon_key="circle_help",
            was_unknown=branch_id == "Other",
        )

    monkeypatch.setattr(ontology, "normalize", fake)


@pytest.mark.parametrize(
    "label,branch_id,parent,expected",
    [
        ("destroyer", "Naval_Maritime", "destroyer", "maritime"),
        ("cargo_plane", "Airfield_Aviation", "cargo_plane", "air"),
        ("boeing_737", "Airfield_Aviation", "boeing_737", "air"),
        ("main_battle_tank", "Armored_Vehicles", "main_battle_tank", "ground"),
        ("missile_launcher_vehicle", "SAM_System", "missile_launcher_vehicle", "ground"),
        ("locomotive", "Logistics", "locomotive", "ground"),
        ("storage_tank", "Industrial_Dual_Use", "storage_tank", "infrastructure"),
        ("road_bridge", "Transportation_Terrain", "road_bridge", "infrastructure"),
        ("hospital", "Urban_Tactical", "hospital", "infrastructure"),
        # Branch-matcher fallback path: parent_class is the canonical branch label.
        ("naval thing", "Naval_Maritime", "naval_/_maritime", "maritime"),
    ],
)
def test_branch_mapping(monkeypatch, label, branch_id, parent, expected):
    _fake_normalize(monkeypatch, {label: (branch_id, parent)})
    assert threat_assessment.category_for_class(label) == expected


def test_unknown_branch_falls_back_to_parent_string_sets(monkeypatch):
    _fake_normalize(
        monkeypatch,
        {
            "vehicle": ("Unmapped_Branch", "vehicle"),
            "ship": ("Unmapped_Branch", "ship"),
            "person": ("Other", "person"),
            "novel_widget": ("Other", "novel_widget"),
        },
    )
    assert threat_assessment.category_for_class("vehicle") == "ground"
    assert threat_assessment.category_for_class("ship") == "maritime"
    assert threat_assessment.category_for_class("person") == "person"
    assert threat_assessment.category_for_class("novel_widget") == "object"


def test_tracker_category_picks_up_branch_categories(monkeypatch):
    _fake_normalize(
        monkeypatch,
        {
            "destroyer": ("Naval_Maritime", "destroyer"),
            "cargo_plane": ("Airfield_Aviation", "cargo_plane"),
            "tank": ("Armored_Vehicles", "tank"),
            "military_facility": ("Military_Installations", "military_facility"),
        },
    )
    assert tracker._tracker_category("destroyer") == "maritime"
    assert tracker._tracker_category("cargo_plane") == "air"
    assert tracker._tracker_category("tank") == "ground"
    assert tracker._tracker_category("military_facility") == "infrastructure"

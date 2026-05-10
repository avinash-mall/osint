"""
Unit tests for label_normalizer.normalize().
Run from the repo root:
    python -m pytest scripts/eval_metrics/tests/ -q
"""
import pytest
from scripts.eval_metrics.label_normalizer import normalize


# ---------------------------------------------------------------------------
# DOTA-v1.0 explicit mapping tests
# ---------------------------------------------------------------------------

def test_dota_plane_maps_to_aircraft():
    assert normalize("plane", "dota_obb") == "aircraft"


def test_dota_ship_maps_to_naval():
    assert normalize("ship", "dota_obb") == "naval"


def test_dota_large_vehicle_maps_to_logistics():
    assert normalize("large-vehicle", "dota_obb") == "logistics"


def test_dota_small_vehicle_maps_to_logistics():
    assert normalize("small-vehicle", "dota_obb") == "logistics"


def test_dota_storage_tank_maps_to_logistics_or_industrial():
    # DB ontology classifies storage tanks as industrial (Storage_Tank object
    # in Industrial_Dual_Use). Logistics is also acceptable for fuel/POL depots.
    assert normalize("storage-tank", "dota_obb") in ("logistics", "industrial")


def test_dota_harbor_maps_to_naval():
    assert normalize("harbor", "dota_obb") == "naval"


def test_dota_helicopter_maps_to_aircraft():
    assert normalize("helicopter", "dota_obb") == "aircraft"


def test_dota_bridge_maps_to_transportation():
    assert normalize("bridge", "dota_obb") == "transportation"


def test_dota_airport_maps_to_aircraft():
    assert normalize("airport", "dota_obb") == "aircraft"


def test_dota_helipad_maps_to_aircraft():
    assert normalize("helipad", "dota_obb") == "aircraft"


def test_dota_container_crane_maps_to_logistics_or_industrial():
    # DB ontology classifies the container-crane object under Industrial_Dual_Use
    # (Heavy Crane). Logistics is also acceptable for port-handling cranes.
    assert normalize("container-crane", "dota_obb") in ("logistics", "industrial")


def test_dota_roundabout_maps_to_transportation():
    assert normalize("roundabout", "dota_obb") == "transportation"


def test_dota_baseball_diamond_maps_to_other():
    # The DB ontology (defence-focused) has no Civilian/Recreation branch yet,
    # so DOTA recreational classes correctly fall through to "other". A future
    # ontology revision can add a Recreation branch with these matchers.
    assert normalize("baseball-diamond", "dota_obb") in ("civilian", "other")


def test_dota_tennis_court_maps_to_other():
    assert normalize("tennis-court", "dota_obb") in ("civilian", "other")


def test_dota_basketball_court_maps_to_other():
    assert normalize("basketball-court", "dota_obb") in ("civilian", "other")


def test_dota_ground_track_field_maps_to_other():
    assert normalize("ground-track-field", "dota_obb") in ("civilian", "other")


def test_dota_soccer_ball_field_maps_to_other():
    assert normalize("soccer-ball-field", "dota_obb") in ("civilian", "other")


def test_dota_swimming_pool_maps_to_other():
    assert normalize("swimming-pool", "dota_obb") in ("civilian", "other")


def test_dota_unknown_class_maps_to_other():
    assert normalize("unknown-dota-class-xyz", "dota_obb") == "other"


# ---------------------------------------------------------------------------
# Open-vocab (SAM3 / GROUNDING_DINO) tests
# ---------------------------------------------------------------------------

def test_open_vocab_tank_maps_to_armored():
    result = normalize("tank", "sam3")
    assert "armored" in result or result == "armored_vehicle", (
        f"Expected result containing 'armored', got '{result}'"
    )


def test_open_vocab_main_battle_tank_maps_to_armored():
    result = normalize("main battle tank", "sam3")
    assert "armored" in result, f"Expected armored branch, got '{result}'"


def test_open_vocab_helicopter_maps_to_aircraft():
    assert normalize("helicopter", "sam3") == "aircraft"


def test_open_vocab_cargo_plane_maps_to_aircraft():
    assert normalize("cargo plane", "sam3") == "aircraft"


def test_open_vocab_warship_maps_to_naval():
    assert normalize("warship", "sam3") == "naval"


def test_open_vocab_rocket_launcher_maps_to_artillery():
    result = normalize("rocket launcher", "sam3")
    assert result in ("artillery", "missile_strategic"), (
        f"Expected artillery or missile_strategic, got '{result}'"
    )


# ---------------------------------------------------------------------------
# Fallback / unknown label tests
# ---------------------------------------------------------------------------

def test_unknown_label_falls_back_to_other():
    assert normalize("completely_unknown_xyzzy", "sam3") == "other"


def test_empty_label_falls_back_to_other():
    assert normalize("", "sam3") == "other"


def test_none_layer_does_not_raise():
    result = normalize("tank", None)
    assert isinstance(result, str) and len(result) > 0


# ---------------------------------------------------------------------------
# Case-insensitivity test
# ---------------------------------------------------------------------------

def test_case_insensitive():
    lower = normalize("tank", "sam3")
    upper = normalize("TANK", "sam3")
    mixed = normalize("Tank", "sam3")
    assert lower == upper == mixed, (
        f"Case mismatch: lower={lower!r} upper={upper!r} mixed={mixed!r}"
    )


def test_case_insensitive_dota():
    # DOTA labels arrive in various casings from detectors
    assert normalize("PLANE", "dota_obb") == normalize("plane", "dota_obb")
    assert normalize("Large-Vehicle", "dota_obb") == normalize("large-vehicle", "dota_obb")


# ---------------------------------------------------------------------------
# DEFENCE_YOLO label tests
# ---------------------------------------------------------------------------

def test_defence_yolo_ifv_maps_to_armored():
    result = normalize("IFV", "defence_yolo")
    assert "armored" in result, f"Expected armored, got '{result}'"


def test_defence_yolo_apc_maps_to_armored():
    result = normalize("APC", "defence_yolo")
    assert "armored" in result, f"Expected armored, got '{result}'"


def test_defence_yolo_artillery_maps_to_artillery():
    assert normalize("Artillery", "defence_yolo") == "artillery"


def test_defence_yolo_truck_maps_to_logistics():
    result = normalize("Truck", "defence_yolo")
    assert result in ("logistics", "tactical_vehicle", "military_forces"), (
        f"Unexpected branch for 'Truck': {result!r}"
    )

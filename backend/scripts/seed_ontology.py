"""Seed PostGIS ontology tables from defenceOntology.json.

Step 1 of the ontology refactor plan. Idempotent — safe to re-run.

Usage:
    python -m backend.scripts.seed_ontology            # insert if absent
    python -m backend.scripts.seed_ontology --check    # dry-run, compare counts
    python -m backend.scripts.seed_ontology --reseed   # UPDATE on conflict
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

# Make `database` importable in both invocation contexts:
#   - host:      `python -m backend.scripts.seed_ontology`  (repo root on path → backend.database)
#   - container: `from scripts.seed_ontology import seed`   (/app on path → database)
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]
for _p in (str(REPO_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from backend.database import postgis_db  # noqa: E402  (host invocation)
except ImportError:
    from database import postgis_db  # noqa: E402  (container invocation)

logger = logging.getLogger("seed_ontology")

# Seed JSON lives next to this script (moved out of frontend/ in v0.9 — the
# frontend now reads the live ontology from /api/ontology, so the static JSON
# is purely a backend-owned bootstrap artifact).
ONTOLOGY_JSON = Path(__file__).resolve().parent / "seeds" / "defenceOntology.seed.json"
ICON_LOG_PATH = Path("/tmp/icon_assignment.csv")


# ---------------------------------------------------------------------------
# Python port of frontend/src/utils/branchIcons.tsx :: objectIconComponent().
# Order is preserved verbatim; first match wins. Each entry is a (regex,
# icon_key) tuple. The icon_key strings are the snake_case stable identifiers
# that the icon library (Step 9) will key off of.
# ---------------------------------------------------------------------------
ICON_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"helipad|helicopter"), "helicopter"),
    (re.compile(r"hangar|aircraft_shelter"), "warehouse"),
    (re.compile(r"airport|airfield|\brunway\b|terminal|control_tower|apron|tarmac|hardstand|dispersal|taxiway"), "landmark"),
    (re.compile(r"aircraft|airplane|\bplane\b|bomber|tanker_aircraft|cargo_aircraft|transport_aircraft|awacs|reconnaissance_aircraft|maritime_patrol|trainer_aircraft|civilian_airliner|\bdrone\b|\buav\b|loitering|fighter"), "plane"),
    (re.compile(r"warship|warcraft|destroyer|frigate|cruiser|aircraft_carrier|\bsubmarine\b|\bnaval\b|missile_boat|fast_attack|mine_warfare|amphibious_assault|landing_ship|patrol_boat"), "shield"),
    (re.compile(r"sailboat|yacht"), "sailboat"),
    (re.compile(r"tugboat|tug_boat|barge|ferry|motorboat|fishing|landing_craft|hovercraft|unmanned_surface|usv"), "anchor"),
    (re.compile(r"\bship\b|vessel|tanker|harbor|harbour|\bport\b|shipyard|cruise_ship|bulk_carrier|container_ship|roll_on_roll_off|cargo_ship|oil_tanker|lng_tanker|hospital_ship|survey_vessel|intelligence_ship|replenishment_ship"), "ship_wheel"),
    (re.compile(r"\bbus\b|bus_terminal"), "bus_front"),
    (re.compile(r"\btruck\b|trailer|\btractor\b|dump|hauler|mixer|cargo_truck|fuel_truck|water_truck|deicing_truck|refueling_truck|tow_tractor|crane_truck|tanker_truck|maintenance_vehicle|command_vehicle|communications_vehicle|ambulance"), "truck"),
    (re.compile(r"\bcar\b|\bvan\b|passenger_vehicle|small_vehicle|vehicle_lot|motorcycle|light_utility|recovery_vehicle|reconnaissance_vehicle|engineering_vehicle|bridging_vehicle|apc|ifv|\btank\b|main_battle_tank|light_tank|mrap|self_propelled_artillery|self_propelled_howitzer|towed_artillery|towed_howitzer|mortar|coastal_artillery|anti_aircraft_gun|aaa|self_propelled_aaa|towed_aaa|twin_aa"), "car"),
    (re.compile(r"locomotive|railway|\brail\b|train|tank_car|flat_car|cargo_car|passenger_car|boxcar|rolling_stock|railhead|rail_yard|rail_switch_yard|rail_junction|rail_tunnel"), "train_front"),
    (re.compile(r"crane|excavator|loader|grader|scraper|stacker|construction|concrete_plant|asphalt_plant|cement_plant"), "construction"),
    (re.compile(r"container|shipping"), "container"),
    (re.compile(r"storage_?tank|\boil\b|\bgas\b|fuel|cryogenic|gas_holder|lng_tank|liquid_oxygen|fueling_infrastructure|coal_pile|grain_silo|bulk_storage_silo"), "fuel"),
    (re.compile(r"factory|powerplant|power_plant|substation|solar|\bwind\b|chimney|smokestack|cooling_tower|refinery|cement|steel_mill|smelter|foundry|chemical_plant|hydroelectric|nuclear|transmission_tower|high_voltage_pylon|pumping_station|water_treatment|wastewater|desalination|satellite_dish_farm|flare_stack|methane_flare"), "factory"),
    (re.compile(r"bridge|overpass|interchange|roundabout|\btoll\b|tunnel|\broad\b|highway|expressway|cloverleaf|paved_road|unpaved_road|dirt_track|\btrail\b|logging_road|drone_airstrip|improvised_airstrip|drop_zone|pickup_zone|river_crossing|border_crossing|customs_station|trafficable_corridor|beach_landing|ferry_crossing|ferry_terminal|canal_lock|marina"), "navigation"),
    (re.compile(r"stadium|baseball|tennis|basketball|soccer|football|golf|race_track|swim|recreation|\bpark\b|parade_ground"), "dumbbell"),
    (re.compile(r"crop|farm|aquaculture"), "wheat"),
    (re.compile(r"lake|pond|\bdam\b|flooded|water"), "waves"),
    (re.compile(r"parking|\blot\b|dealership|gas_station|vehicle_park|truck_park|aircraft_parking_position"), "circle_parking"),
    (re.compile(r"missile|launcher|\btel\b|telar|rocket|\bsam\b|silo|launch_pad|warhead|service_tower|gantry|reentry_vehicle|missile_canister|cruise_missile|ballistic_missile|icbm|irbm|test_range_pad|static_test_stand|missile_assembly|missile_erector|missile_transport_erector|cold_launch"), "rocket"),
    (re.compile(r"radar|\bantenna\b|jammer|signal_intercept|elint|esm|microwave_tower|satellite_ground_station|communications_mast|communications_node"), "crosshair"),
    (re.compile(r"decoy|camouflage|concealed|dummy|deception|underbrush_hidden|paint_mismatch|false_runway_markings|smoke_screen|aerosol_obscurant|reflective_decoy|anti_reflective|mock_building|disturbed_earth_pattern|artificial_material"), "eye_off"),
    (re.compile(r"crater|burn|\bfire\b|fire_plume|fuel_fire|destroyed|damaged|demolished|collapsed|wreckage|charred|capsized|sunk_ship|beached_wreckage|secondary_detonation|cluster_munition|repair_activity|bridge_repair|runway_repair|field_damage_repair"), "flame"),
    (re.compile(r"bunker|trench|revetment|\bwall\b|berm|sangar|hesco|fortification|sandbag|barricade|roadblock|jersey|dragons_teeth|wire_obstacle|concertina|minefield|spike_strip|tank_trap|earthen_wall|breach_lane|mine_cleared_lane|fighting_position|foxhole|crew_served|observation_post_bunker|hedgerow|wire_coil|anti_tank_ditch|anti_vehicle_ditch|compound_wall|city_wall|stone_wall|concrete_wall|sally_port|vehicle_gate|gate|perimeter_fence|security_gate|watchtower|sentry_post|guard_post"), "brick_wall"),
    (re.compile(r"depot|warehouse|\bstack\b|pallet|supply_depot|ammunition|fuel_depot|pol_depot|cold_storage|maintenance_area|forward_supply|replenishment_point|stockpile"), "warehouse"),
    (re.compile(r"courtyard|plaza|square|marketplace|residential|office|mall|hospital|school|mosque|church|temple|government|embassy|prison|detention|police_station|fire_station|train_station_terminal|apartment|tower\b|pylon|\bshed\b|\bhut\b|\bbarn\b|industrial_building|multi_story_building|single_story_building|office_tower|government_building|building"), "building_2"),
    (re.compile(r"vehicle_track|heavy_track|tire_tracks|foot_path|wheel_ruts|track_vehicle_marking|activity_trail|recently_cleared|newly_painted|stockpile_growth|disturbed_vegetation|fresh_earth|soil_disturbance|burn_patch|new_excavation|new_trench|new_temporary|new_building|new_pad|new_road|removed_structure|object_moved|object_removed|object_added|convoy_moving|empty_storage|loaded_storage|increased_depot_activity|smoke_plume|steam_plume|active_burning"), "activity"),
    (re.compile(r"checkpoint|barricade|sniper_position|rooftop_position|crew_served_weapon_position|garrison|combat_outpost|forward_operating_base|military_base|military_headquarters|barracks|officer_quarters|mess_hall|vehicle_shed|motor_pool|training_area|firing_range|tank_range|drone_range|command_bunker|hardened_command_post|tent_city|bivouac|temporary_camp|refugee_camp|detention_facility|vehicle_inspection_lane|vehicle_wash_rack|garrison_helipad"), "landmark"),
]

# Map branch iconKey (camelCase from JSON) to a fallback snake_case icon_key.
BRANCH_ICON_KEY_FALLBACK: dict[str, str] = {
    "military": "shield",
    "airDefense": "crosshair",
    "missile": "rocket",
    "installation": "landmark",
    "logistics": "package",
    "airfield": "plane",
    "naval": "ship",
    "fortification": "brick_wall",
    "camouflage": "eye_off",
    "activity": "activity",
    "industrial": "factory",
    "transport": "truck",
    "urban": "building_2",
    "damage": "flame",
    "other": "circle_help",
}


def normalize_label(value: str) -> str:
    """Lowercase + non-alnum → underscore, mirroring branchIcons.tsx."""
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower())


def assign_icon_key(
    prompt: str | None, label: str | None, fallback: str | None
) -> tuple[str, str, str]:
    """Return (icon_key, source, regex_pattern_or_'fallback').

    `source` is one of 'prompt', 'label', or 'fallback', indicating which
    field produced the icon assignment. Tries the prompt first (most
    specific), then the label, then falls back to the branch-level icon.
    This mirrors the ObjectIcon component which receives `prompt` first.
    """
    for source, raw in (("prompt", prompt), ("label", label)):
        if not raw:
            continue
        normalized = normalize_label(raw)
        for pattern, icon_key in ICON_RULES:
            if pattern.search(normalized):
                return icon_key, source, pattern.pattern
    fallback_icon = BRANCH_ICON_KEY_FALLBACK.get(fallback or "", "circle_help")
    return fallback_icon, "fallback", "fallback"


# ---------------------------------------------------------------------------
# JSON walking
# ---------------------------------------------------------------------------
def walk_branches(
    nodes: Iterable[dict[str, Any]],
    parent_id: str | None,
    order_offset: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (branch_rows, object_rows) — flat lists ready for insert."""
    branches: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    for idx, node in enumerate(nodes):
        branch_id = node["id"]
        branches.append({
            "id": branch_id,
            "parent_id": parent_id,
            "label": node.get("label", branch_id),
            "color": node.get("color"),
            "short": node.get("short"),
            "icon_key": node.get("iconKey"),
            "matchers": node.get("matchers", []),
            "sensors": node.get("sensors", ["optical"]),
            "order_index": order_offset + idx,
        })
        for obj_idx, obj in enumerate(node.get("objects", []) or []):
            objects.append({
                "id": obj["id"],
                "branch_id": branch_id,
                "label": obj.get("label", obj["id"]),
                "prompt": obj.get("prompt", ""),
                "sensors": obj.get("sensors", ["optical"]),
                "min_gsd_meters": obj.get("minGsdMeters"),
                "branch_icon_key": node.get("iconKey"),  # for icon fallback
                "order_index": obj_idx,
            })
        child_branches, child_objects = walk_branches(
            node.get("children", []) or [], branch_id, order_offset=0
        )
        branches.extend(child_branches)
        objects.extend(child_objects)
    return branches, objects


def load_ontology() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with open(ONTOLOGY_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    return walk_branches(data.get("branches", []), parent_id=None)


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------
INSERT_BRANCH_SQL = """
INSERT INTO ontology_branches
    (id, parent_id, label, color, short, icon_key, matchers, sensors, order_index, updated_at)
VALUES
    (%(id)s, %(parent_id)s, %(label)s, %(color)s, %(short)s, %(icon_key)s,
     %(matchers)s::jsonb, %(sensors)s::jsonb, %(order_index)s, now())
ON CONFLICT (id) DO NOTHING
"""

UPSERT_BRANCH_SQL = """
INSERT INTO ontology_branches
    (id, parent_id, label, color, short, icon_key, matchers, sensors, order_index, updated_at)
VALUES
    (%(id)s, %(parent_id)s, %(label)s, %(color)s, %(short)s, %(icon_key)s,
     %(matchers)s::jsonb, %(sensors)s::jsonb, %(order_index)s, now())
ON CONFLICT (id) DO UPDATE SET
    parent_id   = EXCLUDED.parent_id,
    label       = EXCLUDED.label,
    color       = EXCLUDED.color,
    short       = EXCLUDED.short,
    icon_key    = EXCLUDED.icon_key,
    matchers    = EXCLUDED.matchers,
    sensors     = EXCLUDED.sensors,
    order_index = EXCLUDED.order_index,
    updated_at  = now()
"""

INSERT_OBJECT_SQL = """
INSERT INTO ontology_objects
    (id, branch_id, label, prompt, sensors, min_gsd_meters, icon_key, order_index, updated_at)
VALUES
    (%(id)s, %(branch_id)s, %(label)s, %(prompt)s, %(sensors)s::jsonb,
     %(min_gsd_meters)s, %(icon_key)s, %(order_index)s, now())
ON CONFLICT (id) DO NOTHING
"""

UPSERT_OBJECT_SQL = """
INSERT INTO ontology_objects
    (id, branch_id, label, prompt, sensors, min_gsd_meters, icon_key, order_index, updated_at)
VALUES
    (%(id)s, %(branch_id)s, %(label)s, %(prompt)s, %(sensors)s::jsonb,
     %(min_gsd_meters)s, %(icon_key)s, %(order_index)s, now())
ON CONFLICT (id) DO UPDATE SET
    branch_id      = EXCLUDED.branch_id,
    label          = EXCLUDED.label,
    prompt         = EXCLUDED.prompt,
    sensors        = EXCLUDED.sensors,
    min_gsd_meters = EXCLUDED.min_gsd_meters,
    icon_key       = EXCLUDED.icon_key,
    order_index    = EXCLUDED.order_index,
    updated_at     = now()
"""


def _row_to_branch_params(row: dict[str, Any]) -> dict[str, Any]:
    raw_icon_key = row["icon_key"]
    # Map camelCase iconKey from JSON (e.g. 'airDefense') to the snake_case
    # stable identifier (e.g. 'crosshair') used by the icon library.
    snake_icon_key = BRANCH_ICON_KEY_FALLBACK.get(raw_icon_key or "", "circle_help")
    return {
        "id": row["id"],
        "parent_id": row["parent_id"],
        "label": row["label"],
        "color": row["color"],
        "short": row["short"],
        "icon_key": snake_icon_key,
        "matchers": json.dumps(row["matchers"] or []),
        "sensors": json.dumps(row["sensors"] or ["optical"]),
        "order_index": row["order_index"],
    }


def _row_to_object_params(row: dict[str, Any], icon_key: str) -> dict[str, Any]:
    return {
        "id": row["id"],
        "branch_id": row["branch_id"],
        "label": row["label"],
        "prompt": row["prompt"],
        "sensors": json.dumps(row["sensors"] or ["optical"]),
        "min_gsd_meters": row["min_gsd_meters"],
        "icon_key": icon_key,
        "order_index": row["order_index"],
    }


def seed(reseed: bool = False) -> tuple[int, int, int, int]:
    """Seed branches and objects.

    Returns (n_branches_total, n_objects_total, branch_writes, object_writes)
    where the *_writes counters reflect rows actually inserted or updated by
    this run (used to decide whether to bump the ontology_version).
    """
    branches, objects = load_ontology()
    branch_sql = UPSERT_BRANCH_SQL if reseed else INSERT_BRANCH_SQL
    object_sql = UPSERT_OBJECT_SQL if reseed else INSERT_OBJECT_SQL

    icon_log_rows: list[tuple[str, str, str, str]] = []
    branch_writes = 0
    object_writes = 0
    new_version: int | None = None

    with postgis_db.get_cursor(commit=True) as cur:
        # Pass 1: insert branches in two waves so FK to parent is satisfied
        # without requiring a topological sort. First insert root branches
        # (parent_id is None), then children.
        roots = [b for b in branches if b["parent_id"] is None]
        children = [b for b in branches if b["parent_id"] is not None]
        for row in roots:
            cur.execute(branch_sql, _row_to_branch_params(row))
            if cur.rowcount and cur.rowcount > 0:
                branch_writes += cur.rowcount
        for row in children:
            cur.execute(branch_sql, _row_to_branch_params(row))
            if cur.rowcount and cur.rowcount > 0:
                branch_writes += cur.rowcount

        # Pass 2: insert objects with auto-assigned icon_key
        for row in objects:
            icon_key, source, regex_hit = assign_icon_key(
                row["prompt"], row["label"], row.get("branch_icon_key")
            )
            cur.execute(object_sql, _row_to_object_params(row, icon_key))
            if cur.rowcount and cur.rowcount > 0:
                object_writes += cur.rowcount
            icon_log_rows.append((row["id"], source, regex_hit, icon_key))

        # Pass 2b: prune objects removed from the JSON (reseed only). The seed
        # JSON is the source of truth for a wholesale taxonomy revision;
        # without this, collapsed/renamed objects linger as orphan rows and
        # keep surfacing stale prompts through default_prompts().
        if reseed:
            json_ids = [row["id"] for row in objects]
            cur.execute(
                "DELETE FROM ontology_objects WHERE id <> ALL(%s)", (json_ids,)
            )
            if cur.rowcount and cur.rowcount > 0:
                object_writes += cur.rowcount
                logger.info("pruned %d object(s) absent from seed JSON", cur.rowcount)

        # Pass 3: only bump version if at least one branch or object row was
        # actually written. Avoids cache-invalidation thrash on no-op runs.
        if branch_writes > 0 or object_writes > 0:
            cur.execute(
                "UPDATE ontology_version SET version_id = version_id + 1, updated_at = now() "
                "WHERE singleton = TRUE RETURNING version_id"
            )
            row = cur.fetchone()
            new_version = int(row["version_id"]) if row else None
        else:
            cur.execute("SELECT version_id FROM ontology_version WHERE singleton = TRUE")
            row = cur.fetchone()
            new_version = int(row["version_id"]) if row else None

    # Write icon assignment log
    try:
        with open(ICON_LOG_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["object_id", "source", "regex", "icon_key"])
            for row in icon_log_rows:
                writer.writerow(row)
        logger.info("Wrote icon assignment log: %s (%d rows)", ICON_LOG_PATH, len(icon_log_rows))
    except OSError as e:
        logger.warning("Could not write icon assignment log to %s: %s", ICON_LOG_PATH, e)

    if branch_writes == 0 and object_writes == 0:
        logger.info(
            "no-op: branches=%d objects=%d version=%s (unchanged)",
            len(branches), len(objects), new_version,
        )
    else:
        logger.info(
            "wrote: branch_rows=%d object_rows=%d version=%s (bumped)",
            branch_writes, object_writes, new_version,
        )

    return len(branches), len(objects), branch_writes, object_writes


def check() -> int:
    branches, objects = load_ontology()
    expected_branches = len(branches)
    expected_objects = len(objects)

    with postgis_db.get_cursor(commit=False) as cur:
        # Note: 'Other' branch is seeded by SQL outside the JSON walk, so it
        # is added to the expected count.
        cur.execute("SELECT count(*) AS n FROM ontology_branches")
        actual_branches = int(cur.fetchone()["n"])
        cur.execute("SELECT count(*) AS n FROM ontology_objects")
        actual_objects = int(cur.fetchone()["n"])
        cur.execute("SELECT version_id FROM ontology_version WHERE singleton = TRUE")
        version_row = cur.fetchone()
        version_id = version_row["version_id"] if version_row else None

    # Allow +1 for the always-present 'Other' branch (which is not in the
    # JSON tree).
    expected_branches_with_other = expected_branches + 1

    print(f"branches: expected={expected_branches_with_other} (incl 'Other') actual={actual_branches}")
    print(f"objects:  expected={expected_objects} actual={actual_objects}")
    print(f"ontology_version: {version_id}")

    ok = (actual_branches == expected_branches_with_other) and (actual_objects == expected_objects)
    return 0 if ok else 1


def main() -> int:
    # Configure logging here (rather than at import time) so callers that
    # have already configured logging are not overridden.
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    parser = argparse.ArgumentParser(description="Seed PostGIS ontology tables.")
    parser.add_argument("--check", action="store_true", help="Dry-run: compare DB counts to JSON.")
    parser.add_argument("--reseed", action="store_true", help="Re-insert (UPDATE on conflict).")
    args = parser.parse_args()

    if args.check:
        return check()

    n_branches, n_objects, branch_writes, object_writes = seed(reseed=args.reseed)
    logger.info(
        "Seeded ontology: %d branches (from JSON) + 'Other', %d objects. mode=%s writes=%d/%d",
        n_branches, n_objects,
        "reseed" if args.reseed else "insert-if-absent",
        branch_writes, object_writes,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

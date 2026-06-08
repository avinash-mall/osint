/**
 * @deprecated The regex-based `objectIconComponent` is now the LAST-RESORT
 * fallback for icon resolution. New code should use the curated
 * `IconRenderer` from `./iconLibrary` and pass an explicit `iconKey` (the
 * snake_case stable id emitted by `backend/scripts/seed_ontology.py`).
 *
 * The exports in this file (`ObjectIcon`, `objectIconComponent`,
 * `BRANCH_ICON_BY_KEY`) remain for backwards compatibility with integrators
 * that have not yet migrated.
 */
import {
  Activity,
  Anchor,
  BrickWall,
  Building2,
  BusFront,
  Car,
  CircleHelp,
  CircleParking,
  Construction,
  Container,
  Crosshair,
  Dumbbell,
  EyeOff,
  Factory,
  Flame,
  Fuel,
  Helicopter,
  Landmark,
  Navigation,
  Package,
  Plane,
  Rocket,
  Sailboat,
  Shield,
  Ship,
  ShipWheel,
  TrainFront,
  Truck,
  Warehouse,
  Waves,
  Wheat,
  type LucideIcon,
} from 'lucide-react';
import { type BranchIconKey } from './defenceOntology';
import { iconComponentByKey } from './iconLibrary';

export const BRANCH_ICON_BY_KEY: Record<BranchIconKey, LucideIcon> = {
  military: Shield,
  airDefense: Crosshair,
  missile: Rocket,
  installation: Landmark,
  logistics: Package,
  airfield: Plane,
  naval: Ship,
  fortification: BrickWall,
  camouflage: EyeOff,
  activity: Activity,
  industrial: Factory,
  transport: Truck,
  urban: Building2,
  damage: Flame,
  other: CircleHelp,
};

/**
 * Pick a specific lucide icon for an object based on its prompt or raw class
 * label. Falls back to the branch-level icon when nothing more specific
 * matches. Used by both the upload picker (per object card) and the GEOINT
 * map (per detection subclass).
 */
export function objectIconComponent(
  value?: string | null,
  fallbackIconKey?: BranchIconKey | null
): LucideIcon {
  const raw = String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '_');
  if (/helipad|helicopter/.test(raw)) return Helicopter;
  if (/hangar|aircraft_shelter/.test(raw)) return Warehouse;
  if (/airport|airfield|\brunway\b|terminal|control_tower|apron|tarmac|hardstand|dispersal|taxiway/.test(raw)) return Landmark;
  if (/aircraft|airplane|\bplane\b|bomber|tanker_aircraft|cargo_aircraft|transport_aircraft|awacs|reconnaissance_aircraft|maritime_patrol|trainer_aircraft|civilian_airliner|\bdrone\b|\buav\b|loitering|fighter/.test(raw)) return Plane;
  if (/warship|warcraft|destroyer|frigate|cruiser|aircraft_carrier|\bsubmarine\b|\bnaval\b|missile_boat|fast_attack|mine_warfare|amphibious_assault|landing_ship|patrol_boat/.test(raw)) return Ship;
  if (/sailboat|yacht/.test(raw)) return Sailboat;
  if (/tugboat|tug_boat|barge|ferry|motorboat|fishing|landing_craft|hovercraft|unmanned_surface|usv/.test(raw)) return Anchor;
  if (/\bship\b|vessel|tanker|harbor|harbour|\bport\b|shipyard|cruise_ship|bulk_carrier|container_ship|roll_on_roll_off|cargo_ship|oil_tanker|lng_tanker|hospital_ship|survey_vessel|intelligence_ship|replenishment_ship/.test(raw)) return ShipWheel;
  if (/\bbus\b|bus_terminal/.test(raw)) return BusFront;
  if (/\btruck\b|trailer|\btractor\b|dump|hauler|mixer|cargo_truck|fuel_truck|water_truck|deicing_truck|refueling_truck|tow_tractor|crane_truck|tanker_truck|maintenance_vehicle|command_vehicle|communications_vehicle|ambulance/.test(raw)) return Truck;
  if (/\bcar\b|\bvan\b|passenger_vehicle|small_vehicle|vehicle_lot|motorcycle|light_utility|recovery_vehicle|reconnaissance_vehicle|engineering_vehicle|bridging_vehicle|apc|ifv|\btank\b|main_battle_tank|light_tank|mrap|self_propelled_artillery|self_propelled_howitzer|towed_artillery|towed_howitzer|mortar|coastal_artillery|anti_aircraft_gun|aaa|self_propelled_aaa|towed_aaa|twin_aa/.test(raw)) return Car;
  if (/locomotive|railway|\brail\b|train|tank_car|flat_car|cargo_car|passenger_car|boxcar|rolling_stock|railhead|rail_yard|rail_switch_yard|rail_junction|rail_tunnel/.test(raw)) return TrainFront;
  if (/crane|excavator|loader|grader|scraper|stacker|construction|concrete_plant|asphalt_plant|cement_plant/.test(raw)) return Construction;
  if (/container|shipping/.test(raw)) return Container;
  if (/storage_?tank|\boil\b|\bgas\b|fuel|cryogenic|gas_holder|lng_tank|liquid_oxygen|fueling_infrastructure|coal_pile|grain_silo|bulk_storage_silo/.test(raw)) return Fuel;
  if (/factory|powerplant|power_plant|substation|solar|\bwind\b|chimney|smokestack|cooling_tower|refinery|cement|steel_mill|smelter|foundry|chemical_plant|hydroelectric|nuclear|transmission_tower|high_voltage_pylon|pumping_station|water_treatment|wastewater|desalination|satellite_dish_farm|flare_stack|methane_flare/.test(raw)) return Factory;
  if (/bridge|overpass|interchange|roundabout|\btoll\b|tunnel|\broad\b|highway|expressway|cloverleaf|paved_road|unpaved_road|dirt_track|\btrail\b|logging_road|drone_airstrip|improvised_airstrip|drop_zone|pickup_zone|river_crossing|border_crossing|customs_station|trafficable_corridor|beach_landing|ferry_crossing|ferry_terminal|canal_lock|marina/.test(raw)) return Navigation;
  if (/stadium|baseball|tennis|basketball|soccer|football|golf|race_track|swim|recreation|\bpark\b|parade_ground/.test(raw)) return Dumbbell;
  if (/crop|farm|aquaculture/.test(raw)) return Wheat;
  if (/lake|pond|\bdam\b|flooded|water/.test(raw)) return Waves;
  if (/parking|\blot\b|dealership|gas_station|vehicle_park|truck_park|aircraft_parking_position/.test(raw)) return CircleParking;
  if (/missile|launcher|\btel\b|telar|rocket|\bsam\b|silo|launch_pad|warhead|service_tower|gantry|reentry_vehicle|missile_canister|cruise_missile|ballistic_missile|icbm|irbm|test_range_pad|static_test_stand|missile_assembly|missile_erector|missile_transport_erector|cold_launch/.test(raw)) return Rocket;
  if (/radar|\bantenna\b|jammer|signal_intercept|elint|esm|microwave_tower|satellite_ground_station|communications_mast|communications_node/.test(raw)) return Crosshair;
  if (/decoy|camouflage|concealed|dummy|deception|underbrush_hidden|paint_mismatch|false_runway_markings|smoke_screen|aerosol_obscurant|reflective_decoy|anti_reflective|mock_building|disturbed_earth_pattern|artificial_material/.test(raw)) return EyeOff;
  if (/crater|burn|\bfire\b|fire_plume|fuel_fire|destroyed|damaged|demolished|collapsed|wreckage|charred|capsized|sunk_ship|beached_wreckage|secondary_detonation|cluster_munition|repair_activity|bridge_repair|runway_repair|field_damage_repair/.test(raw)) return Flame;
  if (/bunker|trench|revetment|\bwall\b|berm|sangar|hesco|fortification|sandbag|barricade|roadblock|jersey|dragons_teeth|wire_obstacle|concertina|minefield|spike_strip|tank_trap|earthen_wall|breach_lane|mine_cleared_lane|fighting_position|foxhole|crew_served|observation_post_bunker|hedgerow|wire_coil|anti_tank_ditch|anti_vehicle_ditch|compound_wall|city_wall|stone_wall|concrete_wall|sally_port|vehicle_gate|gate|perimeter_fence|security_gate|watchtower|sentry_post|guard_post/.test(raw)) return BrickWall;
  if (/depot|warehouse|\bstack\b|pallet|supply_depot|ammunition|fuel_depot|pol_depot|cold_storage|maintenance_area|forward_supply|replenishment_point|stockpile/.test(raw)) return Warehouse;
  if (/courtyard|plaza|square|marketplace|residential|office|mall|hospital|school|mosque|church|temple|government|embassy|prison|detention|police_station|fire_station|train_station_terminal|apartment|tower\b|pylon|\bshed\b|\bhut\b|\bbarn\b|industrial_building|multi_story_building|single_story_building|office_tower|government_building|building/.test(raw)) return Building2;
  if (/vehicle_track|heavy_track|tire_tracks|foot_path|wheel_ruts|track_vehicle_marking|activity_trail|recently_cleared|newly_painted|stockpile_growth|disturbed_vegetation|fresh_earth|soil_disturbance|burn_patch|new_excavation|new_trench|new_temporary|new_building|new_pad|new_road|removed_structure|object_moved|object_removed|object_added|convoy_moving|empty_storage|loaded_storage|increased_depot_activity|smoke_plume|steam_plume|active_burning/.test(raw)) return Activity;
  if (/checkpoint|barricade|sniper_position|rooftop_position|crew_served_weapon_position|garrison|combat_outpost|forward_operating_base|military_base|military_headquarters|barracks|officer_quarters|mess_hall|vehicle_shed|motor_pool|training_area|firing_range|tank_range|drone_range|command_bunker|hardened_command_post|tent_city|bivouac|temporary_camp|refugee_camp|detention_facility|vehicle_inspection_lane|vehicle_wash_rack|garrison_helipad/.test(raw)) return Landmark;
  // Branch-level fallback. Try the design-canvas BranchIconKey table first
  // (camelCase keys), then the curated snake_case ICON_BY_KEY table from
  // iconLibrary, which is what backend seed_ontology.py writes. If neither
  // resolves, render the CircleHelp glyph rather than `undefined` — passing
  // `undefined` as a JSX element type crashes the render with
  // "Element type is invalid".
  if (fallbackIconKey) {
    const fromBranchMap = BRANCH_ICON_BY_KEY[fallbackIconKey];
    if (fromBranchMap) return fromBranchMap;
    const fromLibrary = iconComponentByKey(fallbackIconKey);
    if (fromLibrary) return fromLibrary;
  }
  return CircleHelp;
}

export function ObjectIcon({
  prompt,
  branchIconKey,
  className = 'h-3.5 w-3.5',
}: {
  prompt?: string | null;
  branchIconKey?: BranchIconKey | null;
  className?: string;
}) {
  const Icon = objectIconComponent(prompt, branchIconKey ?? undefined);
  return <Icon className={className} />;
}

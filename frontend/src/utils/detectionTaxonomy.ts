export const CLASS_LIST = [
  'xview_fixed_wing_aircraft', 'xview_small_aircraft', 'xview_cargo_plane', 'xview_helicopter',
  'xview_passenger_vehicle', 'xview_small_car', 'xview_bus', 'xview_pickup_truck', 'xview_utility_truck',
  'xview_truck', 'xview_cargo_truck', 'xview_truck_with_box', 'xview_truck_tractor', 'xview_trailer',
  'xview_truck_with_flatbed', 'xview_truck_with_liquid', 'xview_crane_truck', 'xview_railway_vehicle',
  'xview_passenger_car', 'xview_cargo_car', 'xview_flat_car', 'xview_tank_car', 'xview_locomotive',
  'xview_maritime_vessel', 'xview_motorboat', 'xview_sailboat', 'xview_tugboat', 'xview_barge',
  'xview_fishing_vessel', 'xview_ferry', 'xview_yacht', 'xview_container_ship', 'xview_oil_tanker',
  'xview_engineering_vehicle', 'xview_tower_crane', 'xview_container_crane', 'xview_reach_stacker',
  'xview_straddle_carrier', 'xview_mobile_crane', 'xview_dump_truck', 'xview_haul_truck',
  'xview_scraper_tractor', 'xview_front_loader_bulldozer', 'xview_excavator', 'xview_cement_mixer',
  'xview_ground_grader', 'xview_hut_tent', 'xview_shed', 'xview_building',
  'xview_aircraft_hangar', 'xview_damaged_demolished_building', 'xview_facility', 'xview_construction_site',
  'xview_vehicle_lot', 'xview_helipad', 'xview_storage_tank', 'xview_shipping_container_lot',
  'xview_shipping_container', 'xview_pylon', 'xview_tower',
  'dota_plane', 'dota_baseball_diamond', 'dota_bridge', 'dota_ground_track_field', 'dota_small_vehicle',
  'dota_large_vehicle', 'dota_ship', 'dota_tennis_court', 'dota_basketball_court', 'dota_storage_tank',
  'dota_soccer_ball_field', 'dota_roundabout', 'dota_harbor', 'dota_swimming_pool', 'dota_helicopter',
  'dota_container_crane', 'dota_airport', 'dota_helipad',
  'fmow_airport', 'fmow_airport_hangar', 'fmow_airport_terminal', 'fmow_amusement_park', 'fmow_aquaculture',
  'fmow_archaeological_site', 'fmow_barn', 'fmow_border_checkpoint', 'fmow_burial_site', 'fmow_car_dealership',
  'fmow_construction_site', 'fmow_crop_field', 'fmow_dam', 'fmow_debris_or_rubble',
  'fmow_educational_institution', 'fmow_electric_substation', 'fmow_factory_or_powerplant', 'fmow_fire_station',
  'fmow_flooded_road', 'fmow_fountain', 'fmow_gas_station', 'fmow_golf_course',
  'fmow_ground_transportation_station', 'fmow_helipad', 'fmow_hospital', 'fmow_impoverished_settlement',
  'fmow_interchange', 'fmow_lake_or_pond', 'fmow_lighthouse', 'fmow_military_facility',
  'fmow_multi_unit_residential', 'fmow_nuclear_powerplant', 'fmow_office_building',
  'fmow_oil_or_gas_facility', 'fmow_park', 'fmow_parking_lot_or_garage', 'fmow_place_of_worship',
  'fmow_police_station', 'fmow_port', 'fmow_prison', 'fmow_race_track', 'fmow_railway_bridge',
  'fmow_recreational_facility', 'fmow_road_bridge', 'fmow_runway', 'fmow_shipyard',
  'fmow_shopping_mall', 'fmow_single_unit_residential', 'fmow_smokestack', 'fmow_solar_farm',
  'fmow_space_facility', 'fmow_stadium', 'fmow_storage_tank', 'fmow_surface_mine',
  'fmow_swimming_pool', 'fmow_toll_booth', 'fmow_tower', 'fmow_tunnel_opening',
  'fmow_waste_disposal', 'fmow_water_treatment_facility', 'fmow_wind_farm', 'fmow_zoo',
  'fair1m_dry_cargo_ship', 'fair1m_baseball_field', 'fair1m_small_car', 'fair1m_van',
  'fair1m_intersection', 'fair1m_dump_truck', 'fair1m_cargo_truck', 'fair1m_other_vehicle',
  'fair1m_bus', 'fair1m_passenger_ship', 'fair1m_liquid_cargo_ship', 'fair1m_other_ship',
  'fair1m_tugboat', 'fair1m_engineering_ship', 'fair1m_trailer', 'fair1m_other_airplane',
  'fair1m_boeing737', 'fair1m_boeing747', 'fair1m_a330', 'fair1m_motorboat',
  'fair1m_fishing_boat', 'fair1m_excavator', 'fair1m_a321', 'fair1m_a220',
  'fair1m_truck_tractor', 'fair1m_tennis_court', 'fair1m_arj21', 'fair1m_basketball_court',
  'fair1m_boeing787', 'fair1m_boeing777', 'fair1m_a350', 'fair1m_tractor',
  'fair1m_football_field', 'fair1m_warship', 'fair1m_roundabout', 'fair1m_bridge', 'fair1m_c919',
  'dior_airplane', 'dior_airport', 'dior_baseballfield', 'dior_basketballcourt', 'dior_bridge',
  'dior_chimney', 'dior_dam', 'dior_expressway_service_area', 'dior_expressway_toll_station',
  'dior_golffield', 'dior_groundtrackfield', 'dior_harbor', 'dior_overpass', 'dior_ship',
  'dior_stadium', 'dior_storagetank', 'dior_tenniscourt', 'dior_trainstation', 'dior_vehicle',
  'dior_windmill',
  'sodaa_airplane', 'sodaa_helicopter', 'sodaa_small_vehicle', 'sodaa_large_vehicle',
  'sodaa_ship', 'sodaa_container', 'sodaa_storage_tank', 'sodaa_swimming_pool', 'sodaa_windmill',
  'hrsc_ship', 'hrsc_aircraft_carrier', 'hrsc_warcraft', 'hrsc_merchant_ship',
  'hrsc_nimitz_class_aircraft_carrier', 'hrsc_enterprise_class_aircraft_carrier',
  'hrsc_arleigh_burke_class_destroyer', 'hrsc_perry_class_frigate',
  'hrsc_ticonderoga_class_cruiser', 'hrsc_kitty_hawk_class_aircraft_carrier',
  'hrsc_kuznetsov_class_aircraft_carrier', 'hrsc_blue_ridge_class_command_ship',
  'hrsc_container_ship', 'hrsc_tugboat', 'hrsc_medical_ship', 'hrsc_car_carrier',
  'hrsc_hovercraft', 'hrsc_yacht', 'hrsc_cruise_ship', 'hrsc_submarine',
  'hrsc_liquid_cargo_ship',
] as const;

export type DetectionCategoryId =
  | 'air'
  | 'vehicle'
  | 'rail'
  | 'sea'
  | 'construction'
  | 'structure'
  | 'mil'
  | 'energy'
  | 'sport'
  | 'infra'
  | 'agri'
  | 'water'
  | 'logistics'
  | 'transit'
  | 'other';

export const CATEGORY_ORDER: DetectionCategoryId[] = [
  'air',
  'vehicle',
  'rail',
  'sea',
  'construction',
  'structure',
  'mil',
  'energy',
  'sport',
  'infra',
  'agri',
  'water',
  'logistics',
  'transit',
  'other',
];

export const DETECTION_CATEGORIES: Record<DetectionCategoryId, { label: string; color: string; short: string }> = {
  air: { label: 'Air', color: '#4ea1ff', short: 'AIR' },
  vehicle: { label: 'Vehicle', color: '#ff7a1a', short: 'VEH' },
  rail: { label: 'Rail', color: '#a78bfa', short: 'RAL' },
  sea: { label: 'Maritime', color: '#3dd68c', short: 'SEA' },
  construction: { label: 'Construct', color: '#f5b400', short: 'CON' },
  structure: { label: 'Structure', color: '#aab2bb', short: 'STR' },
  mil: { label: 'Military', color: '#ff3b30', short: 'MIL' },
  energy: { label: 'Energy', color: '#ff3b30', short: 'ENR' },
  sport: { label: 'Recreation', color: '#3dd68c', short: 'REC' },
  infra: { label: 'Infra', color: '#9bb1c4', short: 'INF' },
  agri: { label: 'Agri', color: '#a8d34a', short: 'AGR' },
  water: { label: 'Water', color: '#4ea1ff', short: 'WTR' },
  logistics: { label: 'Logistics', color: '#f5b400', short: 'LOG' },
  transit: { label: 'Transit', color: '#aab2bb', short: 'TRN' },
  other: { label: 'Other', color: '#727a83', short: 'OTH' },
};

export const SOURCE_ORDER = ['xView', 'DOTA', 'FAIR1M', 'DIOR-R', 'SODA-A', 'HRSC', 'fMoW', 'Local'] as const;

const CATEGORY_BY_ONTOLOGY: Record<string, DetectionCategoryId> = {
  air: 'air',
  maritime: 'sea',
  ground: 'vehicle',
  combat: 'mil',
  infrastructure: 'infra',
  logistics: 'logistics',
  energy: 'energy',
  facility: 'structure',
  unknown: 'other',
};

export function classifyDetectionClass(value?: string | null, ontologyCategory?: string | null): DetectionCategoryId {
  const raw = String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '_');
  const ontology = CATEGORY_BY_ONTOLOGY[String(ontologyCategory || '').toLowerCase()];
  // Maritime checked before air so 'aircraft_carrier', 'container_ship', 'naval_vessel' resolve to sea.
  if (/aircraft_carrier|ship|vessel|boat|tanker|barge|tugboat|ferry|yacht|harbor|harbour|port|shipyard|warship|warcraft|destroyer|frigate|cruiser|corvette|submarine|hovercraft|naval/.test(raw)) return 'sea';
  if (/aircraft|plane|airplane|hangar|airport|runway|helipad|helicopter|boeing|a3\d\d|a2\d\d|arj21|c919|a350/.test(raw)) return 'air';
  if (/locomotive|railway|rail|tank_car|flat_car|cargo_car|passenger_car|train_station|trainstation/.test(raw)) return 'rail';
  if (/truck|car|vehicle|bus|trailer|van|tractor/.test(raw)) return 'vehicle';
  if (/excavator|crane|loader|grader|dump|scraper|stacker|straddle|cement|construction|mining|surface_mine|engineering/.test(raw)) return 'construction';
  if (/storage_?tank|oil|gas|smokestack|powerplant|substation|factory|nuclear|solar|wind_?farm|windmill|chimney/.test(raw)) return 'energy';
  if (/military|prison|police|fire|border|checkpoint/.test(raw)) return 'mil';
  if (/bridge|overpass|interchange|roundabout|toll|tunnel|road|track|expressway|transportation_station/.test(raw)) return 'infra';
  if (/stadium|baseball|tennis|basketball|soccer|football|golf|race_track|ground_?track|swim|park|recreation|amusement|zoo|fountain/.test(raw)) return 'sport';
  if (/residential|office|shopping|mall|hospital|school|education|worship|burial|archaeological|lighthouse|tower|pylon|shed|hut|barn|building|facility/.test(raw)) return 'structure';
  if (/crop|aquaculture|farm/.test(raw)) return 'agri';
  if (/lake|pond|water_treatment|dam|flooded|debris|rubble|waste|impoverished/.test(raw)) return 'water';
  if (/container|shipping/.test(raw)) return 'logistics';
  if (/parking|lot|dealership|gas_station/.test(raw)) return 'transit';
  // HRSC2016 is exclusively maritime, so any unmapped hrsc_* class falls back to sea.
  if (raw.startsWith('hrsc')) return 'sea';
  return ontology || 'other';
}

export function detectionClassLabel(value?: string | null): string {
  const raw = String(value || 'unknown').trim();
  const withoutSource = raw.replace(/^(xview|dota|fmow|fair1m|rareplanes|dior|sodaa|hrsc)[_\s-]+/i, '');
  return withoutSource
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase()) || 'Unknown';
}

export function detectionClassSource(value?: string | null): typeof SOURCE_ORDER[number] {
  const raw = String(value || '').toLowerCase();
  if (raw.startsWith('xview_') || raw.startsWith('xview ')) return 'xView';
  if (raw.startsWith('dota_') || raw.startsWith('dota ')) return 'DOTA';
  if (raw.startsWith('fair1m_') || raw.startsWith('fair1m ')) return 'FAIR1M';
  if (raw.startsWith('dior_') || raw.startsWith('dior ')) return 'DIOR-R';
  if (raw.startsWith('sodaa_') || raw.startsWith('sodaa ')) return 'SODA-A';
  if (raw.startsWith('hrsc_') || raw.startsWith('hrsc ')) return 'HRSC';
  if (raw.startsWith('fmow_') || raw.startsWith('fmow ')) return 'fMoW';
  return 'Local';
}

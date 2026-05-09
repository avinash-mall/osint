export interface DefenceObject {
  id: string;
  label: string;
  prompt: string;
}

export interface DefenceBranch {
  id: string;
  label: string;
  children?: DefenceBranch[];
  objects?: DefenceObject[];
}

const object = (id: string, prompt?: string): DefenceObject => ({
  id,
  label: id.replace(/_/g, ' '),
  prompt: prompt || id.replace(/_/g, ' ').toLowerCase(),
});

export const DEFENCE_ONTOLOGY: DefenceBranch[] = [
  {
    id: 'Military_Forces',
    label: 'Military Forces',
    children: [
      {
        id: 'Armored_Vehicles',
        label: 'Armored Vehicles',
        objects: [
          object('Tank', 'military tank'),
          object('APC_Wheeled', 'wheeled armored personnel carrier'),
          object('APC_Tracked', 'tracked armored personnel carrier'),
          object('IFV', 'infantry fighting vehicle'),
          object('Engineering_Vehicle', 'military engineering vehicle'),
        ],
      },
      {
        id: 'Artillery',
        label: 'Artillery',
        objects: [
          object('Self_Propelled_Artillery', 'self propelled artillery vehicle'),
          object('Towed_Artillery', 'towed artillery gun'),
          object('Rocket_Launcher', 'multiple rocket launcher vehicle'),
          object('Mortar_Position', 'mortar firing position'),
        ],
      },
      {
        id: 'Tactical_Vehicles',
        label: 'Tactical Vehicles',
        objects: [
          object('Cargo_Truck', 'military cargo truck'),
          object('Fuel_Truck', 'fuel tanker truck'),
          object('Command_Vehicle', 'military command vehicle'),
          object('Maintenance_Vehicle', 'military maintenance vehicle'),
          object('Convoy', 'military vehicle convoy'),
        ],
      },
    ],
  },
  {
    id: 'Air_Defense_EW',
    label: 'Air Defense / EW',
    children: [
      {
        id: 'SAM_System',
        label: 'SAM System',
        objects: [
          object('TEL', 'transporter erector launcher'),
          object('TELAR', 'transporter erector launcher and radar'),
          object('Fixed_Launch_Site', 'fixed surface to air missile launch site'),
          object('Missile_Canister', 'missile canister'),
        ],
      },
      {
        id: 'Radar',
        label: 'Radar',
        objects: [
          object('Surveillance_Radar', 'surveillance radar'),
          object('Fire_Control_Radar', 'fire control radar'),
          object('GCI_Radar', 'ground controlled interception radar'),
          object('Counter_Battery_Radar', 'counter battery radar'),
        ],
      },
      {
        id: 'Electronic_Warfare',
        label: 'Electronic Warfare',
        objects: [
          object('Jammer', 'electronic warfare jammer'),
          object('Antenna_Farm', 'antenna farm'),
          object('Signal_Intercept_Site', 'signal intercept site'),
        ],
      },
    ],
  },
  {
    id: 'Missile_Strategic',
    label: 'Missile / Strategic',
    objects: [
      object('Ballistic_Missile_TEL', 'ballistic missile transporter erector launcher'),
      object('Launch_Pad', 'missile launch pad'),
      object('Service_Tower', 'launch service tower'),
      object('Missile_Storage_Bunker', 'missile storage bunker'),
      object('Tunnel_Portal', 'tunnel portal'),
      object('Fueling_Infrastructure', 'missile fueling infrastructure'),
    ],
  },
  {
    id: 'Military_Installations',
    label: 'Military Installations',
    objects: [
      object('Base', 'military base'),
      object('Barracks', 'military barracks'),
      object('Motor_Pool', 'military motor pool'),
      object('Training_Area', 'military training area'),
      object('Command_Bunker', 'command bunker'),
      object('Communications_Node', 'military communications node'),
      object('Temporary_Camp', 'temporary military camp'),
    ],
  },
  {
    id: 'Logistics',
    label: 'Logistics',
    objects: [
      object('Supply_Depot', 'supply depot'),
      object('Ammunition_Depot', 'ammunition depot'),
      object('Fuel_Depot', 'fuel depot'),
      object('Maintenance_Area', 'vehicle maintenance area'),
      object('Railhead', 'military railhead'),
      object('Container_Yard', 'container yard'),
    ],
  },
  {
    id: 'Airfield_Aviation',
    label: 'Airfield / Aviation',
    objects: [
      object('Runway', 'runway'),
      object('Taxiway', 'taxiway'),
      object('Hangar', 'aircraft hangar'),
      object('Hardened_Aircraft_Shelter', 'hardened aircraft shelter'),
      object('Fighter_Aircraft', 'fighter aircraft'),
      object('Helicopter', 'helicopter'),
      object('UAV', 'unmanned aerial vehicle'),
      object('Ground_Support_Equipment', 'airfield ground support equipment'),
    ],
  },
  {
    id: 'Naval_Maritime',
    label: 'Naval / Maritime',
    objects: [
      object('Aircraft_Carrier', 'aircraft carrier'),
      object('Warship', 'warship'),
      object('Patrol_Boat', 'patrol boat'),
      object('Submarine_Pen', 'submarine pen'),
      object('Naval_Pier', 'naval pier'),
      object('Dry_Dock', 'dry dock'),
      object('Landing_Craft', 'landing craft'),
    ],
  },
  {
    id: 'Fortifications_Obstacles',
    label: 'Fortifications / Obstacles',
    objects: [
      object('Trench', 'military trench'),
      object('Bunker', 'bunker'),
      object('Revetment', 'revetment'),
      object('Minefield', 'minefield'),
      object('Anti_Tank_Ditch', 'anti tank ditch'),
      object('Concertina_Wire', 'concertina wire obstacle'),
      object('Roadblock', 'roadblock'),
      object('Breach_Lane', 'breach lane'),
    ],
  },
  {
    id: 'Camouflage_Deception',
    label: 'Camouflage / Deception',
    objects: [
      object('Camouflage_Net', 'camouflage net'),
      object('Concealed_Vehicle', 'concealed vehicle'),
      object('Concealed_Bivouac', 'concealed bivouac'),
      object('Decoy_Tank', 'decoy tank'),
      object('Decoy_Aircraft', 'decoy aircraft'),
      object('Decoy_Radar', 'decoy radar'),
      object('Artificial_Material_Anomaly', 'artificial material anomaly'),
    ],
  },
  {
    id: 'Activity_Change',
    label: 'Activity / Change',
    objects: [
      object('Vehicle_Track', 'vehicle track mark'),
      object('Fresh_Earth_Disturbance', 'fresh earth disturbance'),
      object('New_Excavation', 'new excavation'),
      object('New_Trench', 'new trench'),
      object('New_Temporary_Structure', 'new temporary structure'),
      object('Object_Moved', 'moved object'),
      object('Object_Removed', 'removed object'),
      object('Increased_Depot_Activity', 'increased depot activity'),
    ],
  },
  {
    id: 'Industrial_Dual_Use',
    label: 'Industrial / Dual Use',
    objects: [
      object('Factory', 'factory'),
      object('Refinery', 'oil refinery'),
      object('Chemical_Plant', 'chemical plant'),
      object('Power_Plant', 'power plant'),
      object('Storage_Tank', 'storage tank'),
      object('Rail_Tank_Car', 'rail tank car'),
      object('Security_Perimeter', 'security perimeter'),
      object('Heavy_Crane', 'heavy crane'),
    ],
  },
  {
    id: 'Transportation_Terrain',
    label: 'Transportation / Terrain',
    objects: [
      object('Road', 'road'),
      object('Highway_Bridge', 'highway bridge'),
      object('Rail_Bridge', 'rail bridge'),
      object('Rail_Yard', 'rail yard'),
      object('Ferry_Crossing', 'ferry crossing'),
      object('Pontoon_Bridge', 'pontoon bridge'),
      object('Trafficable_Corridor', 'trafficable corridor'),
      object('Beach_Landing_Zone', 'beach landing zone'),
    ],
  },
  {
    id: 'Urban_Tactical',
    label: 'Urban Tactical',
    objects: [
      object('Building', 'building'),
      object('Wall', 'wall'),
      object('Gate', 'gate'),
      object('Courtyard', 'courtyard'),
      object('Checkpoint', 'checkpoint'),
      object('Barricade', 'barricade'),
      object('Rooftop_Position', 'rooftop fighting position'),
      object('Rubble', 'rubble'),
    ],
  },
  {
    id: 'Battle_Damage',
    label: 'Battle Damage',
    objects: [
      object('Crater', 'impact crater'),
      object('Burn_Scar', 'burn scar'),
      object('Collapsed_Roof', 'collapsed roof'),
      object('Destroyed_Vehicle', 'destroyed vehicle'),
      object('Damaged_Runway', 'damaged runway'),
      object('Damaged_Bridge', 'damaged bridge'),
      object('Destroyed_Radar', 'destroyed radar'),
      object('Secondary_Detonation_Signature', 'secondary detonation signature'),
      object('Repair_Activity', 'repair activity'),
    ],
  },
];

function collectObjects(branches: DefenceBranch[]): DefenceObject[] {
  return branches.flatMap((branch) => [
    ...(branch.objects || []),
    ...collectObjects(branch.children || []),
  ]);
}

export const DEFENCE_OBJECTS = collectObjects(DEFENCE_ONTOLOGY);

export function parseCustomPrompts(value: string): string[] {
  const seen = new Set<string>();
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item) => {
      const key = item.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

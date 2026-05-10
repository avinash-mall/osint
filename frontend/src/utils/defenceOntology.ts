import rawOntology from './defenceOntology.json';

export type Sensor = 'optical' | 'sar' | 'multispectral' | 'thermal';

export type BranchIconKey =
  | 'military'
  | 'airDefense'
  | 'missile'
  | 'installation'
  | 'logistics'
  | 'airfield'
  | 'naval'
  | 'fortification'
  | 'camouflage'
  | 'activity'
  | 'industrial'
  | 'transport'
  | 'urban'
  | 'damage'
  | 'other';

export interface DefenceObject {
  id: string;
  label: string;
  prompt: string;
  sensors: Sensor[];
  minGsdMeters: number;
}

export interface DefenceBranch {
  id: string;
  label: string;
  color: string;
  short: string;
  iconKey: BranchIconKey;
  matchers: RegExp[];
  children?: DefenceBranch[];
  objects?: DefenceObject[];
}

interface RawObject {
  id: string;
  label: string;
  prompt: string;
  sensors?: Sensor[];
  minGsdMeters?: number;
}

interface RawBranch {
  id: string;
  label: string;
  color: string;
  short: string;
  iconKey: BranchIconKey;
  matchers?: string[];
  children?: RawBranch[];
  objects?: RawObject[];
}

interface RawOntology {
  version: string;
  description?: string;
  defaults: { sensors: Sensor[]; minGsdMeters: number };
  branches: RawBranch[];
}

const DATA = rawOntology as RawOntology;
const DEFAULT_SENSORS: Sensor[] = DATA.defaults?.sensors ?? ['optical', 'sar'];
const DEFAULT_MIN_GSD: number = DATA.defaults?.minGsdMeters ?? 1.0;

export const ONTOLOGY_VERSION = DATA.version;
export const ONTOLOGY_DEFAULTS = { sensors: DEFAULT_SENSORS, minGsdMeters: DEFAULT_MIN_GSD };

function compileObject(raw: RawObject): DefenceObject {
  return {
    id: raw.id,
    label: raw.label,
    prompt: raw.prompt,
    sensors: raw.sensors && raw.sensors.length ? raw.sensors : DEFAULT_SENSORS,
    minGsdMeters: typeof raw.minGsdMeters === 'number' ? raw.minGsdMeters : DEFAULT_MIN_GSD,
  };
}

function compileBranch(raw: RawBranch): DefenceBranch {
  return {
    id: raw.id,
    label: raw.label,
    color: raw.color,
    short: raw.short,
    iconKey: raw.iconKey,
    matchers: (raw.matchers || []).map((pattern) => new RegExp(pattern)),
    children: raw.children?.map(compileBranch),
    objects: raw.objects?.map(compileObject),
  };
}

export const DEFENCE_ONTOLOGY: DefenceBranch[] = DATA.branches.map(compileBranch);

export const OTHER_BRANCH: DefenceBranch = {
  id: 'Other',
  label: 'Other',
  color: '#727a83',
  short: 'OTH',
  iconKey: 'other',
  matchers: [],
};

export const ALL_BRANCHES: DefenceBranch[] = [...DEFENCE_ONTOLOGY, OTHER_BRANCH];

function collectObjects(branches: DefenceBranch[]): DefenceObject[] {
  return branches.flatMap((branch) => [
    ...(branch.objects || []),
    ...collectObjects(branch.children || []),
  ]);
}

export const DEFENCE_OBJECTS: DefenceObject[] = collectObjects(DEFENCE_ONTOLOGY);

export const BRANCH_ORDER = ALL_BRANCHES.map((branch) => branch.id);

export type BranchId = (typeof BRANCH_ORDER)[number];

const BRANCH_BY_ONTOLOGY_BUCKET: Record<string, BranchId> = {
  air: 'Airfield_Aviation',
  maritime: 'Naval_Maritime',
  ground: 'Transportation_Terrain',
  combat: 'Military_Forces',
  infrastructure: 'Industrial_Dual_Use',
  facility: 'Urban_Tactical',
  energy: 'Industrial_Dual_Use',
  logistics: 'Logistics',
  unknown: 'Other',
};

// Classification priority — different from JSON display order. Branches with
// the most specific tokens come first so generic matchers (`\baircraft\b`,
// `\btank\b`, `\bbuilding\b`) don't steal detections that belong elsewhere.
// Examples: "aircraft carrier" must hit Naval before Airfield's `\baircraft\b`;
// "storage tank" must hit Industrial before Military's `\btank\b`.
const CLASSIFY_ORDER: BranchId[] = [
  'Battle_Damage',
  'Activity_Change',
  'Auxiliary',
  'Missile_Strategic',
  'Air_Defense_EW',
  'Naval_Maritime',
  'Industrial_Dual_Use',
  'Logistics',
  'Airfield_Aviation',
  'Fortifications_Obstacles',
  'Military_Forces',
  'Transportation_Terrain',
  'Military_Installations',
  'Urban_Tactical',
];

const BRANCH_BY_ID: Record<string, DefenceBranch> = DEFENCE_ONTOLOGY.reduce((acc, branch) => {
  acc[branch.id] = branch;
  return acc;
}, {} as Record<string, DefenceBranch>);

export function classifyToBranch(rawValue?: string | null, ontologyBucket?: string | null): BranchId {
  // Normalise to space-separated tokens. Underscore-separated form was breaking
  // `\bword\b` boundaries (JS treats `_` as a word character), so phrases like
  // "field_bivouac_site" never matched `\bbivouac\b`. Spaces let the boundary
  // anchors work and let multi-word literal patterns match inserted modifiers.
  const raw = String(rawValue || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  for (const branchId of CLASSIFY_ORDER) {
    const branch = BRANCH_BY_ID[branchId];
    if (!branch) continue;
    for (const matcher of branch.matchers) {
      if (matcher.test(raw)) return branch.id as BranchId;
    }
  }
  const bucket = BRANCH_BY_ONTOLOGY_BUCKET[String(ontologyBucket || '').toLowerCase()];
  return bucket || 'Other';
}

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

export function objectMatchesSensor(obj: DefenceObject, sensor: Sensor): boolean {
  // Thermal cameras image at similar spatial detail to optical, so any
  // optical-tagged object is at least observable on thermal too. The explicit
  // 'thermal' tag still marks objects whose primary signature is heat
  // (fires, plumes) — useful for prompt prioritisation in the UI.
  if (sensor === 'thermal') return obj.sensors.includes('thermal') || obj.sensors.includes('optical');
  return obj.sensors.includes(sensor);
}

export function objectsForSensor(sensor: Sensor): DefenceObject[] {
  return DEFENCE_OBJECTS.filter((obj) => objectMatchesSensor(obj, sensor));
}

export function isHighResolutionOnly(obj: DefenceObject): boolean {
  return obj.minGsdMeters <= 0.3;
}

/**
 * Sentinel prompts (e.g. `__prithvi_burn__`, `__prithvi_crop_corn__`) live in
 * the JSON so the Aux Layers branch shows up in the picker / legend, but they
 * are NOT real text prompts for SAM 3 — they're marker strings that the
 * specialist heads (Prithvi burn/flood/crop) emit independently. Strip them
 * before sending the prompt list to the inference service.
 */
export function isSentinelPrompt(prompt: string): boolean {
  return prompt.startsWith('__') && prompt.endsWith('__');
}

export function isSam3Prompt(prompt: string): boolean {
  return !isSentinelPrompt(prompt);
}

const UPLOAD_SENSOR_TO_TAG: Record<string, Sensor> = {
  optical: 'optical',
  radar: 'sar',
  thermal: 'thermal',
};

export function uploadSensorToTag(uploadSensor: string): Sensor {
  return UPLOAD_SENSOR_TO_TAG[uploadSensor.toLowerCase()] ?? 'optical';
}

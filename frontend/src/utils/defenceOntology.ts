/**
 * Legacy module-level exports for the defence ontology.
 *
 * As of Step 10 of the ontology refactor the static `defenceOntology.json`
 * file is gone — the ontology is loaded from the `/api/ontology` endpoint
 * at runtime via the `useOntology()` hook (see `./useOntology.ts`).
 *
 * This file is kept so existing imports keep type-checking and so a few
 * pure helpers (`pipelineForSensor`, `parseCustomPrompts`, sensor
 * matching) remain available without forcing every caller through the
 * hook. The data-bearing exports (`DEFENCE_ONTOLOGY`, `DEFENCE_OBJECTS`,
 * `ALL_BRANCHES`, `BRANCH_ORDER`) are now empty arrays — components that
 * need live ontology data MUST switch to `useOntology()`.
 */

export type Sensor = 'optical' | 'sar' | 'multispectral' | 'hyperspectral' | 'thermal';

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

export const OTHER_BRANCH: DefenceBranch = {
  id: 'Other',
  label: 'Other',
  color: '#727a83',
  short: 'OTH',
  iconKey: 'other',
  matchers: [],
};

// Empty defaults — see file header. Live data lives in `useOntology()`.
export const DEFENCE_ONTOLOGY: DefenceBranch[] = [];
export const ALL_BRANCHES: DefenceBranch[] = [OTHER_BRANCH];
export const DEFENCE_OBJECTS: DefenceObject[] = [];
export const BRANCH_ORDER: string[] = [OTHER_BRANCH.id];

export type BranchId = string;

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
 * Sentinel prompts are `__double_underscore__` marker strings that a
 * specialist head may emit independently — they are NOT real text prompts for
 * SAM 3. Strip them before sending the prompt list to the inference service.
 */
export function isSentinelPrompt(prompt: string): boolean {
  return prompt.startsWith('__') && prompt.endsWith('__');
}

export function isSam3Prompt(prompt: string): boolean {
  return !isSentinelPrompt(prompt);
}

const UPLOAD_SENSOR_TO_TAG: Record<string, Sensor> = {
  optical: 'optical',
  rgb: 'optical',
  multispectral: 'multispectral',
  hyperspectral: 'hyperspectral',
  sar: 'sar',
  radar: 'sar',
  thermal: 'thermal',
};

export function uploadSensorToTag(uploadSensor: string): Sensor {
  return UPLOAD_SENSOR_TO_TAG[uploadSensor.toLowerCase()] ?? 'optical';
}

/**
 * Maps the upload-page sensor selection onto the inference service's
 * `modality` parameter and the recommended specialist layers (`enabled_layers`).
 *
 * Decisions backed by docs/inference_layer_comparison.md:
 *   - optical  → SAM3 + DOTA_OBB (mAP 0.05→0.61) + DINOV3_SAT (re-ID embedding,
 *                only fires when SAM3_EMBED_DETECTIONS=1). GROUNDING_DINO is
 *                loaded but auto-gated server-side for in-vocab prompts.
 *   - multispectral → SAM3 + DINOV3_SAT (re-ID embedding on the multispectral
 *                preview).
 *   - hyperspectral → SAM3 only. The multispectral specialist heads were
 *                trained on HLS 6-band multispectral, not hyperspectral; we
 *                surface this as a UI warning so callers know quality may be
 *                lower.
 *   - sar      → SAM3 + TERRAMIND (S1→RGB preview + embedding pool).
 */
export interface SensorPipeline {
  /** modality string sent to /detect */
  modality: 'rgb' | 'multispectral' | 'sar';
  /** specialist layers to request (sam3 always implicit) */
  enabledLayers: string[];
  /** human-readable model labels for the UI badge */
  models: string[];
  /** optional warning shown under the dropdown */
  warning?: string;
}

const SENSOR_PIPELINE: Record<string, SensorPipeline> = {
  optical: {
    modality: 'rgb',
    enabledLayers: ['sam3', 'dota_obb', 'grounding_dino', 'dinov3_sat', 'mvrsd'],
    models: ['SAM3', 'DOTA-OBB', 'GroundingDINO (auto-gated)', 'DINOV3_SAT', 'MVRSD (military vehicles)'],
  },
  multispectral: {
    modality: 'multispectral',
    enabledLayers: ['sam3', 'dinov3_sat'],
    models: ['SAM3', 'DINOV3_SAT'],
  },
  hyperspectral: {
    modality: 'multispectral',
    enabledLayers: ['sam3', 'dinov3_sat'],
    models: ['SAM3', 'DINOV3_SAT'],
    warning:
      'Native hyperspectral support is experimental — the request is forwarded to the multispectral pipeline. Quality on >6-band data is not validated.',
  },
  sar: {
    modality: 'sar',
    enabledLayers: ['sam3', 'terramind'],
    models: ['SAM3 (on TERRAMIND-rendered preview)', 'TERRAMIND'],
  },
  thermal: {
    modality: 'rgb',
    enabledLayers: ['sam3', 'grounding_dino'],
    models: ['SAM3', 'GroundingDINO'],
    warning: 'No thermal-specialist model is loaded — request is treated as RGB.',
  },
};

export function pipelineForSensor(sensor: string): SensorPipeline {
  return SENSOR_PIPELINE[sensor.toLowerCase()] ?? SENSOR_PIPELINE.optical;
}

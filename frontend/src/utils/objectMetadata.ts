/**
 * Single source of truth for detection metadata enums + color mappings.
 *
 * Previously these lived in three places (atoms.tsx, ObjectDetailsForm.tsx,
 * inline literals in map/fmv components). Consolidating here means adding a
 * new threat level or affiliation is a one-file change.
 *
 * Imported by:
 *   - components/ObjectDetailsForm.tsx
 *   - components/atoms.tsx (threatColor, AffGlyph)
 *   - components/map/SelectionPanel.tsx
 *   - components/admin/OntologyAdmin.tsx
 *   - utils/detectionTaxonomy.ts (re-exports for back-compat)
 */

export type ThreatLevelId = 'critical' | 'high' | 'medium' | 'low' | 'none' | 'unrated';
export type AffiliationId = 'friend' | 'hostile' | 'neutral' | 'unknown';

export type ThreatLevel = {
  id: ThreatLevelId;
  label: string;
  /** CSS color reference — always a custom property, so a theme swap recolors everything. */
  color: string;
  /** Severity index (5 = critical, 0 = unrated). Useful for sort. */
  severity: number;
};

export type Affiliation = {
  id: AffiliationId;
  label: string;
  color: string;
};

export const THREAT_LEVELS: ReadonlyArray<ThreatLevel> = [
  { id: 'critical', label: 'CRITICAL', color: 'var(--nato-hostile)', severity: 5 },
  { id: 'high',     label: 'HIGH',     color: 'var(--accent)',       severity: 4 },
  { id: 'medium',   label: 'MEDIUM',   color: 'var(--nato-unknown)', severity: 3 },
  { id: 'low',      label: 'LOW',      color: 'var(--nato-neutral)', severity: 2 },
  { id: 'none',     label: 'NONE',     color: 'var(--ink-3)',        severity: 1 },
  { id: 'unrated',  label: 'UNRATED',  color: 'var(--ink-3)',        severity: 0 },
] as const;

export const AFFILIATIONS: ReadonlyArray<Affiliation> = [
  { id: 'friend',  label: 'FRIEND',  color: 'var(--nato-friend)'  },
  { id: 'hostile', label: 'HOSTILE', color: 'var(--nato-hostile)' },
  { id: 'neutral', label: 'NEUTRAL', color: 'var(--nato-neutral)' },
  { id: 'unknown', label: 'UNKNOWN', color: 'var(--nato-unknown)' },
] as const;

/** Default values used when a detection hasn't been operator-edited yet. */
export const DEFAULT_THREAT: ThreatLevelId = 'unrated';
export const DEFAULT_AFFILIATION: AffiliationId = 'unknown';

const THREAT_BY_ID = new Map(THREAT_LEVELS.map((t) => [t.id, t]));
const AFF_BY_ID = new Map(AFFILIATIONS.map((a) => [a.id, a]));

export function threatLevel(id: string | undefined | null): ThreatLevel {
  return THREAT_BY_ID.get((id || DEFAULT_THREAT) as ThreatLevelId) ?? THREAT_BY_ID.get(DEFAULT_THREAT)!;
}

export function affiliation(id: string | undefined | null): Affiliation {
  return AFF_BY_ID.get((id || DEFAULT_AFFILIATION) as AffiliationId) ?? AFF_BY_ID.get(DEFAULT_AFFILIATION)!;
}

export function threatColor(id: string | undefined | null): string {
  return threatLevel(id).color;
}

export function natoColor(id: string | undefined | null): string {
  return affiliation(id).color;
}

/** Operator-editable metadata for one detection. Mirrors the backend row. */
export type ObjectDetails = {
  designation?: string;
  object_class?: string;
  military_classification?: string;
  threat_level?: ThreatLevelId | string;
  affiliation?: AffiliationId | string;
  confidence_override?: number;
  notes?: string;
  updated_at?: string;
  updated_by?: string;
};

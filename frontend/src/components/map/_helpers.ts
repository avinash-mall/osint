/**
 * Pure helpers extracted from the GaiaMap monolith.
 *
 * Nothing in this file touches React or react-leaflet — just lat/lon math,
 * detection-property accessors, ontology lookups, and styling decisions.
 * Importers (GaiaMap, MapStage, LayerPanel) get small, stable utilities
 * with no lifecycle cost.
 */

import L from 'leaflet';
import {
  branchIdForFeature,
  categoryFor,
  type DetectionCategoryId,
  type DetectionCategoryMap,
} from '../../utils/detectionTaxonomy';


/* ── Detection feature accessors ──────────────────────────────────────── */

export function detectionLabel(feature: any): string {
  const props = feature?.properties || {};
  return String(
    props.original_class ||
    props.metadata?.original_class ||
    props.class ||
    props.label ||
    'Unknown',
  );
}

export function detectionClassKeys(feature: any): string[] {
  const props = feature?.properties || {};
  return Array.from(new Set(
    [
      props.original_class,
      props.metadata?.original_class,
      props.class,
      props.parent_class,
      props.metadata?.parent_class,
    ]
      .filter(Boolean)
      .map((value) => String(value)),
  ));
}

/**
 * Server-computed branch id is the source of truth. Features without a
 * `branch_id` fall to `Other` (the legend's catch-all bucket).
 */
export function detectionCategoryForFeature(feature: any): DetectionCategoryId {
  return branchIdForFeature(feature);
}

export function confidenceValue(feature: any): number {
  const confidence = Number(feature?.properties?.confidence);
  return Number.isFinite(confidence) ? confidence : 0;
}

export function detectionCenter(feature: any): [number, number] | null {
  const geometry = feature?.geometry;
  const coordinates = geometry?.coordinates;
  if (!geometry || !coordinates) return null;
  if (geometry.type === 'Point' && coordinates.length >= 2) {
    return [Number(coordinates[1]), Number(coordinates[0])];
  }
  const ring =
    geometry.type === 'Polygon' ? coordinates?.[0] :
    geometry.type === 'MultiPolygon' ? coordinates?.[0]?.[0] :
    null;
  if (!Array.isArray(ring) || ring.length === 0) return null;
  const points = ring.filter((point: any) => Array.isArray(point) && point.length >= 2);
  if (points.length === 0) return null;
  const lon = points.reduce((sum: number, point: any) => sum + Number(point[0]), 0) / points.length;
  const lat = points.reduce((sum: number, point: any) => sum + Number(point[1]), 0) / points.length;
  return Number.isFinite(lat) && Number.isFinite(lon) ? [lat, lon] : null;
}

export function detectionBadgePosition(feature: any): [number, number] | null {
  const geometry = feature?.geometry;
  const coordinates = geometry?.coordinates;
  if (!geometry || !coordinates) return null;
  if (geometry.type === 'Point' && coordinates.length >= 2) {
    return [Number(coordinates[1]), Number(coordinates[0])];
  }

  const points: Array<[number, number]> = [];
  const collectPoints = (items: any) => {
    if (!Array.isArray(items)) return;
    if (items.length >= 2 && typeof items[0] === 'number' && typeof items[1] === 'number') {
      const lon = Number(items[0]);
      const lat = Number(items[1]);
      if (Number.isFinite(lat) && Number.isFinite(lon)) points.push([lat, lon]);
      return;
    }
    for (const item of items) collectPoints(item);
  };
  collectPoints(coordinates);
  if (points.length === 0) return null;

  const north = Math.max(...points.map(([lat]) => lat));
  const west = Math.min(...points.map(([, lon]) => lon));
  return [north, west];
}

/**
 * Convert a GeoJSON `Polygon` / `MultiPolygon` geometry into the nested
 * `[lat, lng]` array shape react-leaflet's `<Polygon positions>` expects.
 * GeoJSON stores coordinates as `[lon, lat]`; Leaflet wants `[lat, lon]`.
 * Returns `null` for `Point`, missing, or degenerate (<3-vertex) geometry so
 * callers can skip rendering rather than draw a broken box.
 */
export function geojsonToLatLngs(
  geometry: any,
): L.LatLngExpression[][] | L.LatLngExpression[][][] | null {
  if (!geometry || !geometry.coordinates) return null;

  const ringToLatLng = (ring: any): L.LatLngExpression[] | null => {
    if (!Array.isArray(ring)) return null;
    const out: L.LatLngExpression[] = [];
    for (const pt of ring) {
      if (
        Array.isArray(pt) && pt.length >= 2 &&
        typeof pt[0] === 'number' && typeof pt[1] === 'number'
      ) {
        out.push([pt[1], pt[0]]);
      }
    }
    return out.length >= 3 ? out : null;
  };

  if (geometry.type === 'Polygon') {
    const rings = (geometry.coordinates as any[])
      .map(ringToLatLng)
      .filter((r): r is L.LatLngExpression[] => r !== null);
    return rings.length ? rings : null;
  }

  if (geometry.type === 'MultiPolygon') {
    const polys = (geometry.coordinates as any[])
      .map((poly: any) =>
        (poly as any[])
          .map(ringToLatLng)
          .filter((r): r is L.LatLngExpression[] => r !== null),
      )
      .filter((p) => p.length > 0);
    return polys.length ? polys : null;
  }

  return null;
}


/* ── Detection-track domain types + styling ───────────────────────────── */

export interface DetectionTrackHistoryPoint {
  lat: number;
  lng: number;
  time: string;
  detection_id: number;
  seq_index: number;
  cost: number;
}

export interface DetectionTrack {
  id: string;
  track_uid: string;
  primary_class: string;
  category: string;
  threat_level: string;
  status: 'tentative' | 'confirmed' | 'coast' | 'lost' | 'pinned';
  pinned: boolean;
  obs_count: number;
  miss_count: number;
  first_seen: string | null;
  last_seen: string | null;
  latest: { lat: number; lon: number; class: string };
  history: DetectionTrackHistoryPoint[];
  path_geojson: string | null;
  last_velocity: { vx_mps?: number; vy_mps?: number };
  metadata: Record<string, unknown>;
}

export const TRACKER_CATEGORY_TO_CATEGORY_ID: Record<string, DetectionCategoryId> = {
  maritime: 'Naval_Maritime',
  ground: 'Transportation_Terrain',
  air: 'Airfield_Aviation',
  combat: 'Military_Forces',
  infrastructure: 'Industrial_Dual_Use',
  facility: 'Urban_Tactical',
  energy: 'Industrial_Dual_Use',
  logistics: 'Logistics',
  default: 'Other',
  unknown: 'Other',
};

export function trackColorFor(category: string, categories: DetectionCategoryMap): string {
  const catId = TRACKER_CATEGORY_TO_CATEGORY_ID[category] ?? 'Other';
  return categoryFor(catId, categories).color;
}

export function trackDashArray(status: DetectionTrack['status']): string | undefined {
  if (status === 'confirmed' || status === 'pinned') return undefined;
  if (status === 'coast') return '4 6';
  if (status === 'tentative') return '2 8';
  return undefined;
}


/* ── Time helpers ─────────────────────────────────────────────────────── */

export function relativeTime(iso: string | null): string {
  if (!iso) return 'never';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function timestampInRange(
  timestamp: string | null | undefined,
  range: { start: string; end: string },
): boolean {
  if (!timestamp) return false;
  const time = new Date(timestamp).getTime();
  const start = new Date(range.start).getTime();
  const end = new Date(range.end).getTime();
  return Number.isFinite(time) && Number.isFinite(start) && Number.isFinite(end) && time >= start && time <= end;
}


/* ── Bounds helpers ───────────────────────────────────────────────────── */

export function extendBoundsWithCoordinates(bounds: L.LatLngBounds, coordinates: any): void {
  if (!Array.isArray(coordinates)) return;
  if (coordinates.length >= 2 && typeof coordinates[0] === 'number' && typeof coordinates[1] === 'number') {
    const lon = Number(coordinates[0]);
    const lat = Number(coordinates[1]);
    if (Number.isFinite(lat) && Number.isFinite(lon)) bounds.extend([lat, lon]);
    return;
  }
  for (const item of coordinates) extendBoundsWithCoordinates(bounds, item);
}

export function geojsonFeatureBounds(geojson: any): L.LatLngBounds | null {
  const bounds = L.latLngBounds([]);
  for (const feature of geojson?.features || []) {
    extendBoundsWithCoordinates(bounds, feature?.geometry?.coordinates);
  }
  return bounds.isValid() ? bounds : null;
}

export function featureCentroid(feature: any): [number, number] | null {
  if (!feature?.geometry) return null;
  const bounds = L.latLngBounds([]);
  extendBoundsWithCoordinates(bounds, feature.geometry.coordinates);
  if (!bounds.isValid()) return null;
  const center = bounds.getCenter();
  return [center.lat, center.lng];
}

export function featureLatLonBounds(
  feature: any,
): { south: number; west: number; north: number; east: number } | null {
  if (!feature?.geometry) return null;
  const bounds = L.latLngBounds([]);
  extendBoundsWithCoordinates(bounds, feature.geometry.coordinates);
  if (!bounds.isValid()) return null;
  return {
    south: bounds.getSouth(),
    west: bounds.getWest(),
    north: bounds.getNorth(),
    east: bounds.getEast(),
  };
}


/* ── Threat + style ───────────────────────────────────────────────────── */

export function threatClass(level?: string): '' | 'crit' | 'warn' | 'acc' {
  switch (String(level || '').toLowerCase()) {
    case 'critical': return 'crit';
    case 'high':     return 'warn';
    case 'medium':   return 'acc';
    default:         return '';
  }
}

export const HEAVY_OUTLINE_CATEGORIES: ReadonlySet<DetectionCategoryId> = new Set([
  'Military_Forces',
  'Air_Defense_EW',
  'Missile_Strategic',
  'Battle_Damage',
  'Industrial_Dual_Use',
] as DetectionCategoryId[]);

/**
 * Map an estimated footprint area (m²) to a point-marker radius (px).
 * Log10 scale so a 1 m² vehicle, a 10 000 m² building, and a 1 km² stadium
 * are all visually distinguishable at low zoom. Returns 4 (current default)
 * when the estimate is absent or not finite.
 */
export function sizeAwareRadius(areaM2: unknown): number {
  const area = Number(areaM2);
  if (!Number.isFinite(area) || area <= 0) return 4;
  const radius = 3 + Math.log10(area) * 2;
  return Math.max(3, Math.min(14, radius));
}

export function makeDetectionStyle(categories: DetectionCategoryMap) {
  return (feature: any) => {
    const category = detectionCategoryForFeature(feature);
    const color = categoryFor(category, categories).color;
    const isHeavy = HEAVY_OUTLINE_CATEGORIES.has(category);
    return {
      color,
      // Solid, slightly heavier outline so detection boxes stay perceptible
      // at medium zoom — they are the analyst's primary geo-truth marker.
      weight: isHeavy ? 2.4 : 2,
      opacity: 1,
      fillColor: color,
      fillOpacity: confidenceValue(feature) > 0.85 ? 0.14 : 0.05,
      // Military forces keep a tight dash so they read apart from the rest;
      // every other category draws a solid box.
      dashArray: category === 'Military_Forces' ? '6, 3' : undefined,
    };
  };
}


/* ── Class-stat shape used by the Classes tab ─────────────────────────── */

export type DetectionClassStat = {
  rawClass: string;
  parentClass?: string;
  label: string;
  displayLabel?: string;
  labelSource?: 'deterministic' | 'llm_advisory';
  amgImageCount?: number;
  count: number;
  maxConfidence: number;
  color: string;
  ontology?: any;
  threatLevel?: string;
  category: DetectionCategoryId;
  source: string;
  /**
   * LLM suggestion for this class. YOLOE-PF imagery AMG rows may promote the
   * advisory label to displayLabel; deterministic category/threat/rawClass
   * remain the filtering and audit authority.
   */
  llmAdvisory?: {
    label?: string | null;
    description?: string | null;
    recommended_filter?: string | null;
    generated_by?: string | null;
  } | null;
};

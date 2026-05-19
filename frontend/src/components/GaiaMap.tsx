import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Circle, CircleMarker, GeoJSON, ImageOverlay, MapContainer, Marker, Polyline, Popup, Rectangle, TileLayer, useMap, useMapEvents, ZoomControl } from 'react-leaflet';
import L from 'leaflet';
import axios from 'axios';
import { renderToStaticMarkup } from 'react-dom/server';
import { forward as mgrsForward } from 'mgrs';
import {
  Activity,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  CircleHelp,
  Crosshair,
  Eye,
  EyeOff,
  GitBranch,
  Layers,
  Minus,
  Navigation,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Satellite,
  Search,
  Send,
  Shield,
  Sparkles,
  Swords,
} from 'lucide-react';
import { objectIconComponent } from '../utils/branchIcons';
import { IconRenderer, iconComponentByKey } from '../utils/iconLibrary';
import 'leaflet/dist/leaflet.css';
import { useEventStream } from '../hooks/useEventStream';
import {
  SOURCE_ORDER,
  branchIdForFeature,
  categoryFor,
  detectionClassLabel,
  detectionClassSource,
  useDetectionCategories,
  type DetectionCategoryId,
  type DetectionCategoryMap,
} from '../utils/detectionTaxonomy';
import type { OntologyBranch } from '../utils/useOntology';
import ObjectDetailsForm from './ObjectDetailsForm';
import { useAuth } from '../hooks/useAuth';
import ReviewPanel from './map/ReviewPanel';
import SimilarPanel from './map/SimilarPanel';
import TimeMachineBar from './map/TimeMachineBar';
import AnalyticsToolsPanel, {
  type AnalyticsKind,
  type AnalyticsPick,
} from './map/AnalyticsToolsPanel';
import type { AnalyticsResponse } from '../services/analytics';

const API_URL = import.meta.env.VITE_API_URL || '';
const TILE_PROXY_URL = import.meta.env.VITE_TILE_PROXY_URL || '/tiles';
const CARTO_BASEMAP_URL = '/basemap/{z}/{x}/{y}.png';
const TERRAIN_BASEMAP_URL = '/terrain/{z}/{x}/{y}.png';
const DETECTION_CENTER_MARKER_LIMIT = 800;
const CanvasGeoJSON = GeoJSON as any;

delete (L.Icon.Default.prototype as any)._getIconUrl;

const createIcon = (color: string) => new L.Icon({
  iconUrl: `data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIlMjMzYjgyZjYiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMjEgMTBsLTkgMTVMMyAxMGw5LTl6Ii8+PC9zdmc+`.replace('%233b82f6', encodeURIComponent(color)),
  iconSize: [20, 20],
  iconAnchor: [10, 20],
  popupAnchor: [0, -20],
});

const blueIcon = createIcon('#4ea1ff');
const redIcon = createIcon('#ff3b30');
const emeraldIcon = createIcon('#3dd68c');

function detectionLabel(feature: any) {
  const props = feature?.properties || {};
  return String(props.original_class || props.metadata?.original_class || props.class || props.label || 'Unknown');
}

function detectionClassKeys(feature: any): string[] {
  const props = feature?.properties || {};
  return Array.from(new Set([
    props.original_class,
    props.metadata?.original_class,
    props.class,
    props.parent_class,
    props.metadata?.parent_class,
  ].filter(Boolean).map((value) => String(value))));
}

/**
 * Server-computed branch id is now the source of truth. The frontend no
 * longer regex-classifies — features without a `branch_id` fall to
 * `Other` (consistent with the legend's catch-all bucket).
 */
function detectionCategoryForFeature(feature: any): DetectionCategoryId {
  return branchIdForFeature(feature);
}

function confidenceValue(feature: any) {
  const confidence = Number(feature?.properties?.confidence);
  return Number.isFinite(confidence) ? confidence : 0;
}

function detectionCenter(feature: any): [number, number] | null {
  const geometry = feature?.geometry;
  const coordinates = geometry?.coordinates;
  if (!geometry || !coordinates) return null;
  if (geometry.type === 'Point' && coordinates.length >= 2) {
    return [Number(coordinates[1]), Number(coordinates[0])];
  }
  const ring = geometry.type === 'Polygon' ? coordinates?.[0] : geometry.type === 'MultiPolygon' ? coordinates?.[0]?.[0] : null;
  if (!Array.isArray(ring) || ring.length === 0) return null;
  const points = ring.filter((point: any) => Array.isArray(point) && point.length >= 2);
  if (points.length === 0) return null;
  const lon = points.reduce((sum: number, point: any) => sum + Number(point[0]), 0) / points.length;
  const lat = points.reduce((sum: number, point: any) => sum + Number(point[1]), 0) / points.length;
  return Number.isFinite(lat) && Number.isFinite(lon) ? [lat, lon] : null;
}

function detectionBadgePosition(feature: any): [number, number] | null {
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

interface DetectionTrackHistoryPoint {
  lat: number;
  lng: number;
  time: string;
  detection_id: number;
  seq_index: number;
  cost: number;
}

interface DetectionTrack {
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

const TRACKER_CATEGORY_TO_CATEGORY_ID: Record<string, DetectionCategoryId> = {
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

function trackColorFor(category: string, categories: DetectionCategoryMap): string {
  const catId = TRACKER_CATEGORY_TO_CATEGORY_ID[category] ?? 'Other';
  return categoryFor(catId, categories).color;
}

function trackDashArray(status: DetectionTrack['status']): string | undefined {
  if (status === 'confirmed' || status === 'pinned') return undefined;
  if (status === 'coast') return '4 6';
  if (status === 'tentative') return '2 8';
  return undefined;
}

function relativeTime(iso: string | null): string {
  if (!iso) return 'never';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function timestampInRange(timestamp: string | null | undefined, range: { start: string; end: string }): boolean {
  if (!timestamp) return false;
  const time = new Date(timestamp).getTime();
  const start = new Date(range.start).getTime();
  const end = new Date(range.end).getTime();
  return Number.isFinite(time) && Number.isFinite(start) && Number.isFinite(end) && time >= start && time <= end;
}

function extendBoundsWithCoordinates(bounds: L.LatLngBounds, coordinates: any): void {
  if (!Array.isArray(coordinates)) return;
  if (coordinates.length >= 2 && typeof coordinates[0] === 'number' && typeof coordinates[1] === 'number') {
    const lon = Number(coordinates[0]);
    const lat = Number(coordinates[1]);
    if (Number.isFinite(lat) && Number.isFinite(lon)) bounds.extend([lat, lon]);
    return;
  }
  for (const item of coordinates) extendBoundsWithCoordinates(bounds, item);
}

function geojsonFeatureBounds(geojson: any): L.LatLngBounds | null {
  const bounds = L.latLngBounds([]);
  for (const feature of geojson?.features || []) {
    extendBoundsWithCoordinates(bounds, feature?.geometry?.coordinates);
  }
  return bounds.isValid() ? bounds : null;
}

function featureCentroid(feature: any): [number, number] | null {
  if (!feature?.geometry) return null;
  const bounds = L.latLngBounds([]);
  extendBoundsWithCoordinates(bounds, feature.geometry.coordinates);
  if (!bounds.isValid()) return null;
  const center = bounds.getCenter();
  return [center.lat, center.lng];
}

function featureLatLonBounds(feature: any): { south: number; west: number; north: number; east: number } | null {
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

function threatClass(level?: string) {
  switch (String(level || '').toLowerCase()) {
    case 'critical':
      return 'crit';
    case 'high':
      return 'warn';
    case 'medium':
      return 'acc';
    default:
      return '';
  }
}

const HEAVY_OUTLINE_CATEGORIES: ReadonlySet<DetectionCategoryId> = new Set([
  'Military_Forces',
  'Air_Defense_EW',
  'Missile_Strategic',
  'Battle_Damage',
  'Industrial_Dual_Use',
] as DetectionCategoryId[]);

function makeDetectionStyle(categories: DetectionCategoryMap) {
  return (feature: any) => {
    const category = detectionCategoryForFeature(feature);
    const color = categoryFor(category, categories).color;
    const isHeavy = HEAVY_OUTLINE_CATEGORIES.has(category);
    return {
      color,
      weight: isHeavy ? 1.8 : 1.3,
      opacity: 0.92,
      fillColor: color,
      fillOpacity: confidenceValue(feature) > 0.85 ? 0.14 : 0.05,
      dashArray: category === 'Military_Forces' ? '2, 3' : '3, 4',
    };
  };
}

type DetectionClassStat = {
  rawClass: string;
  parentClass?: string;
  label: string;
  count: number;
  maxConfidence: number;
  color: string;
  ontology?: any;
  threatLevel?: string;
  category: DetectionCategoryId;
  source: string;
  // Phase 7.29: non-authoritative LLM suggestion for this class. The
  // deterministic label/category/threat above remain the authoritative
  // values shown to the analyst — this advisory just adds an "AI
  // suggested" pill alongside, so model hallucination can be inspected
  // without overriding the model's actual class.
  llmAdvisory?: {
    label?: string | null;
    description?: string | null;
    recommended_filter?: string | null;
    generated_by?: string | null;
  } | null;
};

function CategoryIcon({
  category,
  branchById,
  className = 'h-3.5 w-3.5',
}: {
  category: DetectionCategoryId;
  branchById: Map<string, OntologyBranch>;
  className?: string;
}) {
  const branch = branchById.get(category);
  return <IconRenderer iconKey={branch?.icon_key ?? null} className={className} />;
}

function DetectionSubclassIcon({
  iconKey,
  label,
  category,
  branchById,
  className = 'h-3.5 w-3.5',
}: {
  iconKey?: string | null;
  label?: string | null;
  category: DetectionCategoryId;
  branchById: Map<string, OntologyBranch>;
  className?: string;
}) {
  const branch = branchById.get(category);
  const branchIconKey = branch?.icon_key ?? null;
  // Prefer an explicit iconKey from the feature; fall back to the branch-level
  // key. If neither resolves, fall through to the legacy regex matcher (kept
  // available as a last-resort) and finally to CircleHelp via IconRenderer.
  if (iconKey || branchIconKey) {
    return <IconRenderer iconKey={iconKey ?? null} fallbackBranchKey={branchIconKey as any} className={className} />;
  }
  // Last-resort: regex on the raw label.
  const Icon = objectIconComponent(label, branchIconKey as any);
  return <Icon className={className} />;
}

function makeDetectionIcon(
  categories: DetectionCategoryMap,
  branchById: Map<string, OntologyBranch>,
) {
  return (feature: any) => {
    const category = detectionCategoryForFeature(feature);
    const color = categoryFor(category, categories).color;
    const props = feature?.properties || {};
    const branch = branchById.get(category);
    const branchIconKey = branch?.icon_key ?? null;
    // 1. Explicit icon_key from backend feature properties (Step 9 preferred path).
    // 2. Branch-level icon_key fallback.
    // 3. Legacy regex matcher on the raw class/label.
    const featureIconKey: string | null = props.icon_key ?? null;
    const Icon =
      iconComponentByKey(featureIconKey) ??
      iconComponentByKey(branchIconKey) ??
      objectIconComponent(props.original_class || props.class || props.label, branchIconKey as any);
    const iconMarkup = renderToStaticMarkup(<Icon size={12} strokeWidth={2.2} />);
    return L.divIcon({
      className: '',
      iconSize: [14, 14],
      iconAnchor: [15, 15],
      html: `<div class="sentinel-detection-icon" style="color:${color};border-color:${color};box-shadow:0 0 8px ${color}55;">${iconMarkup}</div>`,
    });
  };
}

function MapBoundsUpdater({ onBoundsChange }: { onBoundsChange: (bounds: string) => void }) {
  const map = useMap();
  useEffect(() => {
    const handleMoveEnd = () => {
      const b = map.getBounds();
      onBoundsChange(`${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`);
    };
    map.on('moveend', handleMoveEnd);
    handleMoveEnd();
    return () => { map.off('moveend', handleMoveEnd); };
  }, [map, onBoundsChange]);
  return null;
}

// Phase 7.35: emits the current map zoom so the parent can decide whether to
// render position-uncertainty halos (we only show them at zoom >= 14 to avoid
// drawing thousands of circles when zoomed out).
function MapZoomTracker({ onZoomChange }: { onZoomChange: (zoom: number) => void }) {
  const map = useMap();
  useEffect(() => { onZoomChange(map.getZoom()); }, [map, onZoomChange]);
  useMapEvents({
    zoomend() { onZoomChange(map.getZoom()); },
  });
  return null;
}

function MapCursorTracker({
  onCursorChange,
  onLeave,
}: {
  onCursorChange: (cursor: { lat: number; lon: number }) => void;
  onLeave?: () => void;
}) {
  useMapEvents({
    mousemove(event) {
      onCursorChange({ lat: event.latlng.lat, lon: event.latlng.lng });
    },
    mouseout() {
      onLeave?.();
    },
  });
  return null;
}

/** Captures map clicks while a pick slot is active and forwards the
 * resolved point to the AnalyticsToolsPanel via `onPicked`. */
function AnalyticsPickHandler({
  pickFor,
  onPicked,
}: {
  pickFor: AnalyticsPick | null;
  onPicked: (lat: number, lon: number, pickFor: AnalyticsPick) => void;
}) {
  const map = useMap();
  useEffect(() => {
    if (!pickFor) return;
    const container = map.getContainer();
    const prev = container.style.cursor;
    container.style.cursor = 'crosshair';
    return () => { container.style.cursor = prev; };
  }, [pickFor, map]);
  useMapEvents({
    click(event) {
      if (!pickFor) return;
      onPicked(event.latlng.lat, event.latlng.lng, pickFor);
    },
  });
  return null;
}

/**
 * Drag-to-draw a rectangle on the map and emit it as a Leaflet LatLngBounds.
 * Active only while `enabled` is true; disables map drag while active so the
 * user can box-select without panning, then re-enables it on completion or
 * when the mode is turned off.
 */
function DrawRectHandler({
  enabled,
  onFinish,
}: {
  enabled: boolean;
  onFinish: (bounds: L.LatLngBounds) => void;
}) {
  const map = useMap();
  const [draftStart, setDraftStart] = useState<L.LatLng | null>(null);
  const [draftEnd, setDraftEnd] = useState<L.LatLng | null>(null);

  useEffect(() => {
    if (!enabled) return;
    map.dragging.disable();
    map.boxZoom.disable();
    const container = map.getContainer();
    container.style.cursor = 'crosshair';
    return () => {
      map.dragging.enable();
      map.boxZoom.enable();
      container.style.cursor = '';
    };
  }, [enabled, map]);

  useMapEvents({
    mousedown(event) {
      if (!enabled) return;
      setDraftStart(event.latlng);
      setDraftEnd(event.latlng);
    },
    mousemove(event) {
      if (!enabled || !draftStart) return;
      setDraftEnd(event.latlng);
    },
    mouseup() {
      if (!enabled || !draftStart || !draftEnd) {
        setDraftStart(null);
        setDraftEnd(null);
        return;
      }
      const bounds = L.latLngBounds(draftStart, draftEnd);
      setDraftStart(null);
      setDraftEnd(null);
      // Reject zero-size rectangles (single click).
      const minPx = 6;
      const swPt = map.latLngToContainerPoint(bounds.getSouthWest());
      const nePt = map.latLngToContainerPoint(bounds.getNorthEast());
      if (Math.abs(swPt.x - nePt.x) < minPx || Math.abs(swPt.y - nePt.y) < minPx) return;
      onFinish(bounds);
    },
  });

  if (!enabled || !draftStart || !draftEnd) return null;
  return <Rectangle bounds={L.latLngBounds(draftStart, draftEnd)} pathOptions={{ color: '#ff7a1a', weight: 2, dashArray: '6 4', fillOpacity: 0.18 }} />;
}

function imageryBounds(imagery: any): L.LatLngBounds | null {
  if (!imagery?.footprint_geojson) return null;
  try {
    const geometry = typeof imagery.footprint_geojson === 'string'
      ? JSON.parse(imagery.footprint_geojson)
      : imagery.footprint_geojson;
    const bounds = L.geoJSON(geometry).getBounds();
    return bounds.isValid() ? bounds : null;
  } catch {
    return null;
  }
}

function MapFitToImagery({ imagery }: { imagery: any }) {
  const map = useMap();
  useEffect(() => {
    const bounds = imageryBounds(imagery);
    if (bounds) {
      map.fitBounds(bounds.pad(0.15), { animate: true, maxZoom: 13 });
    }
  }, [map, imagery?.id]);
  return null;
}

function MapFitToDetections({ geojson, filterKey }: { geojson: any; filterKey: string | null }) {
  const map = useMap();
  const [lastFittedKey, setLastFittedKey] = useState<string | null>(null);

  useEffect(() => {
    if (!filterKey) {
      setLastFittedKey(null);
      return;
    }
    if (filterKey === lastFittedKey) return;
    if (!geojson?.features?.length) return;

    try {
      const bounds = geojsonFeatureBounds(geojson);
      if (bounds?.isValid()) {
        map.fitBounds(bounds.pad(0.25), { animate: true, maxZoom: 15 });
        setLastFittedKey(filterKey);
      }
    } catch {
      // Ignore invalid geometries; the GeoJSON layer itself will skip what Leaflet cannot draw.
    }
  }, [filterKey, geojson, map, lastFittedKey]);
  return null;
}

type GaiaMapProps = {
  onOpenGraph?: () => void;
  /** Switch to FMV with the given clip selected (Detection's fmv_clip_id). */
  onOpenFmv?: (clipId: number) => void;
  /** Bubble cursor lat/lon up to the global status bar. */
  onCursorChange?: (cursor: { lat: number; lon: number } | null) => void;
  /** Cross-workspace navigation: focus a specific detection on mount. */
  crossNav?: {
    workspace: 'ingest' | 'map' | 'fmv' | 'graph' | 'admin';
    detectionId?: number;
    className?: string;
  } | null;
  consumeCrossNav?: () => void;
};

export default function GaiaMap({
  onOpenGraph,
  onOpenFmv,
  onCursorChange,
  crossNav,
  consumeCrossNav,
}: GaiaMapProps) {
  // Map view no longer triggers an imagery-profile load on mount: that would
  // race the FMV tab and force a container restart (which loses any
  // in-flight FMV tracking). The /detect endpoint already calls
  // _ensure_profile("imagery") internally, so the load happens lazily on
  // the first satellite detection request instead.
  const [data, setData] = useState<{ static: any[]; tracks: any[] }>({ static: [], tracks: [] });
  const [imagery, setImagery] = useState<any[]>([]);
  const [detectionsGeoJSON, setDetectionsGeoJSON] = useState<any>({ type: 'FeatureCollection', features: [] });
  const [detectionClasses, setDetectionClasses] = useState<any[]>([]);
  const [basemapGeoJSON, setBasemapGeoJSON] = useState<any>({ type: 'FeatureCollection', features: [] });
  const [selectedImagery, setSelectedImagery] = useState<number | null>(null);
  const [activeBaseLayer, setActiveBaseLayer] = useState<'base' | 'sat' | 'terrain'>('base');
  const [layerOpacities, setLayerOpacities] = useState<{ base: number; sat: number; terrain: number }>({ base: 1, sat: 0.8, terrain: 1 });
  // Phase 7.29: persist hidden-category state across sessions so the analyst's
  // earlier filter survives a reload, AND show a banner on the next load so a
  // category hidden last week doesn't quietly stay hidden forever.
  const HIDDEN_CATEGORIES_LSK = 'sentinel.geoMap.hiddenDetectionCategories.v1';
  const HIDDEN_LABELS_LSK = 'sentinel.geoMap.hiddenDetectionLabels.v1';
  const [hiddenDetectionLabels, setHiddenDetectionLabels] = useState<string[]>(() => {
    try {
      const raw = typeof window !== 'undefined' ? window.localStorage.getItem(HIDDEN_LABELS_LSK) : null;
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.filter((v) => typeof v === 'string') : [];
    } catch { return []; }
  });
  const [hiddenDetectionCategories, setHiddenDetectionCategories] = useState<DetectionCategoryId[]>(() => {
    try {
      const raw = typeof window !== 'undefined' ? window.localStorage.getItem(HIDDEN_CATEGORIES_LSK) : null;
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? (parsed.filter((v) => typeof v === 'string') as DetectionCategoryId[]) : [];
    } catch { return []; }
  });
  // Show a one-shot banner during this session if the previous session left
  // categories or labels hidden. Dismissed by clicking either chip or the X.
  const [restoredHiddenNotice, setRestoredHiddenNotice] = useState<{
    categories: DetectionCategoryId[];
    labels: string[];
  } | null>(() => {
    try {
      if (typeof window === 'undefined') return null;
      const rawC = window.localStorage.getItem(HIDDEN_CATEGORIES_LSK);
      const rawL = window.localStorage.getItem(HIDDEN_LABELS_LSK);
      const cats = rawC ? JSON.parse(rawC) : [];
      const labs = rawL ? JSON.parse(rawL) : [];
      const catList = Array.isArray(cats) ? cats : [];
      const labList = Array.isArray(labs) ? labs : [];
      if (catList.length === 0 && labList.length === 0) return null;
      return { categories: catList as DetectionCategoryId[], labels: labList as string[] };
    } catch { return null; }
  });
  useEffect(() => {
    try {
      if (typeof window === 'undefined') return;
      window.localStorage.setItem(HIDDEN_CATEGORIES_LSK, JSON.stringify(hiddenDetectionCategories));
    } catch { /* ignore quota errors */ }
  }, [hiddenDetectionCategories]);
  useEffect(() => {
    try {
      if (typeof window === 'undefined') return;
      window.localStorage.setItem(HIDDEN_LABELS_LSK, JSON.stringify(hiddenDetectionLabels));
    } catch { /* ignore quota errors */ }
  }, [hiddenDetectionLabels]);
  const [detectionClassFilter, setDetectionClassFilter] = useState<string | null>(null);
  const [expandedDetectionGroups, setExpandedDetectionGroups] = useState<string[]>([]);
  const [detectionGroupMode, setDetectionGroupMode] = useState<'CAT' | 'SRC'>('CAT');
  const [detectionsLayerVersion, setDetectionsLayerVersion] = useState(0);
  const [detectionLabelSearch, setDetectionLabelSearch] = useState('');
  const [selectedDetection, setSelectedDetection] = useState<any | null>(null);
  const [detectionTracks, setDetectionTracks] = useState<DetectionTrack[]>([]);
  const [selectedDetectionTrack, setSelectedDetectionTrack] = useState<DetectionTrack | null>(null);
  const [showBbox, setShowBbox] = useState(true);
  const [timelineWindowMinutes, setTimelineWindowMinutes] = useState(60);
  const [timelinePlaying, setTimelinePlaying] = useState(false);
  const [cursor, setCursor] = useState({ lat: 25, lon: 55 });
  const [actionStatus, setActionStatus] = useState('');
  const [isActionBusy, setIsActionBusy] = useState(false);
  const [candidateLinks, setCandidateLinks] = useState<any[]>([]);
  // Manual box drawing & soft-delete
  const [drawMode, setDrawMode] = useState(false);
  const [drawError, setDrawError] = useState<string | null>(null);
  const { user } = useAuth();

  // Map+ enhancements
  const [bboxMode, setBboxMode] = useState<'hbb' | 'obb' | 'mask'>('mask');
  const [prithviOverlays, setPrithviOverlays] = useState<{ flood: boolean; burn: boolean; crops: boolean }>({
    flood: false,
    burn: false,
    crops: false,
  });
  const [prithviGeojson, setPrithviGeojson] = useState<Record<string, any>>({});
  const [selectionTab, setSelectionTab] = useState<'edit' | 'review'>('edit');
  const [tmRange, setTmRange] = useState<'24h' | '7d' | '30d'>('24h');
  const [tmValue, setTmValue] = useState(1);
  const [tmPlaying, setTmPlaying] = useState(false);
  const [confidenceThreshold, setConfidenceThreshold] = useState(0);
  const [timeRange, setTimeRange] = useState<{ start: string; end: string }>(() => {
    const now = new Date();
    const hourAgo = new Date(now.getTime() - 60 * 60 * 1000);
    return { start: hourAgo.toISOString(), end: now.toISOString() };
  });
  const [mapBounds, setMapBounds] = useState('');
  // Phase 7.35: track zoom so we only render position-uncertainty halos when
  // the analyst is zoomed in tight enough that the halo size is visually useful.
  const [mapZoom, setMapZoom] = useState(6);
  const [activeLayers, setActiveLayers] = useState({
    satellite: true,
    detections: true,
    tracks: true,
    detectionTracks: true,
    static: true,
    grid: true,
    viewshed: false,
    los: false,
    routes: false,
  });
  const [pendingPick, setPendingPick] = useState<AnalyticsPick | null>(null);
  const [lastMapClick, setLastMapClick] = useState<{ lat: number; lon: number; pickFor: AnalyticsPick | null } | null>(null);
  const [analyticsResults, setAnalyticsResults] = useState<Record<AnalyticsKind, AnalyticsResponse | null>>({
    viewshed: null,
    los: null,
    routes: null,
  });
  const [rightTab, setRightTab] = useState<'details' | 'analytics' | 'similar' | 'tracks'>('details');
  const [overlaysOpen, setOverlaysOpen] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  // Modern shell: each side panel can be collapsed to a 36 px floating handle so
  // the analyst can maximise the map canvas without losing context.
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [timelineOpen, setTimelineOpen] = useState(true);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const autoCollapsedRef = useRef(false);

  // Respect the workspace's own container, not the viewport: when this map is
  // mounted inside a narrow shell, begin with the side drawers collapsed so
  // the canvas remains useful. The analyst can reopen either drawer manually.
  useEffect(() => {
    const node = workspaceRef.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry || autoCollapsedRef.current) return;
      if (entry.contentRect.width < 640) {
        setLeftOpen(false);
        setRightOpen(false);
        autoCollapsedRef.current = true;
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const selectedImageryData = imagery.find((img) => img.id === selectedImagery);

  // Live ontology categories (sensor-agnostic — the map shows all detections
  // regardless of sensor). Order/colour/short come from the API and update
  // automatically when the backend bumps `version_id`.
  const { order: CATEGORY_ORDER, categories: DETECTION_CATEGORIES, branches: ONTOLOGY_BRANCHES_FLAT } = useDetectionCategories();

  const branchById = useMemo(() => {
    const map = new Map<string, OntologyBranch>();
    for (const branch of ONTOLOGY_BRANCHES_FLAT) map.set(branch.id, branch);
    return map;
  }, [ONTOLOGY_BRANCHES_FLAT]);

  const getDetectionStyle = useMemo(() => makeDetectionStyle(DETECTION_CATEGORIES), [DETECTION_CATEGORIES]);
  const detectionIcon = useMemo(() => makeDetectionIcon(DETECTION_CATEGORIES, branchById), [DETECTION_CATEGORIES, branchById]);
  const trackColor = useCallback(
    (category: string) => trackColorFor(category, DETECTION_CATEGORIES),
    [DETECTION_CATEGORIES],
  );

  const detectionLabelStats = useMemo<DetectionClassStat[]>(() => {
    const stats = new Map<string, DetectionClassStat>();
    const parentClassesWithSubclassDetails = new Set<string>();

    for (const feature of detectionsGeoJSON.features || []) {
      const rawClass = detectionLabel(feature);
      const parentClass = String(feature?.properties?.parent_class || feature?.properties?.metadata?.parent_class || rawClass);
      const storedClass = String(feature?.properties?.class || '');
      // Server now writes `branch_id` onto every feature; fall back to Other.
      const category = branchIdForFeature(feature);
      if (rawClass !== parentClass && rawClass !== storedClass) {
        parentClassesWithSubclassDetails.add(parentClass);
        if (storedClass) parentClassesWithSubclassDetails.add(storedClass);
      }
      const existing = stats.get(rawClass);
      stats.set(rawClass, {
        ...existing,
        rawClass,
        parentClass,
        label: rawClass === storedClass ? feature?.properties?.label || existing?.label || detectionClassLabel(rawClass) : existing?.label || detectionClassLabel(rawClass),
        count: Number(existing?.count || 0) + 1,
        maxConfidence: Math.max(Number(existing?.maxConfidence || 0), confidenceValue(feature)),
        color: categoryFor(category, DETECTION_CATEGORIES).color,
        ontology: existing?.ontology || feature?.properties?.ontology,
        threatLevel: existing?.threatLevel || feature?.properties?.threat_level,
        category,
        source: detectionClassSource(rawClass),
      });
    }

    for (const meta of detectionClasses) {
      const rawClass = String(meta.class || meta.label || 'Unknown');
      const parentClass = String(meta.parent_class || meta.ontology?.parent_class || rawClass);
      // /api/detections/classes also returns server-computed branch_id.
      const category: DetectionCategoryId = (meta?.branch_id ? String(meta.branch_id) : 'Other') as DetectionCategoryId;
      const existing = stats.get(rawClass);
      if (!existing && parentClassesWithSubclassDetails.has(rawClass)) continue;
      stats.set(rawClass, {
        ...existing,
        rawClass,
        parentClass,
        label: existing?.label || meta?.label || detectionClassLabel(rawClass),
        count: Number(existing?.count ?? meta?.count ?? 0),
        maxConfidence: Math.max(Number(existing?.maxConfidence || 0), Number(meta?.max_confidence || 0)),
        color: categoryFor(category, DETECTION_CATEGORIES).color,
        ontology: existing?.ontology || meta?.ontology,
        llmAdvisory: existing?.llmAdvisory ?? meta?.llm_advisory ?? null,
        threatLevel: existing?.threatLevel || meta?.threat_level,
        category,
        source: detectionClassSource(rawClass),
      });
    }

    return Array.from(stats.values()).filter((item) => item.count > 0).sort((a, b) => {
      const aIdx = CATEGORY_ORDER.indexOf(a.category);
      const bIdx = CATEGORY_ORDER.indexOf(b.category);
      const aSafe = aIdx === -1 ? CATEGORY_ORDER.length : aIdx;
      const bSafe = bIdx === -1 ? CATEGORY_ORDER.length : bIdx;
      return aSafe - bSafe || b.count - a.count || a.label.localeCompare(b.label);
    });
  }, [detectionsGeoJSON, detectionClasses, DETECTION_CATEGORIES, CATEGORY_ORDER]);

  const filteredDetectionsGeoJSON = useMemo(() => ({
    ...detectionsGeoJSON,
    features: (detectionsGeoJSON.features || []).filter((feature: any) => {
      const rawConf = feature?.properties?.confidence;
      const conf = (typeof rawConf === 'number' && Number.isFinite(rawConf)) ? rawConf : 1;
      if (conf < confidenceThreshold) return false;
      const labels = detectionClassKeys(feature);
      if (detectionClassFilter) {
        // SOLO mode: restrict to features whose own raw class matches.
        // Compare ONLY the leaf class (`original_class` / `class` / `label`),
        // not parent_class — otherwise a feature with parent_class="building"
        // and class="military_facility" gets erased by the auto-hide of
        // every other class (including "building") that SOLO injects into
        // hiddenDetectionLabels.
        const props = feature?.properties || {};
        const leafClasses = [
          props.original_class,
          props.metadata?.original_class,
          props.class,
          props.label,
        ].filter(Boolean).map((value) => String(value));
        return leafClasses.includes(detectionClassFilter);
      }
      if (hiddenDetectionCategories.includes(branchIdForFeature(feature))) return false;
      return !labels.some((label) => hiddenDetectionLabels.includes(label));
    }),
  }), [detectionsGeoJSON, detectionClassFilter, hiddenDetectionCategories, hiddenDetectionLabels, confidenceThreshold]);

  // Suppression breakdown: count how many detections were dropped by *each*
  // independent filter, so the analyst can see what the pipeline + UI are
  // hiding (rather than the silent zero-feedback default).
  // Counts are per-filter (not stacked) so the analyst can attribute drops
  // back to a single control.
  const suppressionCounts = useMemo(() => {
    const all = detectionsGeoJSON.features || [];
    let byConfidence = 0;
    let byCategory = 0;
    let byLabel = 0;
    // Phase 3.13: detect whether any of the visible passes were sub-sampled
    // by the chip planner. We surface this so the analyst knows the AOI
    // wasn't 100% covered, which materially affects "we didn't find any X"
    // statements.
    let sampledPasses = 0;
    let worstCoverage = 1.0;
    let sampledPassesSeen = new Set<number>();
    for (const feature of all) {
      const rawConf = feature?.properties?.confidence;
      const conf = (typeof rawConf === 'number' && Number.isFinite(rawConf)) ? rawConf : 1;
      if (confidenceThreshold > 0 && conf < confidenceThreshold) byConfidence += 1;
      if (hiddenDetectionCategories.length > 0
        && hiddenDetectionCategories.includes(branchIdForFeature(feature))) byCategory += 1;
      if (hiddenDetectionLabels.length > 0) {
        const labels = detectionClassKeys(feature);
        if (labels.some((label) => hiddenDetectionLabels.includes(label))) byLabel += 1;
      }
      const passId = Number(feature?.properties?.pass_id);
      const wasSampled = Boolean(feature?.properties?.sampling_enabled);
      const coverage = Number(feature?.properties?.coverage_fraction);
      if (wasSampled && Number.isFinite(passId) && !sampledPassesSeen.has(passId)) {
        sampledPassesSeen.add(passId);
        sampledPasses += 1;
      }
      if (Number.isFinite(coverage) && coverage > 0 && coverage < worstCoverage) {
        worstCoverage = coverage;
      }
    }
    return {
      total: all.length,
      byConfidence,
      byCategory,
      byLabel,
      sampledPasses,
      worstCoverage,
    };
  }, [detectionsGeoJSON, confidenceThreshold, hiddenDetectionCategories, hiddenDetectionLabels]);

  // Map+ geometry mode — rewrite each feature's geometry into the requested
  // shape:
  //   hbb  → axis-aligned envelope (Polygon) from the original geometry
  //   obb  → polygon built from metadata.obb when present; falls back to mask
  //   mask → the raw geometry as ingested (default for SAM3 outputs)
  const geomDisplayedDetectionsGeoJSON = useMemo(() => {
    if (bboxMode === 'mask') return filteredDetectionsGeoJSON;
    const out = { ...filteredDetectionsGeoJSON, features: [] as any[] };
    for (const f of filteredDetectionsGeoJSON.features || []) {
      if (!f?.geometry) continue;
      if (bboxMode === 'hbb') {
        // Compute envelope by scanning all coordinates.
        const coords: number[][] = [];
        const walk = (c: any) => {
          if (Array.isArray(c) && c.length >= 2 && typeof c[0] === 'number' && typeof c[1] === 'number') {
            coords.push([c[0], c[1]]);
          } else if (Array.isArray(c)) {
            for (const i of c) walk(i);
          }
        };
        walk(f.geometry.coordinates);
        if (!coords.length) {
          out.features.push(f);
          continue;
        }
        let minLon = coords[0][0], maxLon = coords[0][0], minLat = coords[0][1], maxLat = coords[0][1];
        for (const [lon, lat] of coords) {
          if (lon < minLon) minLon = lon;
          if (lon > maxLon) maxLon = lon;
          if (lat < minLat) minLat = lat;
          if (lat > maxLat) maxLat = lat;
        }
        out.features.push({
          ...f,
          geometry: {
            type: 'Polygon',
            coordinates: [[
              [minLon, minLat],
              [maxLon, minLat],
              [maxLon, maxLat],
              [minLon, maxLat],
              [minLon, minLat],
            ]],
          },
        });
      } else {
        // OBB — if metadata.obb is a polygon of [lon,lat] pairs, use it; else
        // fall back to the mask polygon so the layer never disappears.
        const obb = f?.properties?.metadata?.obb;
        if (Array.isArray(obb) && obb.length >= 3 && Array.isArray(obb[0]) && obb[0].length >= 2) {
          const ring = obb.map((pt: any) => [Number(pt[0]), Number(pt[1])]);
          ring.push(ring[0]);
          out.features.push({
            ...f,
            geometry: { type: 'Polygon', coordinates: [ring] },
          });
        } else {
          out.features.push(f);
        }
      }
    }
    return out;
  }, [filteredDetectionsGeoJSON, bboxMode]);

  const filteredDetectionClassStats = useMemo(() => {
    const query = detectionLabelSearch.trim().toLowerCase();
    return query
      ? detectionLabelStats.filter((item) => `${item.label} ${item.rawClass} ${item.parentClass || ''} ${categoryFor(item.category, DETECTION_CATEGORIES).label} ${item.source} ${item.ontology?.category || ''} ${item.threatLevel || ''}`.toLowerCase().includes(query))
      : detectionLabelStats;
  }, [detectionLabelSearch, detectionLabelStats]);

  const detectionGroups = useMemo(() => {
    if (detectionGroupMode === 'SRC') {
      return SOURCE_ORDER.map((source) => {
        const classes = filteredDetectionClassStats.filter((item) => item.source === source);
        return {
          id: source,
          label: source,
          short: source.toUpperCase().slice(0, 4),
          color: classes.find((item) => item.count > 0)?.color || '#727a83',
          count: classes.reduce((sum, item) => sum + item.count, 0),
          classes,
        };
      }).filter((group) => group.classes.length > 0);
    }
    return CATEGORY_ORDER.map((category) => {
      const categoryMeta = categoryFor(category, DETECTION_CATEGORIES);
      const classes = filteredDetectionClassStats.filter((item) => item.category === category);
      return {
        id: category,
        label: categoryMeta.label,
        short: categoryMeta.short,
        color: categoryMeta.color,
        count: classes.reduce((sum, item) => sum + item.count, 0),
        classes,
      };
    }).filter((group) => group.classes.length > 0);
  }, [detectionGroupMode, filteredDetectionClassStats, CATEGORY_ORDER, DETECTION_CATEGORIES]);

  const maxDetectionLabelCount = Math.max(1, ...detectionLabelStats.map((item) => item.count));
  const visibleDetectionCount = filteredDetectionsGeoJSON.features?.length || 0;
  const showDetectionCenterMarkers = visibleDetectionCount > 0 && (
    visibleDetectionCount <= DETECTION_CENTER_MARKER_LIMIT || !showBbox
  );
  const detectionCanvasRenderer = useMemo(() => L.canvas({ padding: 0.5 }), []);
  const timelineBuckets = useMemo(() => {
    const buckets = new Array(60).fill(0);
    const now = Date.now();
    for (const feature of filteredDetectionsGeoJSON.features || []) {
      const imageTime = feature?.properties?.acquisition_time || feature?.properties?.imagery_metadata?.acquisition_time;
      const time = new Date(imageTime || feature?.properties?.created_at || now).getTime();
      const minsAgo = Math.floor((now - time) / 60000);
      if (minsAgo >= 0 && minsAgo < 60) buckets[59 - minsAgo] += 1;
    }
    return buckets;
  }, [filteredDetectionsGeoJSON]);
  const maxTimelineBucket = Math.max(1, ...timelineBuckets);

  const setRecentWindow = (minutes: number) => {
    const end = new Date();
    const start = new Date(end.getTime() - minutes * 60 * 1000);
    setTimelineWindowMinutes(minutes);
    setTimeRange({ start: start.toISOString(), end: end.toISOString() });
  };

  const showAllDetectionClasses = () => {
    setDetectionClassFilter(null);
    setHiddenDetectionCategories([]);
    setHiddenDetectionLabels([]);
  };

  const hideAllDetectionClasses = () => {
    setDetectionClassFilter(null);
    setHiddenDetectionCategories([...CATEGORY_ORDER]);
    setHiddenDetectionLabels([]);
  };

  const invertDetectionClasses = () => {
    setDetectionClassFilter(null);
    setHiddenDetectionCategories(CATEGORY_ORDER.filter((category) => !hiddenDetectionCategories.includes(category)));
    setHiddenDetectionLabels([]);
  };

  const toggleDetectionGroupExpanded = (groupId: string) => {
    setExpandedDetectionGroups((current) => (
      current.includes(groupId) ? current.filter((item) => item !== groupId) : [...current, groupId]
    ));
  };

  const toggleDetectionGroupVisibility = (group: { id: string; classes: DetectionClassStat[] }) => {
    setDetectionClassFilter(null);
    if (detectionGroupMode === 'CAT') {
      const category = group.id as DetectionCategoryId;
      setHiddenDetectionCategories((current) => (
        current.includes(category) ? current.filter((item) => item !== category) : [...current, category]
      ));
      return;
    }

    const groupClassKeys = group.classes.map((item) => item.rawClass);
    const allHidden = groupClassKeys.every((rawClass) => hiddenDetectionLabels.includes(rawClass));
    setHiddenDetectionLabels((current) => (
      allHidden
        ? current.filter((rawClass) => !groupClassKeys.includes(rawClass))
        : Array.from(new Set([...current, ...groupClassKeys]))
    ));
  };

  const soloDetectionClass = (rawClass: string) => {
    setDetectionClassFilter(rawClass);
    setHiddenDetectionCategories([]);
    setHiddenDetectionLabels(detectionLabelStats.filter((item) => item.rawClass !== rawClass).map((item) => item.rawClass));
  };

  const toggleDetectionClassVisibility = (rawClass: string) => {
    setDetectionClassFilter(null);
    setHiddenDetectionLabels((current) => (
      current.includes(rawClass) ? current.filter((item) => item !== rawClass) : [...current, rawClass]
    ));
  };

  const focusTimeRange = useCallback((timestamp?: string | null) => {
    if (!timestamp) return;
    const center = new Date(timestamp);
    if (!Number.isFinite(center.getTime())) return;
    const halfWindowMs = Math.max(15, timelineWindowMinutes) * 60 * 1000 / 2;
    setTimeRange({
      start: new Date(center.getTime() - halfWindowMs).toISOString(),
      end: new Date(center.getTime() + halfWindowMs).toISOString(),
    });
  }, [timelineWindowMinutes]);

  const fetchData = useCallback(async () => {
    try {
      const response = await axios.get(`${API_URL}/api/geotime/features`);
      setData(response.data || { static: [], tracks: [] });
    } catch (error) {
      console.error('Error fetching geotime data:', error);
    }
  }, []);

  const fetchDetectionTracks = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        status: 'confirmed,coast,pinned,tentative',
        start_time: timeRange.start,
        end_time: timeRange.end,
        limit: '200',
      });
      if (mapBounds) params.set('bbox', mapBounds);
      const response = await axios.get(`${API_URL}/api/tracks/detections?${params.toString()}`, { timeout: 10000 });
      setDetectionTracks(response.data?.tracks || []);
    } catch (error) {
      console.error('Error fetching detection tracks:', error);
    }
  }, [timeRange, mapBounds]);

  const pinTrack = useCallback(async (detectionId: number) => {
    setIsActionBusy(true);
    setActionStatus('Pinning track...');
    try {
      await axios.post(`${API_URL}/api/tracks/detections/pin`, { detection_id: detectionId }, { timeout: 10000 });
      setActionStatus('Track pinned.');
      fetchDetectionTracks();
    } catch (error) {
      console.error('Error pinning track:', error);
      setActionStatus('Track pin failed.');
    } finally {
      setIsActionBusy(false);
    }
  }, [fetchDetectionTracks]);


  const fetchImagery = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      params.append('start_time', timeRange.start);
      params.append('end_time', timeRange.end);
      const response = await axios.get(`${API_URL}/api/imagery?${params.toString()}`);
      let rows = response.data.imagery || [];
      let usedLatestFallback = false;
      if (rows.length === 0) {
        const latestResponse = await axios.get(`${API_URL}/api/imagery`);
        rows = latestResponse.data.imagery || [];
        usedLatestFallback = rows.length > 0;
      }
      setImagery(rows);
      const selectedRow = rows.find((row: any) => row.id === selectedImagery) || rows[0] || null;
      setSelectedImagery((current) => (current && rows.some((row: any) => row.id === current) ? current : rows[0]?.id || null));
      if (usedLatestFallback && selectedRow?.acquisition_time && !timestampInRange(selectedRow.acquisition_time, timeRange)) {
        focusTimeRange(selectedRow.acquisition_time);
      }
    } catch (error) {
      console.error('Error fetching imagery:', error);
    }
  }, [focusTimeRange, selectedImagery, timeRange]);

  const fetchDetectionClasses = useCallback(async () => {
    // The class legend shows every class present in the timeframe globally —
    // bbox is intentionally NOT applied so the panel stays useful even when
    // the map viewport doesn't yet cover newly-uploaded imagery. Map-rendered
    // features are still bbox-filtered separately by fetchDetectionFeatures().
    try {
      const classParams = new URLSearchParams({
        start_time: timeRange.start,
        end_time: timeRange.end,
        llm: 'true',
      });
      const response = await axios.get(`${API_URL}/api/detections/classes?${classParams.toString()}`, { timeout: 10000 });
      setDetectionClasses(response.data?.classes || []);
    } catch (error) {
      console.error('Error fetching detection classes:', error);
    }
  }, [timeRange]);

  const fetchDetectionFeatures = useCallback(async () => {
    if (!mapBounds) {
      setDetectionsGeoJSON({ type: 'FeatureCollection', features: [] });
      setDetectionsLayerVersion((version) => version + 1);
      return;
    }
    setIsLoading(true);
    try {
      const geoParams = new URLSearchParams({
        start_time: timeRange.start,
        end_time: timeRange.end,
        bbox: mapBounds,
        limit: '20000',
      });
      if (detectionClassFilter) {
        geoParams.append('det_class', detectionClassFilter);
      }
      const response = await axios.get(`${API_URL}/api/detections/geojson?${geoParams.toString()}`, { timeout: 10000 });
      setDetectionsGeoJSON(response.data || { type: 'FeatureCollection', features: [] });
      setDetectionsLayerVersion((version) => version + 1);
    } catch (error) {
      console.error('Error fetching detections:', error);
    } finally {
      setIsLoading(false);
    }
  }, [detectionClassFilter, mapBounds, timeRange]);

  const fetchDetections = useCallback(async () => {
    await Promise.all([fetchDetectionClasses(), fetchDetectionFeatures()]);
  }, [fetchDetectionClasses, fetchDetectionFeatures]);

  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => { fetchImagery(); }, [fetchImagery]);
  useEffect(() => { fetchDetectionClasses(); }, [fetchDetectionClasses]);
  useEffect(() => { fetchDetectionFeatures(); }, [fetchDetectionFeatures]);
  useEffect(() => { fetchDetectionTracks(); }, [fetchDetectionTracks]);

  // Fetch Prithvi overlay GeoJSON for any toggled kind. Each kind is loaded
  // lazily and cached until the user toggles it off.
  useEffect(() => {
    let cancelled = false;
    const wanted: Array<'flood' | 'burn' | 'crops'> = ['flood', 'burn', 'crops'].filter(
      (k) => prithviOverlays[k as 'flood' | 'burn' | 'crops'],
    ) as any;
    (async () => {
      for (const kind of wanted) {
        if (prithviGeojson[kind]) continue;
        try {
          const params: any = { kind };
          if (mapBounds) params.bbox = mapBounds;
          const { data } = await axios.get(`${API_URL}/api/detections/prithvi-overlays`, { params });
          if (cancelled) return;
          setPrithviGeojson((cur) => ({ ...cur, [kind]: data }));
        } catch (err) {
          console.error('prithvi overlay load failed', kind, err);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [prithviOverlays, mapBounds, prithviGeojson]);

  useEffect(() => {
    const fetchBasemap = async () => {
      try {
        const response = await axios.get(`${API_URL}/api/basemap/countries`);
        setBasemapGeoJSON(response.data || { type: 'FeatureCollection', features: [] });
      } catch (error) {
        console.error('Error fetching offline basemap:', error);
      }
    };
    fetchBasemap();
  }, []);

  useEventStream('geotime', useCallback(() => { fetchData(); }, [fetchData]));
  useEventStream('detections', useCallback((message: any) => {
    focusTimeRange(message?.acquisition_time);
    fetchDetections();
    fetchDetectionTracks();
    fetchImagery();
  }, [focusTimeRange, fetchDetections, fetchDetectionTracks, fetchImagery]));
  useEventStream('imagery', useCallback((message: any) => {
    focusTimeRange(message?.acquisition_time);
    fetchImagery();
  }, [focusTimeRange, fetchImagery]));
  useEventStream('ops', useCallback((message: any) => {
    if (String(message?.type || '').startsWith('imagery_') || message?.type === 'upload_received') {
      focusTimeRange(message?.acquisition_time);
    }
  }, [focusTimeRange]));

  // Bubble cursor coords up to the global status bar.
  useEffect(() => {
    if (!onCursorChange) return;
    onCursorChange(cursor);
  }, [cursor, onCursorChange]);

  // Listen for global "jump to detection" events (Shell's Jump search).
  useEffect(() => {
    const handler = (evt: Event) => {
      const id = Number((evt as CustomEvent).detail?.id);
      if (!Number.isFinite(id)) return;
      const feat = detectionsGeoJSON?.features?.find(
        (f: any) => Number(f.properties?.id) === id,
      );
      if (feat) {
        setSelectedDetection(feat);
        setRightOpen(true);
        if (!pendingPick) setRightTab('details');
      }
    };
    window.addEventListener('sentinel:jump-to-detection', handler);
    return () => window.removeEventListener('sentinel:jump-to-detection', handler);
  }, [detectionsGeoJSON, pendingPick]);

  // Consume cross-workspace navigation: when the user clicks "Open on GEOINT"
  // from Ontology or FMV we land here with a detectionId or className. Select
  // the matching detection, fit the map to it, then notify the parent so the
  // intent is consumed only once.
  useEffect(() => {
    if (!crossNav) return;
    if (crossNav.detectionId) {
      const feat = detectionsGeoJSON?.features?.find(
        (f: any) => Number(f.properties?.id) === Number(crossNav.detectionId),
      );
      if (feat) {
        setSelectedDetection(feat);
        setRightOpen(true);
        if (!pendingPick) setRightTab('details');
      }
    }
    if (crossNav.className) {
      setDetectionClassFilter(crossNav.className);
    }
    consumeCrossNav?.();
  }, [crossNav, detectionsGeoJSON, consumeCrossNav, pendingPick]);
  useEffect(() => {
    if (!onCursorChange) return;
    return () => onCursorChange(null);
  }, [onCursorChange]);

  // Create a manual detection from a user-drawn rectangle. We turn the rect
  // into a GeoJSON polygon and POST to /api/detections/manual; the new row
  // streams back via fetchDetections() and shows up on the map immediately.
  const createManualDetection = useCallback(
    async (
      bounds: L.LatLngBounds,
      payload: { object_class?: string; designation?: string; threat?: string; affiliation?: string; notes?: string },
    ) => {
      const sw = bounds.getSouthWest();
      const ne = bounds.getNorthEast();
      const geometry = {
        type: 'Polygon',
        coordinates: [[
          [sw.lng, sw.lat],
          [ne.lng, sw.lat],
          [ne.lng, ne.lat],
          [sw.lng, ne.lat],
          [sw.lng, sw.lat],
        ]],
      };
      try {
        setIsActionBusy(true);
        setDrawError(null);
        const { data } = await axios.post(`${API_URL}/api/detections/manual`, {
          geometry,
          object_class: (payload.object_class || 'unknown').trim().toLowerCase() || 'unknown',
          designation: payload.designation,
          threat_level: payload.threat || 'medium',
          affiliation: payload.affiliation || 'unknown',
          notes: payload.notes,
        });
        setActionStatus(`Manual detection ${data?.id} created.`);
        await fetchDetections();
        // Pre-select the new detection so the right panel opens on it.
        setSelectedDetection({
          type: 'Feature',
          geometry: data?.geometry,
          properties: {
            id: data?.id,
            class: data?.class,
            confidence: data?.confidence,
            source: 'operator',
            threat_level: data?.threat_level,
            allegiance: data?.affiliation,
            metadata: data?.metadata,
          },
        });
        setRightOpen(true);
        if (!pendingPick) setRightTab('details');
        return data;
      } catch (err: any) {
        const detail = err?.response?.data?.detail || err?.message || 'manual detection failed';
        setDrawError(detail);
        setActionStatus(`Manual detection failed: ${detail}`);
        return null;
      } finally {
        setIsActionBusy(false);
      }
    },
    [fetchDetections, pendingPick],
  );

  const deleteDetection = useCallback(async (detectionId: number) => {
    setIsActionBusy(true);
    try {
      await axios.delete(`${API_URL}/api/detections/${detectionId}`);
      setActionStatus(`Detection ${detectionId} deleted.`);
      setSelectedDetection(null);
      await fetchDetections();
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || 'delete failed';
      setActionStatus(`Delete failed: ${detail}`);
    } finally {
      setIsActionBusy(false);
    }
  }, [fetchDetections]);

  const tagDetection = useCallback(async (detectionId: number, allegiance: string) => {
    setIsActionBusy(true);
    setActionStatus(`Tagging detection ${detectionId} as ${allegiance}...`);
    try {
      const response = await axios.patch(`${API_URL}/api/detections/${detectionId}/tag`, { allegiance }, { timeout: 10000 });
      const metadata = response.data?.metadata || {};
      setSelectedDetection((current: any) => {
        if (!current || current.properties?.id !== detectionId) return current;
        return {
          ...current,
          properties: {
            ...current.properties,
            allegiance,
            metadata,
            ontology: metadata.ontology || current.properties?.ontology,
            threat_level: metadata.threat_level || current.properties?.threat_level,
            threat_confidence: metadata.threat_confidence ?? current.properties?.threat_confidence,
            assessment_status: metadata.assessment_status || current.properties?.assessment_status,
            evidence: metadata.evidence || current.properties?.evidence,
          },
        };
      });
      await fetchDetections();
      setActionStatus(`Detection tagged ${allegiance}.`);
    } catch (error: any) {
      console.error('Detection tagging failed:', error);
      setActionStatus(error.response?.data?.detail || 'Detection tagging failed.');
    } finally {
      setIsActionBusy(false);
    }
  }, [fetchDetections]);

  const requireSelectedDetection = () => {
    const detectionId = selectedDetection?.properties?.id;
    if (!detectionId) {
      setActionStatus('Select a detection first.');
      return null;
    }
    return detectionId;
  };

  const fetchCandidateLinks = useCallback(async (detectionId: number) => {
    const response = await axios.get(`${API_URL}/api/detections/${detectionId}/candidate-links`, { timeout: 10000 });
    setCandidateLinks(response.data?.candidates || []);
    return response.data?.candidates || [];
  }, []);

  useEffect(() => {
    const detectionId = selectedDetection?.properties?.id;
    setActionStatus('');
    setCandidateLinks([]);
    if (!detectionId) return;
    fetchCandidateLinks(detectionId).catch((error) => console.error('Candidate link fetch failed:', error));
  }, [fetchCandidateLinks, selectedDetection?.properties?.id]);

  const createCandidateLinks = useCallback(async () => {
    const detectionId = requireSelectedDetection();
    if (!detectionId) return null;
    const response = await axios.post(`${API_URL}/api/detections/${detectionId}/candidate-links`, null, { timeout: 12000 });
    const candidates = response.data?.candidates || [];
    setCandidateLinks(candidates);
    return candidates;
  }, [selectedDetection]);

  const cueCollection = useCallback(async () => {
    setIsActionBusy(true);
    setActionStatus('Checking approved target association...');
    try {
      const approved = candidateLinks.find((candidate) => candidate.status === 'approved');
      if (!approved) {
        const candidates = candidateLinks.length ? candidateLinks : await createCandidateLinks();
        setActionStatus(candidates?.length ? 'Review and approve a candidate link before cueing collection.' : 'No candidate target found for this detection.');
        return;
      }
      setActionStatus('Creating collection task...');
      const props = selectedDetection?.properties || {};
      const threat = String(props.threat_level || '').toLowerCase();
      await axios.post(`${API_URL}/api/collection/tasks`, {
        target_id: approved.target_id,
        target_name: approved.target_name,
        asset_type: 'ISR',
        priority: threat === 'critical' || threat === 'high' ? 'High' : 'Medium',
        queue: threat === 'critical' || threat === 'high' ? 'ATD Queue' : 'GEOINT Queue',
        notes: `Cue collection from GEO detection ${props.id || ''} (${props.label || props.class || 'unknown'}).`,
        aipoints: [],
      }, { timeout: 12000 });
      setActionStatus(`Collection queued for ${approved.target_name || approved.target_id}.`);
    } catch (error) {
      console.error('Cue collection failed:', error);
      setActionStatus('Cue collection failed.');
    } finally {
      setIsActionBusy(false);
    }
  }, [candidateLinks, createCandidateLinks, selectedDetection]);

  const addToLinkGraph = useCallback(async () => {
    setIsActionBusy(true);
    setActionStatus('Generating candidate graph links...');
    try {
      const candidates = await createCandidateLinks();
      setActionStatus(candidates?.length ? 'Candidate links ready for analyst approval.' : 'No candidate target found for this detection.');
    } catch (error) {
      console.error('Add to link graph failed:', error);
      setActionStatus('Candidate link generation failed.');
    } finally {
      setIsActionBusy(false);
    }
  }, [createCandidateLinks]);

  const approveCandidate = useCallback(async (candidateId: number) => {
    setIsActionBusy(true);
    setActionStatus('Approving candidate link...');
    try {
      await axios.post(`${API_URL}/api/detection-target-candidates/${candidateId}/approve`, { analyst: 'ui' }, { timeout: 12000 });
      const detectionId = selectedDetection?.properties?.id;
      if (detectionId) await fetchCandidateLinks(detectionId);
      setActionStatus('Candidate approved and graph link created.');
      onOpenGraph?.();
    } catch (error) {
      console.error('Candidate approval failed:', error);
      setActionStatus('Candidate approval failed.');
    } finally {
      setIsActionBusy(false);
    }
  }, [fetchCandidateLinks, onOpenGraph, selectedDetection]);

  const rejectCandidate = useCallback(async (candidateId: number) => {
    setIsActionBusy(true);
    setActionStatus('Rejecting candidate link...');
    try {
      await axios.post(`${API_URL}/api/detection-target-candidates/${candidateId}/reject`, { analyst: 'ui' }, { timeout: 12000 });
      const detectionId = selectedDetection?.properties?.id;
      if (detectionId) await fetchCandidateLinks(detectionId);
      setActionStatus('Candidate rejected.');
    } catch (error) {
      console.error('Candidate rejection failed:', error);
      setActionStatus('Candidate rejection failed.');
    } finally {
      setIsActionBusy(false);
    }
  }, [fetchCandidateLinks, selectedDetection]);

  const onEachDetection = (feature: any, layer: L.Layer) => {
    const props = feature.properties;
    const category = detectionCategoryForFeature(feature);
    const categoryMeta = categoryFor(category, DETECTION_CATEGORIES);
    const reviewStatus = props.review_status || props.metadata?.review_status || 'review_candidate';
    const originalClass = props.original_class || props.metadata?.original_class || props.class;
    const parentClass = props.parent_class || props.metadata?.parent_class || props.class;
    const hoverLabel = originalClass && originalClass !== props.class ? detectionClassLabel(originalClass) : props.label || props.class || 'Detection';
    const rawClassStr = String(originalClass || props.class || '');
    const source = rawClassStr.startsWith('prithvi:') ? 'Prithvi-EO'
      : 'SAM 3 / Specialist';
    layer.bindPopup(`
      <div style="font-family: sans-serif; min-width: 210px;">
        <div style="font-weight: 700; font-size: 13px; margin-bottom: 8px; color: #e8ebee; border-bottom: 1px solid #373e46; padding-bottom: 4px;">
          ${props.label || props.class}
        </div>
        <div style="font-size: 12px; color: #aab2bb; line-height: 1.6;">
          <div>ID: <span style="color:#e8ebee">${props.id}</span></div>
          <div>Category: <span style="color:${categoryMeta.color}">${categoryMeta.label}</span></div>
          <div>Source: <span style="color:#e8ebee">${source}</span></div>
          <div>Parent: <span style="color:#e8ebee">${parentClass}</span></div>
          <div>Original: <span style="color:#e8ebee">${originalClass}</span></div>
          <div>Confidence: <span style="color:#e8ebee">${(Number(props.confidence || 0) * 100).toFixed(1)}%</span></div>
          <div>Review: <span style="color:#e8ebee">${reviewStatus}</span></div>
          <div>Threat: <span style="color:#e8ebee">${props.threat_level || 'unknown'}</span></div>
          <div>Tag: <span style="color:#e8ebee">${props.allegiance || 'unknown'}</span></div>
        </div>
      </div>
    `);
    layer.bindTooltip(`${categoryMeta.short} / ${hoverLabel}`, {
      direction: 'top',
      className: 'sentinel-detection-label',
      opacity: 0.95,
      sticky: true,
    });
    layer.on('click', () => setSelectedDetection(feature));
  };

  return (
    <div ref={workspaceRef} className="map-workspace" style={{ position: 'relative', height: '100%', width: '100%', background: 'var(--bg-0)', overflow: 'hidden' }}>
      {/* Full-bleed map column (rendered below, sandwiched between the floating
          left / right panels via z-index).  This is now the workspace canvas. */}
      {leftOpen ? (
      <section
        className="sentinel-panel map-float-panel map-left-panel"
        style={{
          position: 'absolute',
          left: 14,
          top: 14,
          bottom: 14,
          zIndex: 500,
          border: '1px solid var(--line)',
          borderRadius: 10,
          background: 'color-mix(in oklab, var(--bg-1) 94%, transparent)',
          backdropFilter: 'blur(8px)',
          boxShadow: '0 8px 30px rgba(0,0,0,.35)',
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
        <div className="sentinel-panel-header">
          <Layers className="h-4 w-4" />
          <span>Operating picture</span>
          <button type="button" onClick={fetchDetections} className="sentinel-icon-btn ml-auto h-6 w-6" title="Refresh">
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
          <button type="button" onClick={() => setLeftOpen(false)} className="sentinel-icon-btn h-6 w-6" title="Collapse panel">
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="sentinel-scroll">
          <div className="border-b border-sentinel-line p-2">
            <div className="grid grid-cols-3 border border-sentinel-line-2">
              {(['base', 'sat', 'terrain'] as const).map((key) => {
                const isActive = activeBaseLayer === key;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setActiveBaseLayer(key)}
                    className={`h-7 font-mono text-[10px] uppercase tracking-widest ${isActive ? 'bg-sentinel-panel-2 text-slate-100' : 'text-sentinel-muted'}`}
                  >
                    {key}
                  </button>
                );
              })}
            </div>
            <div className="mt-2 flex items-center gap-2">
              <span className="sentinel-label">OPACITY</span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={layerOpacities[activeBaseLayer]}
                onChange={(event) => {
                  const next = parseFloat(event.target.value);
                  setLayerOpacities((prev) => ({ ...prev, [activeBaseLayer]: next }));
                }}
                className="flex-1"
              />
              <span className="font-mono text-[10px] text-sentinel-muted w-8 text-right">
                {Math.round(layerOpacities[activeBaseLayer] * 100)}%
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2 border-b border-sentinel-line bg-sentinel-panel-2 px-3 py-2">
            <button
              type="button"
              onClick={() => setOverlaysOpen((v) => !v)}
              className="text-sentinel-muted hover:text-slate-200"
              title={overlaysOpen ? 'Collapse overlays' : 'Expand overlays'}
            >
              {overlaysOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            </button>
            <span className="sentinel-label flex-1">Overlays</span>
            <button type="button" onClick={() => setShowBbox((value) => !value)} className={`sentinel-btn h-6 ${showBbox ? 'primary' : ''}`}>
              BBOX
            </button>
          </div>

          {overlaysOpen && [
            { key: 'satellite', label: 'Satellite Imagery', metric: imagery.length, color: 'text-sentinel-info', available: true },
            { key: 'detections', label: 'AI Detections', metric: visibleDetectionCount, color: 'text-sentinel-accent', available: true },
            { key: 'tracks', label: 'Active Tracks', metric: data.tracks.length, color: 'text-sentinel-info', available: true },
            { key: 'static', label: 'Static Features', metric: data.static.length, color: 'text-sentinel-crit', available: true },
            { key: 'grid', label: 'Tactical Grid', metric: 'WGS84', color: 'text-sentinel-muted', available: true },
            {
              key: 'viewshed',
              label: 'Viewshed',
              metric: analyticsResults.viewshed?.result?.features?.length ?? 0,
              color: 'text-sentinel-accent',
              available: !!analyticsResults.viewshed,
            },
            {
              key: 'los',
              label: 'Line of Sight',
              metric: analyticsResults.los?.result?.features?.length ?? 0,
              color: 'text-sentinel-accent',
              available: !!analyticsResults.los,
            },
            {
              key: 'routes',
              label: 'Routes',
              metric: analyticsResults.routes?.result?.features?.length ?? 0,
              color: 'text-sentinel-accent',
              available: !!analyticsResults.routes,
            },
          ].map((layer) => {
            const active = activeLayers[layer.key as keyof typeof activeLayers];
            const disabled = layer.available === false;
            return (
              <button
                key={layer.key}
                type="button"
                disabled={disabled}
                onClick={() => setActiveLayers((prev) => ({ ...prev, [layer.key]: !active }))}
                className={`sentinel-row w-full grid-cols-[22px_1fr_auto] text-left ${disabled ? 'opacity-40 cursor-not-allowed' : ''}`}
                title={disabled ? 'Run the tool first to enable this layer' : ''}
              >
                <span className={active && !disabled ? layer.color : 'text-sentinel-muted'}>{active && !disabled ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}</span>
                <span className="truncate text-xs text-slate-200">{layer.label}</span>
                <span className="font-mono text-[10px] text-sentinel-muted">{layer.metric}</span>
              </button>
            );
          })}


          <div className="border-b border-sentinel-line bg-sentinel-panel-2 px-3 py-2">
            <div className="flex items-center gap-2">
              <span className="sentinel-label flex-1">Detection Classes / {visibleDetectionCount}</span>
              <div className="grid grid-cols-2 border border-sentinel-line-2">
                {(['CAT', 'SRC'] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setDetectionGroupMode(mode)}
                    className={`h-6 px-2 font-mono text-[10px] ${detectionGroupMode === mode ? 'bg-sentinel-panel text-slate-100' : 'text-sentinel-muted'}`}
                  >
                    {mode}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="sentinel-btn h-6"
                onClick={showAllDetectionClasses}
              >
                ALL
              </button>
              <button
                type="button"
                className="sentinel-btn h-6"
                onClick={hideAllDetectionClasses}
              >
                NONE
              </button>
              <button type="button" className="sentinel-btn h-6" onClick={invertDetectionClasses}>
                INV
              </button>
            </div>
            <div className="mt-2 flex h-8 items-center gap-2 border border-sentinel-line-2 bg-sentinel-bg px-2">
              <Search className="h-3.5 w-3.5 text-sentinel-muted" />
              <input
                value={detectionLabelSearch}
                onChange={(event) => setDetectionLabelSearch(event.target.value)}
                placeholder="search classes"
                className="min-w-0 flex-1 bg-transparent text-xs text-slate-200 outline-none placeholder:text-sentinel-muted"
              />
            </div>
          </div>

          {detectionGroups.length === 0 && (
            <div className="p-4 text-xs text-sentinel-muted">No detections in current view.</div>
          )}

          {detectionGroups.map((group) => {
            const expanded = expandedDetectionGroups.includes(group.id);
            const category = group.id as DetectionCategoryId;
            const groupHidden = detectionGroupMode === 'CAT'
              ? hiddenDetectionCategories.includes(category)
              : group.classes.every((item) => hiddenDetectionLabels.includes(item.rawClass));
            const groupColor = groupHidden ? 'var(--ink-2)' : group.color;
            return (
              <div key={group.id} className="border-b border-sentinel-line">
                <div className="grid grid-cols-[22px_22px_1fr_auto_auto] items-center gap-2 px-3 py-2">
                  <button type="button" className="text-sentinel-muted" onClick={() => toggleDetectionGroupExpanded(group.id)}>
                    {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                  </button>
                  <button type="button" style={{ color: groupColor }} onClick={() => toggleDetectionGroupVisibility(group)}>
                    {groupHidden ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                  </button>
                  <button
                    type="button"
                    className="min-w-0 text-left"
                    onClick={() => toggleDetectionGroupExpanded(group.id)}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      {detectionGroupMode === 'CAT' && <span style={{ color: groupColor }}><CategoryIcon category={category} branchById={branchById} /></span>}
                      <span className={`truncate text-xs ${groupHidden ? 'text-sentinel-muted' : 'text-slate-200'}`}>{group.label}</span>
                    </span>
                  </button>
                  <span className="font-mono text-[10px] text-sentinel-muted">{group.classes.length}</span>
                  <span className="font-mono text-[10px]" style={{ color: groupColor }}>{group.count}</span>
                </div>
                <div className="px-3 pb-2">
                  <div className="h-1.5 bg-sentinel-bg">
                    <div className="h-full" style={{ width: `${Math.max(3, (group.count / maxDetectionLabelCount) * 100)}%`, backgroundColor: group.color }} />
                  </div>
                </div>
                {expanded && (
                  <div className="border-t border-sentinel-line bg-sentinel-bg/70">
                    {group.classes.map((item) => {
                      const hidden = Boolean(detectionClassFilter && detectionClassFilter !== item.rawClass)
                        || hiddenDetectionCategories.includes(item.category)
                        || hiddenDetectionLabels.includes(item.rawClass);
                      const solo = detectionClassFilter === item.rawClass;
                      const advisory = item.llmAdvisory;
                      const advisoryLabel = advisory?.label && advisory.label !== item.label ? advisory.label : null;
                      const advisoryTitle = advisory
                        ? `AI suggestion (non-authoritative): ${advisory.label || ''}${advisory.description ? ` — ${advisory.description}` : ''}\nGenerated by ${advisory.generated_by || 'llm'}. Deterministic ontology remains the canonical class.`
                        : '';
                      return (
                        <div key={item.rawClass} className="grid grid-cols-[22px_18px_1fr_auto_auto] items-center gap-2 px-3 py-1.5">
                          <button type="button" style={{ color: hidden ? 'var(--ink-2)' : item.color }} onClick={() => toggleDetectionClassVisibility(item.rawClass)}>
                            {hidden ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                          </button>
                          <span style={{ color: hidden ? 'var(--ink-2)' : item.color }}>
                            <DetectionSubclassIcon label={item.rawClass} category={item.category} branchById={branchById} className="h-3 w-3" />
                          </span>
                          <button type="button" className="min-w-0 text-left" onClick={() => soloDetectionClass(item.rawClass)}>
                            <span className={`block truncate text-[11px] ${hidden ? 'text-sentinel-muted' : 'text-slate-200'}`}>
                              {item.label}{solo ? ' / SOLO' : ''}
                              {/* Phase 7.29: non-authoritative AI suggestion. The
                                  deterministic label above stays canonical; this
                                  pill is a separate hint the analyst can inspect
                                  on hover or use to inform manual relabelling. */}
                              {advisoryLabel && (
                                <span
                                  className="ml-1.5 inline-flex items-center rounded-sm border border-amber-500/60 px-1 py-[1px] font-mono text-[9px] uppercase tracking-wider text-amber-300"
                                  title={advisoryTitle}
                                >
                                  AI · {advisoryLabel}
                                </span>
                              )}
                            </span>
                          </button>
                          <span className={`sentinel-tag ${threatClass(item.threatLevel)}`}>{item.threatLevel || categoryFor(item.category, DETECTION_CATEGORIES).short}</span>
                          <span className="font-mono text-[10px]" style={{ color: hidden ? 'var(--ink-2)' : item.color }}>{item.count}</span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}

          {imagery.length > 0 && (
            <>
              <div className="sentinel-panel-header border-t border-sentinel-line">
                <Satellite className="h-4 w-4" />
                <span>Imagery</span>
              </div>
              {imagery.slice(0, 10).map((img) => (
                <button
                  key={img.id}
                  type="button"
                  onClick={() => {
                    const next = selectedImagery === img.id ? null : img.id;
                    setSelectedImagery(next);
                    if (next !== null) setActiveBaseLayer('sat');
                  }}
                  className={`sentinel-row w-full grid-cols-[1fr_auto] text-left ${selectedImagery === img.id ? 'selected' : ''}`}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-xs text-slate-200">{img.name}</span>
                    <span className="block truncate font-mono text-[10px] text-sentinel-muted">{img.sensor_type} / {img.cloud_cover ?? 0}% cloud</span>
                  </span>
                  <span className="sentinel-tag info">SAT</span>
                </button>
              ))}
            </>
          )}
        </div>
      </section>
      ) : (
        <button
          type="button"
          onClick={() => setLeftOpen(true)}
          title="Show operating picture"
          style={{
            position: 'absolute',
            left: 14,
            top: 14,
            width: 36,
            zIndex: 500,
            padding: '10px 0',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 8,
            background: 'color-mix(in oklab, var(--bg-1) 94%, transparent)',
            backdropFilter: 'blur(8px)',
            border: '1px solid var(--line)',
            borderRadius: 10,
            color: 'var(--ink-1)',
            cursor: 'pointer',
            boxShadow: '0 6px 18px rgba(0,0,0,.3)',
          }}
        >
          <Layers size={14} style={{ color: 'var(--accent)' }} />
          <span
            style={{
              writingMode: 'vertical-rl',
              transform: 'rotate(180deg)',
              fontSize: 10.5,
              letterSpacing: '.06em',
              color: 'var(--ink-1)',
            }}
          >
            Operating picture
          </span>
          <ChevronRight size={11} style={{ color: 'var(--ink-3)' }} />
        </button>
      )}

      <section
        className="relative flex min-h-0 min-w-0 flex-col bg-sentinel-bg"
        style={{ position: 'absolute', inset: 0 }}
      >
        <div className="relative min-h-0 flex-1">
          <MapContainer
            center={[25.0, 55.0]}
            zoom={6}
            style={{ height: '100%', width: '100%', background: '#122231' }}
            zoomControl={false}
          >
            <ZoomControl position="bottomright" />
            <MapBoundsUpdater onBoundsChange={setMapBounds} />
            <MapCursorTracker onCursorChange={setCursor} />
            <MapZoomTracker onZoomChange={setMapZoom} />
            <AnalyticsPickHandler
              pickFor={pendingPick}
              onPicked={(lat, lon, pickFor) => {
                setLastMapClick({ lat, lon, pickFor });
              }}
            />
            <DrawRectHandler
              enabled={drawMode}
              onFinish={async (bounds) => {
                const cls = window.prompt(
                  'Object class for this manual detection (e.g. tank, frigate, building):',
                  'unknown',
                )?.trim();
                if (cls === undefined) {
                  setDrawMode(false);
                  return;
                }
                await createManualDetection(bounds, { object_class: cls || 'unknown' });
                setDrawMode(false);
              }}
            />
            <MapFitToImagery imagery={selectedImageryData} />
            <MapFitToDetections geojson={filteredDetectionsGeoJSON} filterKey={detectionClassFilter} />

            {activeBaseLayer === 'base' && (
              <TileLayer
                key="base-carto"
                url={CARTO_BASEMAP_URL}
                subdomains="abcd"
                maxZoom={20}
                maxNativeZoom={10}
                opacity={layerOpacities.base}
                attribution="&copy; OpenStreetMap &copy; CARTO"
              />
            )}
            {activeBaseLayer === 'terrain' && (
              <TileLayer
                key="base-terrain"
                url={TERRAIN_BASEMAP_URL}
                maxZoom={20}
                maxNativeZoom={10}
                opacity={layerOpacities.terrain}
                attribution="&copy; OpenStreetMap &copy; OpenTopoMap (CC-BY-SA)"
              />
            )}

            <ImageOverlay url="/world_map.svg" bounds={[[-85, -180], [85, 180]]} opacity={0.32} />

            {/* Prithvi overlays — hatched fills coloured per kind */}
            {(['flood', 'burn', 'crops'] as const).map((kind) => {
              if (!prithviOverlays[kind]) return null;
              const data = prithviGeojson[kind];
              if (!data || !data.features || data.features.length === 0) return null;
              const color =
                kind === 'flood' ? '#4ea1ff'
                : kind === 'burn' ? '#c46a30'
                : '#3dd68c';
              return (
                <GeoJSON
                  key={`prithvi-${kind}`}
                  data={data as any}
                  style={() => ({
                    color,
                    weight: 1.2,
                    opacity: 0.85,
                    fillColor: color,
                    fillOpacity: 0.22,
                    dashArray: '4 3',
                  })}
                />
              );
            })}

            {activeLayers.grid && (
              <GeoJSON
                data={basemapGeoJSON}
                style={() => ({
                  color: '#c4e6ff',
                  weight: 1.75,
                  opacity: 1,
                  fillColor: '#33556e',
                  fillOpacity: 0.56,
                  dashArray: '4 3',
                })}
                onEachFeature={(feature, layer) => {
                  const props = feature?.properties || {};
                  const name = props.admin || props.name || props.iso_a3;
                  if (name) layer.bindTooltip(String(name), { sticky: true, direction: 'top', opacity: 0.92 });
                }}
              />
            )}

            {activeBaseLayer === 'sat' && activeLayers.satellite && selectedImageryData && (
              <TileLayer
                key={`sat-${selectedImageryData.id}`}
                url={`${TILE_PROXY_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}?url=${encodeURIComponent(selectedImageryData.file_path)}`}
                opacity={layerOpacities.sat}
                maxZoom={22}
              />
            )}

            {activeLayers.static && data.static.map((loc: any) => {
              const isLaunchPoint = loc.label === 'LaunchPoint';
              const radius = loc.properties.threatRadius || 0;
              return (
                <div key={loc.id}>
                  {isLaunchPoint && radius > 0 && (
                    <Circle
                      center={[loc.properties.latitude, loc.properties.longitude]}
                      radius={radius}
                      pathOptions={{ color: '#ff3b30', fillColor: '#ff3b30', fillOpacity: 0.08, weight: 1, dashArray: '5, 5' }}
                    />
                  )}
                  <Marker position={[loc.properties.latitude, loc.properties.longitude]} icon={isLaunchPoint ? redIcon : emeraldIcon}>
                    <Popup className="sentinel-popup">
                      <div className="border border-sentinel-line bg-sentinel-panel p-2 text-slate-200">
                        <div className="mb-2 border-b border-sentinel-line pb-1 text-xs font-bold uppercase tracking-wider">{loc.properties.name}</div>
                        <div className="font-mono text-[11px] text-sentinel-muted">
                          LAT {loc.properties.latitude.toFixed(4)}<br />
                          LON {loc.properties.longitude.toFixed(4)}
                        </div>
                      </div>
                    </Popup>
                  </Marker>
                </div>
              );
            })}

            {activeLayers.detections && showDetectionCenterMarkers && filteredDetectionsGeoJSON.features?.map((feature: any) => {
              const badgePosition = detectionBadgePosition(feature);
              if (!badgePosition) return null;
              const category = detectionCategoryForFeature(feature);
              const categoryMeta = categoryFor(category, DETECTION_CATEGORIES);
              const props = feature.properties || {};
              return (
                <Marker
                  key={`det-marker-${props.id || props.class}-${badgePosition[0]}-${badgePosition[1]}`}
                  position={badgePosition}
                  icon={detectionIcon(feature)}
                  eventHandlers={{ click: () => setSelectedDetection(feature) }}
                >
                  <Popup className="sentinel-popup">
                    <div className="border border-sentinel-line bg-sentinel-panel p-2 text-slate-200">
                      <div className="mb-2 flex items-center gap-2 border-b border-sentinel-line pb-1 text-xs font-bold uppercase tracking-wider">
                        <span style={{ color: categoryMeta.color }}><DetectionSubclassIcon iconKey={props.icon_key ?? null} label={props.original_class || props.class || props.label} category={category} branchById={branchById} /></span>
                        <span>{props.label || detectionClassLabel(props.class)}</span>
                      </div>
                      <div className="font-mono text-[11px] text-sentinel-muted">
                        CAT <span style={{ color: categoryMeta.color }}>{categoryMeta.label}</span><br />
                        PARENT {props.parent_class || props.class || 'unknown'}<br />
                        ORIG {props.original_class || props.metadata?.original_class || props.class || 'unknown'}<br />
                        CONF {Math.round(Number(props.confidence || 0) * 100)}%
                      </div>
                    </div>
                  </Popup>
                </Marker>
              );
            })}

            {activeLayers.detections && !showDetectionCenterMarkers && !showBbox && filteredDetectionsGeoJSON.features?.map((feature: any) => {
              const center = detectionCenter(feature);
              if (!center) return null;
              const category = detectionCategoryForFeature(feature);
              const categoryMeta = categoryFor(category, DETECTION_CATEGORIES);
              const props = feature.properties || {};
              return (
                <CircleMarker
                  key={`det-dot-${props.id || props.class}-${center[0]}-${center[1]}`}
                  center={center}
                  renderer={detectionCanvasRenderer}
                  radius={3}
                  pathOptions={{
                    color: categoryMeta.color,
                    fillColor: categoryMeta.color,
                    fillOpacity: 0.8,
                    weight: 1,
                  }}
                  eventHandlers={{ click: () => setSelectedDetection(feature) }}
                >
                  <Popup className="sentinel-popup">
                    <div className="border border-sentinel-line bg-sentinel-panel p-2 text-slate-200">
                      <div className="mb-2 flex items-center gap-2 border-b border-sentinel-line pb-1 text-xs font-bold uppercase tracking-wider">
                        <span style={{ color: categoryMeta.color }}><DetectionSubclassIcon iconKey={props.icon_key ?? null} label={props.original_class || props.class || props.label} category={category} branchById={branchById} /></span>
                        <span>{props.label || detectionClassLabel(props.class)}</span>
                      </div>
                      <div className="font-mono text-[11px] text-sentinel-muted">
                        CAT <span style={{ color: categoryMeta.color }}>{categoryMeta.label}</span><br />
                        PARENT {props.parent_class || props.class || 'unknown'}<br />
                        ORIG {props.original_class || props.metadata?.original_class || props.class || 'unknown'}<br />
                        CONF {Math.round(Number(props.confidence || 0) * 100)}%
                      </div>
                    </div>
                  </Popup>
                </CircleMarker>
              );
            })}

            {/* Phase 7.35: position-uncertainty halos. Render a faint circle at
                each detection's centroid with radius = position_uncertainty_m
                when zoomed in tight (z>=14). Skipped when there are too many
                visible features, since N circles at z>=14 would still cost. */}
            {activeLayers.detections
              && mapZoom >= 14
              && filteredDetectionsGeoJSON.features
              && filteredDetectionsGeoJSON.features.length > 0
              && filteredDetectionsGeoJSON.features.length <= 400
              && filteredDetectionsGeoJSON.features.map((feature: any) => {
                const center = detectionCenter(feature);
                const uncertainty = Number(feature?.properties?.position_uncertainty_m);
                if (!center || !Number.isFinite(uncertainty) || uncertainty <= 0) return null;
                const props = feature.properties || {};
                return (
                  <Circle
                    key={`uncert-${props.id}-${center[0]}-${center[1]}`}
                    center={center}
                    radius={uncertainty}
                    pathOptions={{
                      color: '#9ec8ff',
                      weight: 1,
                      opacity: 0.35,
                      fillColor: '#9ec8ff',
                      fillOpacity: 0.05,
                      dashArray: '3,3',
                    }}
                    interactive={false}
                  />
                );
              })}

            {activeLayers.detections && showBbox && geomDisplayedDetectionsGeoJSON.features?.length > 0 && (
              <CanvasGeoJSON
                key={`detections-${detectionsLayerVersion}-${detectionClassFilter || 'all'}-${hiddenDetectionCategories.join('|')}-${hiddenDetectionLabels.join('|')}-${geomDisplayedDetectionsGeoJSON.features.length}-${bboxMode}`}
                data={geomDisplayedDetectionsGeoJSON}
                renderer={detectionCanvasRenderer}
                pointToLayer={(feature: any, latlng: L.LatLng) => L.circleMarker(latlng, {
                  ...getDetectionStyle(feature),
                  radius: 4,
                  fillOpacity: 0.8,
                })}
                style={getDetectionStyle}
                onEachFeature={onEachDetection}
              />
            )}

            {activeLayers.tracks && data.tracks.map((track: any) => {
              const positions: [number, number][] = track.history.map((h: any) => [h.lat, h.lng]);
              const latest = track.latest;
              return (
                <div key={track.id}>
                  <Polyline positions={positions} pathOptions={{ color: '#4ea1ff', weight: 2, opacity: 0.55, dashArray: '4, 6' }} />
                  {latest && (
                    <Marker position={[latest.latitude, latest.longitude]} icon={blueIcon}>
                      <Popup className="sentinel-popup">
                        <div className="border border-sentinel-line bg-sentinel-panel p-2 text-slate-200">
                          <div className="mb-2 border-b border-sentinel-line pb-1 text-xs font-bold uppercase tracking-wider">{track.properties.callsign || track.asset_id}</div>
                          <div className="font-mono text-[11px] text-sentinel-muted">
                            TYPE {track.label}<br />
                            SPEED {track.properties.speed?.toFixed(1)} kts
                          </div>
                        </div>
                      </Popup>
                    </Marker>
                  )}
                </div>
              );
            })}

            {activeLayers.detectionTracks && detectionTracks
              .filter((track) => track.status !== 'lost' && track.history.length >= 2)
              .map((track) => {
                const color = trackColor(track.category);
                const dashArray = trackDashArray(track.status);
                const positions: [number, number][] = track.history.map((h) => [h.lat, h.lng]);
                const isSelected = selectedDetectionTrack?.track_uid === track.track_uid;
                return (
                  <div key={track.track_uid}>
                    {track.status === 'confirmed' && track.threat_level === 'critical' && (
                      <Polyline
                        positions={positions}
                        pathOptions={{ color, weight: 6, opacity: 0.18 }}
                      />
                    )}
                    {track.pinned && (
                      <Polyline
                        positions={positions}
                        pathOptions={{ color: '#ffffff', weight: isSelected ? 6 : 4, opacity: 0.25 }}
                      />
                    )}
                    <Polyline
                      positions={positions}
                      pathOptions={{
                        color,
                        weight: isSelected ? 3 : 2,
                        opacity: isSelected ? 1 : 0.75,
                        dashArray,
                      }}
                      eventHandlers={{ click: () => setSelectedDetectionTrack(track) }}
                    />
                    {track.history.map((h, i) => (
                      <CircleMarker
                        key={`${track.track_uid}-${i}`}
                        center={[h.lat, h.lng]}
                        radius={2}
                        pathOptions={{
                          color,
                          fillColor: color,
                          fillOpacity: 0.3 + 0.7 * (i / Math.max(1, track.history.length - 1)),
                          opacity: 0,
                          weight: 0,
                        }}
                      />
                    ))}
                    {track.latest && (
                      <Marker
                        position={[track.latest.lat, track.latest.lon]}
                        icon={createIcon(color)}
                        eventHandlers={{ click: () => setSelectedDetectionTrack(track) }}
                      >
                        <Popup className="sentinel-popup">
                          <div className="border border-sentinel-line bg-sentinel-panel p-2 text-slate-200">
                            <div className="mb-2 border-b border-sentinel-line pb-1 text-xs font-bold uppercase tracking-wider">
                              DT-{track.track_uid.slice(-6)} {track.pinned ? '· PINNED' : ''}
                            </div>
                            <div className="font-mono text-[11px] text-sentinel-muted">
                              CLASS {track.primary_class}<br />
                              STATUS {track.status.toUpperCase()}<br />
                              OBS {track.obs_count} · {relativeTime(track.last_seen)}
                            </div>
                          </div>
                        </Popup>
                      </Marker>
                    )}
                  </div>
                );
              })}

            {activeLayers.viewshed && analyticsResults.viewshed?.result && (
              <GeoJSON
                key={`viewshed-${analyticsResults.viewshed.job.id}`}
                data={analyticsResults.viewshed.result as any}
                style={() => ({
                  color: '#5ee0a0',
                  weight: 1.5,
                  opacity: 0.9,
                  fillColor: '#5ee0a0',
                  fillOpacity: 0.22,
                })}
                onEachFeature={(_feature, layer) => {
                  const mode = (analyticsResults.viewshed?.result as any)?.mode;
                  const tip = mode === 'dem'
                    ? `Viewshed · DEM · job ${analyticsResults.viewshed?.job.id}`
                    : `Viewshed · demo fixture · job ${analyticsResults.viewshed?.job.id}`;
                  layer.bindTooltip(tip, { sticky: true, opacity: 0.92 });
                }}
              />
            )}

            {activeLayers.los && analyticsResults.los?.result && (
              <GeoJSON
                key={`los-${analyticsResults.los.job.id}`}
                data={analyticsResults.los.result as any}
                style={(feature) => {
                  const visible = !!feature?.properties?.visible;
                  const role = feature?.properties?.role;
                  if (role === 'obstruction') {
                    return { color: '#ff5577', weight: 0, fillColor: '#ff5577', fillOpacity: 0.7 };
                  }
                  return {
                    color: visible ? '#5ee0a0' : '#ff5577',
                    weight: 3,
                    opacity: 0.95,
                    dashArray: visible ? undefined : '6 4',
                  };
                }}
                onEachFeature={(feature, layer) => {
                  const p = feature?.properties || {};
                  const tip = p.role === 'obstruction'
                    ? `Obstructions · ${p.count} pts`
                    : `LOS · ${p.visible ? 'visible' : 'blocked'} · clearance ${Number(p.clearance_m || 0).toFixed(1)} m`;
                  layer.bindTooltip(tip, { sticky: true, opacity: 0.92 });
                }}
              />
            )}

            {activeLayers.routes && analyticsResults.routes?.result && (
              <GeoJSON
                key={`routes-${analyticsResults.routes.job.id}`}
                data={analyticsResults.routes.result as any}
                style={(feature) => {
                  const palette = ['#5fc4ff', '#ffb14a', '#c87aff'];
                  const idx = Math.max(0, ((feature?.properties?.option || 1) - 1) % palette.length);
                  return {
                    color: palette[idx],
                    weight: 4,
                    opacity: 0.9,
                  };
                }}
                onEachFeature={(feature, layer) => {
                  const p = feature?.properties || {};
                  const km = (Number(p.length_m || 0) / 1000).toFixed(1);
                  const min = Number(p.duration_minutes || 0).toFixed(0);
                  layer.bindTooltip(
                    `Route ${p.option} · ${p.label || p.risk || p.strategy} · ${km} km · ${min} min`,
                    { sticky: true, opacity: 0.92 },
                  );
                }}
              />
            )}
          </MapContainer>

          <div className="pointer-events-none absolute inset-0">
            <div className="sentinel-grid" />
            <div className="absolute left-2 top-2 font-mono text-[10px] tracking-wider text-sentinel-muted">WGS84 / MERCATOR / LIVE COP</div>
            <div className="absolute right-2 top-2 font-mono text-[10px] tracking-wider text-sentinel-muted">AOR / CURRENT VIEW</div>
            <div className="absolute left-1/2 top-8 -translate-x-1/2 border border-sentinel-line-2 bg-sentinel-panel px-3 py-1 font-mono text-[11px]">
              <span className="text-sentinel-accent">{visibleDetectionCount}</span>
              <span className="text-sentinel-muted"> / {detectionsGeoJSON.features?.length || 0} detections / last {timelineWindowMinutes}m</span>
              {visibleDetectionCount > 0 && <span className="text-sentinel-muted"> / hover labels</span>}
            </div>
            <div className="absolute left-3 bottom-4 border border-sentinel-line-2 bg-sentinel-panel px-3 py-2 font-mono text-[11px]">
              <div className="sentinel-label">cursor</div>
              <div>LAT {cursor.lat.toFixed(3).padStart(8, ' ')} deg</div>
              <div>LON {cursor.lon.toFixed(3).padStart(8, ' ')} deg</div>
              <div className="mt-1 text-sentinel-muted">MGRS <span className="text-slate-200">AUTO</span></div>
            </div>
            <div className="absolute right-3 bottom-4 border border-sentinel-line-2 bg-sentinel-panel px-3 py-2 font-mono text-[11px]">
              <div className="sentinel-label">scale</div>
              <div className="flex items-center gap-2">
                <span className="h-px w-20 bg-slate-200" />
                <span>500 km</span>
              </div>
            </div>
            <div className="absolute right-3 top-10 flex flex-col border border-sentinel-line-2 bg-sentinel-panel">
              <button type="button" className="pointer-events-auto grid h-7 w-7 place-items-center border-b border-sentinel-line text-sentinel-muted"><Plus className="h-3.5 w-3.5" /></button>
              <button type="button" className="pointer-events-auto grid h-7 w-7 place-items-center border-b border-sentinel-line text-sentinel-muted"><Minus className="h-3.5 w-3.5" /></button>
              <button type="button" className="pointer-events-auto grid h-7 w-7 place-items-center text-sentinel-muted"><Crosshair className="h-3.5 w-3.5" /></button>
            </div>

            {/* Map+ top-center toolbar — geometry mode, Prithvi overlays, draw mode */}
            <div className="absolute left-1/2 top-3 z-[500] -translate-x-1/2 pointer-events-auto flex flex-col items-center gap-2">
              <div
                className="flex items-center gap-1 border border-sentinel-line-2 bg-sentinel-panel/95 px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-slate-300 rounded-full"
                role="group"
                aria-label="Detection geometry mode"
              >
                <span className="px-2 text-[10px] text-sentinel-muted">GEOM</span>
                {(['hbb', 'obb', 'mask'] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setBboxMode(k)}
                    title={
                      k === 'hbb'
                        ? 'Axis-aligned bounding box'
                        : k === 'obb'
                          ? 'Oriented bounding box (from SAM3 metadata)'
                          : 'Mask polygon (raw geometry)'
                    }
                    className={`px-3 py-1 rounded-full transition ${
                      bboxMode === k
                        ? 'bg-sentinel-accent text-slate-900 font-bold'
                        : 'text-slate-300 hover:text-white'
                    }`}
                  >
                    {k.toUpperCase()}
                  </button>
                ))}
                <span className="mx-1 h-4 w-px bg-sentinel-line-2" />
                <span className="px-1 text-[10px] text-sentinel-muted">PRITHVI</span>
                {(['flood', 'burn', 'crops'] as const).map((k) => {
                  const on = prithviOverlays[k];
                  return (
                    <button
                      key={k}
                      type="button"
                      onClick={() => setPrithviOverlays((cur) => ({ ...cur, [k]: !cur[k] }))}
                      title={`Toggle Prithvi ${k} overlay`}
                      className={`px-3 py-1 rounded-full transition ${
                        on
                          ? 'bg-sentinel-accent/20 text-sentinel-accent'
                          : 'text-slate-400 hover:text-white'
                      }`}
                    >
                      {k}
                    </button>
                  );
                })}
                <span className="mx-1 h-4 w-px bg-sentinel-line-2" />
                <button
                  type="button"
                  onClick={() => setActiveLayers((cur) => ({ ...cur, tracks: !cur.tracks }))}
                  title="Toggle asset tracks"
                  className={`px-3 py-1 rounded-full transition ${
                    activeLayers.tracks
                      ? 'bg-sentinel-accent/20 text-sentinel-accent'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  tracks
                </button>
              </div>

              <button
                type="button"
                onClick={() => setDrawMode((v) => !v)}
                title={drawMode ? 'Cancel drawing' : 'Draw a manual box over an object'}
                className={`flex items-center gap-2 border px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest transition ${
                  drawMode
                    ? 'border-sentinel-accent bg-sentinel-accent/15 text-sentinel-accent'
                    : 'border-sentinel-line-2 bg-sentinel-panel text-sentinel-text hover:border-sentinel-accent/60'
                }`}
              >
                <Crosshair className="h-3.5 w-3.5" />
                {drawMode ? 'Cancel draw' : 'Draw object'}
              </button>
              {drawError && (
                <div className="mt-1 border border-red-500 bg-red-500/10 px-2 py-1 font-mono text-[10px] text-red-300">
                  {drawError}
                </div>
              )}
            </div>

            {drawMode && (
              <div className="absolute left-1/2 top-16 z-[500] -translate-x-1/2 pointer-events-none border border-sentinel-accent bg-sentinel-panel/80 px-3 py-1.5 font-mono text-[10.5px] uppercase tracking-widest text-sentinel-accent">
                Drag on the map to box an object, then label it
              </div>
            )}
          </div>

          {isLoading && (
            <div className="absolute left-1/2 top-1/2 z-[500] -translate-x-1/2 -translate-y-1/2 border border-sentinel-line bg-sentinel-panel px-4 py-2 text-xs text-slate-300">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 animate-pulse rounded-full bg-sentinel-accent" />
                Loading detections
              </div>
            </div>
          )}

        </div>

      </section>

      {/* Floating event-timeline panel, anchored to the bottom and inset
          past the left/right floating panels when they're open so it
          always uses the maximum free width. */}
      {timelineOpen ? (
        <div
          className="map-timeline"
          style={{
            ['--map-timeline-start' as any]: leftOpen
              ? 'calc(min(20rem, calc(100cqi - 1.75rem)) + 1.75rem)'
              : '4rem',
            ['--map-timeline-end' as any]: rightOpen
              ? 'calc(min(21.25rem, calc(100cqi - 1.75rem)) + 1.75rem)'
              : '4rem',
            position: 'absolute',
            bottom: 14,
            zIndex: 500,
            background: 'color-mix(in oklab, var(--bg-1) 94%, transparent)',
            backdropFilter: 'blur(8px)',
            border: '1px solid var(--line)',
            borderRadius: 10,
            padding: '6px 38px 6px 14px',
            boxShadow: '0 6px 24px rgba(0,0,0,.3)',
            transition: 'left .18s ease, right .18s ease',
          }}
        >
          <button
            type="button"
            onClick={() => setTimelineOpen(false)}
            title="Collapse timeline"
            className="btn icon xs"
            style={{ position: 'absolute', top: 8, right: 8, borderRadius: 6 }}
          >
            <ChevronDown size={11} />
          </button>

          {/* Time-machine scrubber (imagery acquisition timeline) */}
          {imagery.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <TimeMachineBar
                passes={imagery.map((p: any) => ({
                  id: Number(p.id),
                  acquisition_time: p.acquisition_time,
                  sensor_type: p.sensor_type,
                  name: p.name,
                }))}
                range={tmRange}
                value={tmValue}
                playing={tmPlaying}
                onRangeChange={setTmRange}
                onValueChange={setTmValue}
                onTogglePlay={() => setTmPlaying((p) => !p)}
                onRecenter={() => setTmValue(1)}
                isoNow={new Date().toISOString()}
                confidence={confidenceThreshold}
                onConfidenceChange={setConfidenceThreshold}
              />
            </div>
          )}

          {/* Phase 7.29: one-shot reminder that the previous session left
              categories or labels hidden. Appears once per page load and
              disappears as soon as the analyst acts on it. */}
          {restoredHiddenNotice && (
            <div
              role="status"
              aria-label="Restored hidden filters"
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                alignItems: 'center',
                gap: 6,
                marginBottom: 4,
                padding: '4px 6px',
                border: '1px solid #d8a14a',
                borderRadius: 6,
                background: 'rgba(216, 161, 74, 0.10)',
              }}
            >
              <span className="label-mono" style={{ fontSize: 10, color: '#f0c279' }}>
                ⚠ Filters from your last session are still hiding:
              </span>
              {restoredHiddenNotice.categories.length > 0 && (
                <button
                  type="button"
                  onClick={() => { setHiddenDetectionCategories([]); setRestoredHiddenNotice(null); }}
                  style={{
                    fontSize: 10,
                    padding: '2px 6px',
                    border: '1px solid #d8a14a',
                    borderRadius: 999,
                    background: 'var(--bg-0)',
                    cursor: 'pointer',
                    color: '#f0c279',
                  }}
                >
                  Show {restoredHiddenNotice.categories.length} hidden categories ✓
                </button>
              )}
              {restoredHiddenNotice.labels.length > 0 && (
                <button
                  type="button"
                  onClick={() => { setHiddenDetectionLabels([]); setRestoredHiddenNotice(null); }}
                  style={{
                    fontSize: 10,
                    padding: '2px 6px',
                    border: '1px solid #d8a14a',
                    borderRadius: 999,
                    background: 'var(--bg-0)',
                    cursor: 'pointer',
                    color: '#f0c279',
                  }}
                >
                  Show {restoredHiddenNotice.labels.length} hidden labels ✓
                </button>
              )}
              <div style={{ flex: 1 }} />
              <button
                type="button"
                onClick={() => setRestoredHiddenNotice(null)}
                aria-label="Dismiss"
                style={{
                  fontSize: 12,
                  border: 'none',
                  background: 'transparent',
                  color: '#f0c279',
                  cursor: 'pointer',
                  padding: '0 4px',
                }}
              >
                ✕
              </button>
            </div>
          )}

          {/* Suppression transparency: surface what the pipeline + UI are
              currently hiding from the analyst, so silent filters can't mask
              true positives without a breadcrumb. Each chip is clickable to
              clear its filter; the marker-mode + time-window chips are
              advisory (no-op clicks). */}
          {(() => {
            const overflowMarkers = (showBbox || visibleDetectionCount > DETECTION_CENTER_MARKER_LIMIT)
              && visibleDetectionCount > DETECTION_CENTER_MARKER_LIMIT
              ? visibleDetectionCount - DETECTION_CENTER_MARKER_LIMIT
              : 0;
            const tw = (() => {
              const start = new Date(timeRange.start).getTime();
              const end = new Date(timeRange.end).getTime();
              if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
              const minutes = Math.round((end - start) / 60000);
              return minutes > 0 ? minutes : null;
            })();
            const showSamplingChip = suppressionCounts.sampledPasses > 0
              && suppressionCounts.worstCoverage < 1.0;
            const anyHidden = suppressionCounts.byConfidence > 0
              || suppressionCounts.byCategory > 0
              || suppressionCounts.byLabel > 0
              || overflowMarkers > 0
              || (tw !== null && tw <= 60)
              || showSamplingChip;
            if (!anyHidden) return null;
            const chipStyle: React.CSSProperties = {
              fontSize: 10,
              padding: '2px 6px',
              border: '1px solid var(--line)',
              borderRadius: 999,
              background: 'var(--bg-0)',
              cursor: 'pointer',
              fontFamily: 'var(--font-mono, monospace)',
            };
            return (
              <div
                role="status"
                aria-label="Hidden detection summary"
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  alignItems: 'center',
                  gap: 6,
                  marginBottom: 4,
                  padding: '4px 6px',
                  border: '1px solid var(--line)',
                  borderRadius: 6,
                  background: 'color-mix(in oklab, var(--bg-0) 88%, transparent)',
                }}
              >
                <span className="label-mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>
                  Showing {visibleDetectionCount}/{suppressionCounts.total} ·
                </span>
                {suppressionCounts.byConfidence > 0 && (
                  <button
                    type="button"
                    onClick={() => setConfidenceThreshold(0)}
                    title={`Click to reset confidence floor (currently ${confidenceThreshold.toFixed(2)})`}
                    style={chipStyle}
                  >
                    -{suppressionCounts.byConfidence} below conf {confidenceThreshold.toFixed(2)} ✕
                  </button>
                )}
                {suppressionCounts.byCategory > 0 && (
                  <button
                    type="button"
                    onClick={() => setHiddenDetectionCategories([])}
                    title="Click to show all hidden categories"
                    style={chipStyle}
                  >
                    -{suppressionCounts.byCategory} hidden by category ({hiddenDetectionCategories.length}) ✕
                  </button>
                )}
                {suppressionCounts.byLabel > 0 && (
                  <button
                    type="button"
                    onClick={() => setHiddenDetectionLabels([])}
                    title="Click to show all hidden labels"
                    style={chipStyle}
                  >
                    -{suppressionCounts.byLabel} hidden by label ({hiddenDetectionLabels.length}) ✕
                  </button>
                )}
                {overflowMarkers > 0 && (
                  <span
                    style={{ ...chipStyle, cursor: 'default' }}
                    title={`Above ${DETECTION_CENTER_MARKER_LIMIT} the map renders dots/bboxes instead of icon markers`}
                  >
                    +{overflowMarkers} rendered as dots (over {DETECTION_CENTER_MARKER_LIMIT})
                  </span>
                )}
                {tw !== null && tw <= 60 && (
                  <span
                    style={{ ...chipStyle, cursor: 'default' }}
                    title="Older detections are excluded by the time-window query; expand the timeline range to see more"
                  >
                    last {tw}m window — older detections excluded
                  </span>
                )}
                {showSamplingChip && (
                  <span
                    style={{
                      ...chipStyle,
                      cursor: 'default',
                      borderColor: '#d8a14a',
                      color: '#f0c279',
                    }}
                    title={`The chip planner sub-sampled ${suppressionCounts.sampledPasses} pass(es) — only ~${Math.round(suppressionCounts.worstCoverage * 100)}% of the raster was scanned for inference. "No detections" in unscanned regions does not mean "no targets". Re-ingest with INFERENCE_SPEED_PROFILE=recall_review or raise MAX_INFERENCE_CHIPS for full coverage.`}
                  >
                    ⚠ {suppressionCounts.sampledPasses} sub-sampled pass(es) · coverage {Math.round(suppressionCounts.worstCoverage * 100)}%
                  </span>
                )}
              </div>
            );
          })()}

          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
            <button
              type="button"
              className="btn icon sm"
              onClick={() => setTimelinePlaying((value) => !value)}
              title={timelinePlaying ? 'Pause timeline' : 'Play timeline'}
            >
              {timelinePlaying ? <Pause size={12} /> : <Play size={12} />}
            </button>
            <button
              type="button"
              className="btn icon sm"
              onClick={fetchDetections}
              title="Refresh detections"
            >
              <RefreshCw size={12} />
            </button>
            <span className="label-mono">Event timeline · last {timelineWindowMinutes}m</span>
            <div className="seg" style={{ marginLeft: 8 }}>
              {[15, 30, 60].map((minutes) => (
                <button
                  key={minutes}
                  type="button"
                  className={timelineWindowMinutes === minutes ? 'on' : ''}
                  onClick={() => setRecentWindow(minutes)}
                >
                  {minutes}M
                </button>
              ))}
            </div>
            <div style={{ flex: 1 }} />
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>
              {new Date(timeRange.start).toLocaleTimeString()} / {new Date(timeRange.end).toLocaleTimeString()}
            </span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
              · {visibleDetectionCount} in window
            </span>
          </div>
          <div
            style={{
              position: 'relative',
              display: 'flex',
              alignItems: 'flex-end',
              gap: 1,
              height: 28,
              border: '1px solid var(--line)',
              background: 'var(--bg-0)',
              padding: 2,
            }}
          >
            {timelineBuckets.map((value, index) => {
              const inWindow = index >= 60 - timelineWindowMinutes;
              return (
                <div
                  key={index}
                  style={{
                    flex: 1,
                    height: `${Math.max(4, (value / maxTimelineBucket) * 100)}%`,
                    background: inWindow ? 'var(--accent)' : 'var(--line-2)',
                    opacity: inWindow ? 0.45 + (value / maxTimelineBucket) * 0.55 : 0.35,
                  }}
                />
              );
            })}
            <div
              style={{
                position: 'absolute',
                top: 0,
                bottom: 0,
                width: 1,
                left: `${((60 - timelineWindowMinutes) / 60) * 100}%`,
                background: 'var(--accent)',
              }}
            />
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setTimelineOpen(true)}
          title="Show event timeline"
          style={{
            position: 'absolute',
            left: '50%',
            transform: 'translateX(-50%)',
            bottom: 14,
            zIndex: 500,
            padding: '6px 14px',
            background: 'color-mix(in oklab, var(--bg-1) 94%, transparent)',
            backdropFilter: 'blur(8px)',
            border: '1px solid var(--line)',
            borderRadius: 999,
            color: 'var(--ink-1)',
            fontSize: 11.5,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            boxShadow: '0 6px 18px rgba(0,0,0,.3)',
          }}
        >
          <Activity size={11} style={{ color: 'var(--accent)' }} />
          Event timeline
          <ChevronUp size={10} style={{ color: 'var(--ink-3)' }} />
        </button>
      )}

      {rightOpen ? (
      <section
        className="sentinel-panel map-float-panel map-right-panel"
        style={{
          position: 'absolute',
          right: 14,
          top: 14,
          bottom: 14,
          zIndex: 500,
          border: '1px solid var(--line)',
          borderRadius: 10,
          background: 'color-mix(in oklab, var(--bg-1) 94%, transparent)',
          backdropFilter: 'blur(8px)',
          boxShadow: '0 8px 30px rgba(0,0,0,.35)',
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
        {(() => {
          const rightHeader =
            rightTab === 'analytics' ? { Icon: Sparkles,    label: 'Analytics',     tag: 'ANALYTICS' } :
            rightTab === 'similar'   ? { Icon: Crosshair,   label: 'Similar',       tag: 'NEAREST'   } :
            rightTab === 'tracks'    ? { Icon: Navigation,  label: 'Active Tracks', tag: 'TRACKS'    } :
                                       { Icon: Crosshair,   label: selectedDetection ? `DET-${selectedDetection.properties?.id}` : 'Selection', tag: 'DETAIL' };
          const HeaderIcon = rightHeader.Icon;
          const allegianceLabel = String(selectedDetection?.properties?.allegiance || '').toLowerCase();
          const allegianceTagClass =
            allegianceLabel === 'hostile' ? 'crit' :
            allegianceLabel === 'friendly' ? 'ok' :
            allegianceLabel === 'neutral' ? 'info' :
            'acc';
          return (
            <div className="sentinel-panel-header">
              <HeaderIcon className="h-4 w-4" />
              <span>{rightHeader.label}</span>
              {rightTab === 'details' && selectedDetection ? (
                <span className={`sentinel-tag ${allegianceTagClass} ml-auto uppercase`}>{selectedDetection.properties?.allegiance || 'unknown'}</span>
              ) : (
                <span className="sentinel-tag acc ml-auto">{rightHeader.tag}</span>
              )}
              <button type="button" onClick={() => setRightOpen(false)} className="sentinel-icon-btn h-6 w-6" title="Collapse panel">
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        })()}
        <div className="flex border-b border-sentinel-line bg-sentinel-panel-2">
          {([
            ['details', 'Details'],
            ['analytics', 'Analytics'],
            ['similar', 'Similar'],
            ['tracks', 'Active Tracks'],
          ] as const).map(([k, label]) => {
            const isActive = rightTab === k;
            return (
              <button
                key={k}
                type="button"
                onClick={() => setRightTab(k)}
                className={`flex-1 h-[34px] font-mono text-[10.5px] uppercase tracking-[.08em] flex items-center justify-center gap-1.5 border-r border-sentinel-line last:border-r-0 ${
                  isActive ? 'bg-sentinel-panel text-slate-100' : 'text-sentinel-muted'
                }`}
                style={{ borderBottom: isActive ? '2px solid var(--accent, #ff7a1a)' : '2px solid transparent' }}
              >
                {label}
              </button>
            );
          })}
        </div>
        <div className="sentinel-scroll">
          {rightTab === 'details' && (selectedDetection ? (() => {
            const props = selectedDetection.properties || {};
            const category = detectionCategoryForFeature(selectedDetection);
            const categoryMeta = categoryFor(category, DETECTION_CATEGORIES);
            const confidencePct = Math.round(Number(props.confidence || 0) * 100);
            const centroid = featureCentroid(selectedDetection);
            const llBounds = featureLatLonBounds(selectedDetection);
            const mgrsString = centroid
              ? (() => { try { return mgrsForward([centroid[1], centroid[0]], 5); } catch { return null; } })()
              : null;
            const trackForDetection = detectionTracks.find((t) => {
              const ids = (t.metadata as any)?.detection_ids;
              return Array.isArray(ids) && ids.includes(Number(props.id));
            });
            const vx = trackForDetection?.last_velocity?.vx_mps;
            const vy = trackForDetection?.last_velocity?.vy_mps;
            const motion = (typeof vx === 'number' && typeof vy === 'number')
              ? (() => {
                  const speedMs = Math.sqrt(vx * vx + vy * vy);
                  const speedKmh = speedMs * 3.6;
                  let bearing = (Math.atan2(vx, vy) * 180) / Math.PI;
                  if (bearing < 0) bearing += 360;
                  return `${speedKmh.toFixed(1)} km/h · bearing ${String(Math.round(bearing)).padStart(3, '0')}°`;
                })()
              : null;
            const captureSource = selectedImageryData?.name
              ? `${selectedImageryData.name}${selectedImageryData.sensor_type ? ` / ${selectedImageryData.sensor_type}` : ''}`
              : props.metadata?.source_cog || 'n/a';
            const captureTime = props.metadata?.acquisition_time || selectedImageryData?.acquisition_time;
            const resolution = props.metadata?.resolution_m ?? selectedImageryData?.resolution_m;
            return (
              <>
                <div className="border-b border-sentinel-line p-3">
                  <div className="font-mono text-[10px] text-sentinel-muted">DET-{props.id} / {props.parent_class || props.class}</div>
                  <div className="mt-1 flex items-center gap-2">
                    <span style={{ color: categoryMeta.color }}><CategoryIcon category={category} branchById={branchById} /></span>
                    <div className="text-lg font-semibold uppercase tracking-wide text-slate-100">
                      {props.label || detectionClassLabel(props.class)}
                    </div>
                  </div>
                  <div className="mt-3 flex items-center gap-2">
                    <div className="h-1 flex-1 bg-sentinel-bg">
                      <div className="h-full" style={{ width: `${confidencePct}%`, background: 'var(--accent, #ff7a1a)' }} />
                    </div>
                    <span className="font-mono text-[10px] text-sentinel-muted">{confidencePct}% CONF</span>
                  </div>
                </div>

                <div className="border-b border-sentinel-line p-3">
                  <div className="flex items-center gap-2 pb-2">
                    <span className="inline-flex h-4 w-4 items-center justify-center bg-sentinel-line-2 font-mono text-[9px] text-slate-200">B</span>
                    <span className="sentinel-label">Capture</span>
                  </div>
                  <div className="grid grid-cols-[92px_1fr] gap-y-1 font-mono text-[10.5px]">
                    <span className="text-sentinel-muted">SOURCE</span><span className="truncate">{captureSource}</span>
                    <span className="text-sentinel-muted">CAPTURE</span><span className="truncate">{captureTime ? new Date(captureTime).toISOString().replace(/\.\d+/, '') : 'n/a'}</span>
                    <span className="text-sentinel-muted">RESOLUTION</span><span>{resolution ? `${Number(resolution).toFixed(2)} m / px` : 'n/a'}</span>
                    <span className="text-sentinel-muted">BBOX</span>
                    <span className="truncate">
                      {llBounds
                        ? `${llBounds.south.toFixed(4)},${llBounds.west.toFixed(4)} → ${llBounds.north.toFixed(4)},${llBounds.east.toFixed(4)}`
                        : 'n/a'}
                    </span>
                  </div>
                </div>

                <div className="border-b border-sentinel-line p-3">
                  <div className="flex items-center gap-2 pb-2">
                    <span className="inline-flex h-4 w-4 items-center justify-center bg-sentinel-line-2 font-mono text-[9px] text-slate-200">C</span>
                    <span className="sentinel-label">Geolocation</span>
                  </div>
                  <div className="grid grid-cols-[92px_1fr] gap-y-1 font-mono text-[10.5px]">
                    <span className="text-sentinel-muted">WGS84</span>
                    <span>{centroid ? `${centroid[0].toFixed(4)}° N, ${centroid[1].toFixed(4)}° E` : 'n/a'}</span>
                    <span className="text-sentinel-muted">MGRS</span><span>{mgrsString || 'n/a'}</span>
                    <span className="text-sentinel-muted">MOTION</span><span>{motion || 'static'}</span>
                  </div>
                </div>

                <div className="border-b border-sentinel-line p-3">
                  <div className="flex items-center gap-2 pb-2">
                    <span className="inline-flex h-4 w-4 items-center justify-center bg-sentinel-line-2 font-mono text-[9px] text-slate-200">D</span>
                    <span className="sentinel-label">Taxonomy</span>
                  </div>
                  <div className="grid grid-cols-[92px_1fr] gap-y-1 font-mono text-[10.5px]">
                    <span className="text-sentinel-muted">CLASS</span><span className="truncate">{props.class || 'n/a'}</span>
                    <span className="text-sentinel-muted">VERSION</span><span>{props.metadata?.taxonomy_version || 'n/a'}</span>
                    <span className="text-sentinel-muted">MODEL</span><span>{props.metadata?.model_version || 'n/a'}</span>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2 border-b border-sentinel-line p-3">
                  <button
                    type="button"
                    disabled={!props.fmv_clip_id || !onOpenFmv}
                    onClick={() => props.fmv_clip_id && onOpenFmv && onOpenFmv(Number(props.fmv_clip_id))}
                    className="sentinel-btn justify-center disabled:opacity-40"
                  >
                    OPEN IN FMV →
                  </button>
                  <button
                    type="button"
                    disabled={isActionBusy}
                    onClick={addToLinkGraph}
                    className="sentinel-btn justify-center disabled:opacity-40"
                  >
                    OPEN IN GRAPH →
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-2 border-b border-sentinel-line p-3">
                  <button type="button" disabled={isActionBusy} onClick={() => tagDetection(props.id, 'friendly')} className="sentinel-btn justify-center disabled:opacity-40"><Shield className="h-3.5 w-3.5" /> Friendly</button>
                  <button type="button" disabled={isActionBusy} onClick={() => tagDetection(props.id, 'hostile')} className="sentinel-btn justify-center disabled:opacity-40"><Swords className="h-3.5 w-3.5" /> Hostile</button>
                  <button type="button" disabled={isActionBusy} onClick={() => tagDetection(props.id, 'neutral')} className="sentinel-btn justify-center disabled:opacity-40"><CircleHelp className="h-3.5 w-3.5" /> Neutral</button>
                  <button type="button" disabled={isActionBusy} onClick={() => tagDetection(props.id, 'unknown')} className="sentinel-btn justify-center disabled:opacity-40">Clear</button>
                </div>

                <div className="flex border-b border-sentinel-line">
                  {(['edit', 'review'] as const).map((k) => (
                    <button
                      key={k}
                      type="button"
                      onClick={() => setSelectionTab(k)}
                      className={`flex-1 px-2 py-2 font-mono text-[10.5px] uppercase tracking-widest transition border-b-2 ${
                        selectionTab === k
                          ? 'border-sentinel-accent text-sentinel-accent bg-sentinel-panel-2'
                          : 'border-transparent text-sentinel-muted hover:text-slate-200'
                      }`}
                    >
                      {k}
                    </button>
                  ))}
                </div>

                {selectionTab === 'edit' && (
                  <ObjectDetailsForm
                    key={`map-det-${props.id}`}
                    source="map"
                    detectionId={Number(props.id)}
                    defaultClass={props.class}
                    title={props.label || detectionClassLabel(props.class)}
                    initial={{
                      designation: props.metadata?.designation,
                      military_classification: props.metadata?.military_classification,
                      threat_level: props.threat_level,
                      affiliation: props.allegiance,
                    }}
                    canDelete={
                      (props.source || props.metadata?.source) === 'operator'
                      || user?.role === 'admin'
                    }
                    onDeleted={() => deleteDetection(Number(props.id))}
                    onSaved={() => fetchDetections()}
                    onViewInFmv={
                      props.fmv_clip_id && onOpenFmv
                        ? () => onOpenFmv(Number(props.fmv_clip_id))
                        : undefined
                    }
                  />
                )}
                {selectionTab === 'review' && (
                  <ReviewPanel
                    selectedDetection={selectedDetection}
                    onReviewed={() => fetchDetections()}
                    onJump={(id) => {
                      const feat = detectionsGeoJSON?.features?.find(
                        (f: any) => Number(f.properties?.id) === id,
                      );
                      if (feat) setSelectedDetection(feat);
                    }}
                  />
                )}

                <div className="border-b border-sentinel-line p-3">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="sentinel-label flex-1">Candidate Links</span>
                    <span className="sentinel-tag">{candidateLinks.length}</span>
                  </div>
                  {candidateLinks.length === 0 && (
                    <div className="text-[11px] text-sentinel-muted">No candidate target links. Use Add To Link Graph to generate review candidates.</div>
                  )}
                  <div className="space-y-2">
                    {candidateLinks.slice(0, 4).map((candidate) => (
                      <div key={candidate.id} className="border border-sentinel-line bg-sentinel-bg p-2">
                        <div className="flex items-center gap-2">
                          <span className="min-w-0 flex-1 truncate text-xs text-slate-200">{candidate.target_name || candidate.target_id}</span>
                          <span className={`sentinel-tag ${candidate.status === 'approved' ? 'ok' : candidate.status === 'rejected' ? 'crit' : 'warn'}`}>{candidate.status}</span>
                        </div>
                        <div className="mt-1 font-mono text-[10px] text-sentinel-muted">{Math.round(Number(candidate.score || 0) * 100)} score / {candidate.reason}</div>
                        {candidate.status === 'pending' && (
                          <div className="mt-2 grid grid-cols-2 gap-2">
                            <button type="button" disabled={isActionBusy} onClick={() => approveCandidate(candidate.id)} className="sentinel-btn justify-center disabled:opacity-40">Approve</button>
                            <button type="button" disabled={isActionBusy} onClick={() => rejectCandidate(candidate.id)} className="sentinel-btn justify-center disabled:opacity-40">Reject</button>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                <div className="sentinel-panel-header">
                  <Activity className="h-4 w-4" />
                  <span>Actions</span>
                </div>
                <div className="space-y-2 p-3">
                  <button
                    type="button"
                    disabled={isActionBusy || !selectedDetection}
                    onClick={cueCollection}
                    className="sentinel-btn primary w-full justify-center disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Send className="h-3.5 w-3.5" /> Cue Collection
                  </button>
                  <button
                    type="button"
                    disabled={isActionBusy || !selectedDetection}
                    onClick={addToLinkGraph}
                    className="sentinel-btn w-full justify-center disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <GitBranch className="h-3.5 w-3.5" /> Add To Link Graph
                  </button>
                  <div className="min-h-8 border border-sentinel-line bg-sentinel-bg px-2 py-1 font-mono text-[10px] text-sentinel-muted">
                    {actionStatus || 'Detection action ready.'}
                  </div>
                </div>
              </>
            );
          })() : (
            <div className="border-b border-sentinel-line p-3 text-xs text-sentinel-muted">Select a detection polygon to inspect classification details.</div>
          ))}

          {rightTab === 'analytics' && (
            <AnalyticsToolsPanel
              pendingPick={pendingPick}
              onRequestPick={setPendingPick}
              lastMapClick={lastMapClick}
              layers={{
                viewshed: { on: !!activeLayers.viewshed, disabled: !analyticsResults.viewshed },
                los: { on: !!activeLayers.los, disabled: !analyticsResults.los },
                routes: { on: !!activeLayers.routes, disabled: !analyticsResults.routes },
              }}
              onToggleLayer={(kind) =>
                setActiveLayers((prev) => ({ ...prev, [kind]: !prev[kind] }))
              }
              onResult={(kind, response) => {
                setAnalyticsResults((prev) => ({ ...prev, [kind]: response }));
                if (response) setActiveLayers((prev) => ({ ...prev, [kind]: true }));
                setLastMapClick(null);
              }}
            />
          )}

          {rightTab === 'similar' && (
            selectedDetection ? (
              <SimilarPanel
                selectedDetection={selectedDetection}
                onSelect={(id) => {
                  const feat = detectionsGeoJSON?.features?.find(
                    (f: any) => Number(f.properties?.id) === id,
                  );
                  if (feat) setSelectedDetection(feat);
                }}
              />
            ) : (
              <div className="border-b border-sentinel-line p-3 text-xs text-sentinel-muted">Select a detection polygon to inspect similar objects.</div>
            )
          )}

          {rightTab === 'tracks' && (
            <>
              <div className="sentinel-panel-header">
                <Navigation className="h-4 w-4" />
                <span>Active Tracks</span>
                <span className="sentinel-tag info ml-auto">{data.tracks.length}</span>
              </div>
              <div className="border-b border-sentinel-line p-3">
                <button
                  type="button"
                  disabled={isActionBusy || !selectedDetection}
                  onClick={() => selectedDetection && pinTrack(selectedDetection.properties.id)}
                  className="sentinel-btn w-full justify-center disabled:cursor-not-allowed disabled:opacity-40"
                  title={selectedDetection ? 'Force-create a track from the selected detection' : 'Select a detection first'}
                >
                  <Crosshair className="h-3.5 w-3.5" /> Track Object
                </button>
              </div>
              {data.tracks.length === 0 ? (
                <div className="border-b border-sentinel-line p-3 text-[11px] text-sentinel-muted">No active tracks.</div>
              ) : (
                data.tracks.map((track: any) => (
                  <div key={track.id} className="sentinel-row grid-cols-[1fr_auto]">
                    <span className="min-w-0">
                      <span className="block truncate text-xs text-slate-200">{track.properties?.callsign || track.asset_id || track.id}</span>
                      <span className="block truncate font-mono text-[10px] text-sentinel-muted">{track.label}</span>
                    </span>
                    <span className="sentinel-tag info">LIVE</span>
                  </div>
                ))
              )}
            </>
          )}
        </div>
      </section>
      ) : (
        <button
          type="button"
          onClick={() => setRightOpen(true)}
          title="Show selection panel"
          style={{
            position: 'absolute',
            right: 14,
            top: 14,
            width: 36,
            zIndex: 500,
            padding: '10px 0',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 8,
            background: 'color-mix(in oklab, var(--bg-1) 94%, transparent)',
            backdropFilter: 'blur(8px)',
            border: '1px solid var(--line)',
            borderRadius: 10,
            color: 'var(--ink-1)',
            cursor: 'pointer',
            boxShadow: '0 6px 18px rgba(0,0,0,.3)',
          }}
        >
          <Crosshair size={14} style={{ color: 'var(--accent)' }} />
          <span
            style={{
              writingMode: 'vertical-rl',
              transform: 'rotate(180deg)',
              fontSize: 10.5,
              letterSpacing: '.06em',
              color: 'var(--ink-1)',
            }}
          >
            Selection {selectedDetection ? `· DET-${selectedDetection.properties?.id}` : ''}
          </span>
          <ChevronLeft size={11} style={{ color: 'var(--ink-3)' }} />
        </button>
      )}
    </div>
  );
}

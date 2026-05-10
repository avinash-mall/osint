import { useCallback, useEffect, useMemo, useState } from 'react';
import { Circle, CircleMarker, GeoJSON, ImageOverlay, MapContainer, Marker, Polyline, Popup, TileLayer, useMap, useMapEvents, ZoomControl } from 'react-leaflet';
import L from 'leaflet';
import axios from 'axios';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  Activity,
  ChevronDown,
  ChevronRight,
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
  Swords,
} from 'lucide-react';
import { BranchIcon as SharedBranchIcon, ObjectIcon as SharedObjectIcon, objectIconComponent } from '../utils/branchIcons';
import 'leaflet/dist/leaflet.css';
import { useEventStream } from '../hooks/useEventStream';
import { type UploadJob, isUploadActive, uploadMessage, uploadProgress, uploadProgressClass, uploadStage } from '../utils/uploadProgress';
import {
  CATEGORY_ORDER,
  DETECTION_CATEGORIES,
  SOURCE_ORDER,
  classifyDetectionClass,
  detectionClassLabel,
  detectionClassSource,
  type DetectionCategoryId,
} from '../utils/detectionTaxonomy';
import { ALL_BRANCHES, type DefenceBranch } from '../utils/defenceOntology';

const ALL_BRANCHES_BY_ID: Record<string, DefenceBranch> = ALL_BRANCHES.reduce((acc, branch) => {
  acc[branch.id] = branch;
  return acc;
}, {} as Record<string, DefenceBranch>);

const API_URL = import.meta.env.VITE_API_URL || '';
const TILE_PROXY_URL = import.meta.env.VITE_TILE_PROXY_URL || '/tiles';
const CARTO_BASEMAP_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
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

function detectionCategoryForFeature(feature: any): DetectionCategoryId {
  const props = feature?.properties || {};
  return classifyDetectionClass(props.original_class || props.metadata?.original_class || props.class || props.label, props.ontology?.category || props.category);
}

function detectionCategoryForLabel(label: string, ontologyCategory?: string | null): DetectionCategoryId {
  return classifyDetectionClass(label, ontologyCategory);
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

function trackColor(category: string): string {
  const catId = TRACKER_CATEGORY_TO_CATEGORY_ID[category] ?? 'Other';
  return DETECTION_CATEGORIES[catId]?.color ?? '#727a83';
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

const getDetectionStyle = (feature: any) => {
  const category = detectionCategoryForFeature(feature);
  const color = DETECTION_CATEGORIES[category].color;
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
};

function CategoryIcon({ category, className = 'h-3.5 w-3.5' }: { category: DetectionCategoryId; className?: string }) {
  return <SharedBranchIcon branchId={category} className={className} />;
}

function DetectionSubclassIcon({
  label,
  category,
  className = 'h-3.5 w-3.5',
}: {
  label?: string | null;
  category: DetectionCategoryId;
  className?: string;
}) {
  const branch = ALL_BRANCHES_BY_ID[category];
  return <SharedObjectIcon prompt={label} branchIconKey={branch?.iconKey} className={className} />;
}

function detectionIcon(feature: any) {
  const category = detectionCategoryForFeature(feature);
  const color = DETECTION_CATEGORIES[category].color;
  const props = feature?.properties || {};
  const branch = ALL_BRANCHES_BY_ID[category];
  const Icon = objectIconComponent(props.original_class || props.class || props.label, branch?.iconKey);
  const iconMarkup = renderToStaticMarkup(<Icon size={12} strokeWidth={2.2} />);
  return L.divIcon({
    className: '',
    iconSize: [14, 14],
    iconAnchor: [15, 15],
    html: `<div class="sentinel-detection-icon" style="color:${color};border-color:${color};box-shadow:0 0 8px ${color}55;">${iconMarkup}</div>`,
  });
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

function MapCursorTracker({ onCursorChange }: { onCursorChange: (cursor: { lat: number; lon: number }) => void }) {
  useMapEvents({
    mousemove(event) {
      onCursorChange({ lat: event.latlng.lat, lon: event.latlng.lng });
    },
  });
  return null;
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
};

export default function GaiaMap({ onOpenGraph }: GaiaMapProps) {
  const [data, setData] = useState<{ static: any[]; tracks: any[] }>({ static: [], tracks: [] });
  const [imagery, setImagery] = useState<any[]>([]);
  const [detectionsGeoJSON, setDetectionsGeoJSON] = useState<any>({ type: 'FeatureCollection', features: [] });
  const [detectionClasses, setDetectionClasses] = useState<any[]>([]);
  const [basemapGeoJSON, setBasemapGeoJSON] = useState<any>({ type: 'FeatureCollection', features: [] });
  const [uploadJobs, setUploadJobs] = useState<UploadJob[]>([]);
  const [selectedImagery, setSelectedImagery] = useState<number | null>(null);
  const [imageryOpacity, setImageryOpacity] = useState(0.8);
  const [hiddenDetectionLabels, setHiddenDetectionLabels] = useState<string[]>([]);
  const [hiddenDetectionCategories, setHiddenDetectionCategories] = useState<DetectionCategoryId[]>([]);
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
  const [timeRange, setTimeRange] = useState<{ start: string; end: string }>(() => {
    const now = new Date();
    const hourAgo = new Date(now.getTime() - 60 * 60 * 1000);
    return { start: hourAgo.toISOString(), end: now.toISOString() };
  });
  const [mapBounds, setMapBounds] = useState('');
  const [activeLayers, setActiveLayers] = useState({
    satellite: true,
    detections: true,
    tracks: true,
    detectionTracks: true,
    static: true,
    grid: true,
  });
  const [isLoading, setIsLoading] = useState(false);

  const selectedImageryData = imagery.find((img) => img.id === selectedImagery);
  const processingUploads = useMemo(
    () => uploadJobs.filter((job) => job.media_type === 'imagery' && isUploadActive(job)).slice(0, 3),
    [uploadJobs],
  );

  const detectionLabelStats = useMemo<DetectionClassStat[]>(() => {
    const stats = new Map<string, DetectionClassStat>();
    const parentClassesWithSubclassDetails = new Set<string>();

    for (const feature of detectionsGeoJSON.features || []) {
      const rawClass = detectionLabel(feature);
      const parentClass = String(feature?.properties?.parent_class || feature?.properties?.metadata?.parent_class || rawClass);
      const storedClass = String(feature?.properties?.class || '');
      const category = detectionCategoryForLabel(rawClass, feature?.properties?.ontology?.category || feature?.properties?.category);
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
        color: DETECTION_CATEGORIES[category].color,
        ontology: existing?.ontology || feature?.properties?.ontology,
        threatLevel: existing?.threatLevel || feature?.properties?.threat_level,
        category,
        source: detectionClassSource(rawClass),
      });
    }

    for (const meta of detectionClasses) {
      const rawClass = String(meta.class || meta.label || 'Unknown');
      const parentClass = String(meta.parent_class || meta.ontology?.parent_class || rawClass);
      const category = detectionCategoryForLabel(rawClass, meta?.ontology?.category);
      const existing = stats.get(rawClass);
      if (!existing && parentClassesWithSubclassDetails.has(rawClass)) continue;
      stats.set(rawClass, {
        ...existing,
        rawClass,
        parentClass,
        label: existing?.label || meta?.label || detectionClassLabel(rawClass),
        count: Number(existing?.count ?? meta?.count ?? 0),
        maxConfidence: Math.max(Number(existing?.maxConfidence || 0), Number(meta?.max_confidence || 0)),
        color: DETECTION_CATEGORIES[category].color,
        ontology: existing?.ontology || meta?.ontology,
        threatLevel: existing?.threatLevel || meta?.threat_level,
        category,
        source: detectionClassSource(rawClass),
      });
    }

    return Array.from(stats.values()).filter((item) => item.count > 0).sort((a, b) => (
      CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category)
      || b.count - a.count
      || a.label.localeCompare(b.label)
    ));
  }, [detectionsGeoJSON, detectionClasses]);

  const filteredDetectionsGeoJSON = useMemo(() => ({
    ...detectionsGeoJSON,
    features: (detectionsGeoJSON.features || []).filter((feature: any) => {
      const labels = detectionClassKeys(feature);
      if (detectionClassFilter && !labels.includes(detectionClassFilter)) return false;
      if (hiddenDetectionCategories.includes(detectionCategoryForFeature(feature))) return false;
      return !labels.some((label) => hiddenDetectionLabels.includes(label));
    }),
  }), [detectionsGeoJSON, detectionClassFilter, hiddenDetectionCategories, hiddenDetectionLabels]);

  const filteredDetectionClassStats = useMemo(() => {
    const query = detectionLabelSearch.trim().toLowerCase();
    return query
      ? detectionLabelStats.filter((item) => `${item.label} ${item.rawClass} ${item.parentClass || ''} ${DETECTION_CATEGORIES[item.category].label} ${item.source} ${item.ontology?.category || ''} ${item.threatLevel || ''}`.toLowerCase().includes(query))
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
      const categoryMeta = DETECTION_CATEGORIES[category];
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
  }, [detectionGroupMode, filteredDetectionClassStats]);

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

  const fetchUploadJobs = useCallback(async () => {
    try {
      const response = await axios.get(`${API_URL}/api/ingest/uploads`);
      setUploadJobs(response.data.uploads || []);
    } catch (error) {
      console.error('Error fetching upload jobs:', error);
    }
  }, []);

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
    if (!mapBounds) return;
    try {
      const classParams = new URLSearchParams({
        start_time: timeRange.start,
        end_time: timeRange.end,
        bbox: mapBounds,
        llm: 'true',
      });
      const response = await axios.get(`${API_URL}/api/detections/classes?${classParams.toString()}`, { timeout: 10000 });
      setDetectionClasses(response.data?.classes || []);
    } catch (error) {
      console.error('Error fetching detection classes:', error);
    }
  }, [mapBounds, timeRange]);

  const fetchDetectionFeatures = useCallback(async () => {
    if (!mapBounds || !detectionClassFilter) {
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
        det_class: detectionClassFilter,
        limit: '20000',
      });
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
  useEffect(() => { fetchUploadJobs(); }, [fetchUploadJobs]);
  useEffect(() => { fetchImagery(); }, [fetchImagery]);
  useEffect(() => { fetchDetectionClasses(); }, [fetchDetectionClasses]);
  useEffect(() => { fetchDetectionFeatures(); }, [fetchDetectionFeatures]);
  useEffect(() => { fetchDetectionTracks(); }, [fetchDetectionTracks]);

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

  useEffect(() => {
    if (processingUploads.length === 0) return;
    const timer = window.setInterval(fetchUploadJobs, 2000);
    return () => window.clearInterval(timer);
  }, [processingUploads.length, fetchUploadJobs]);

  useEventStream('geotime', useCallback(() => { fetchData(); }, [fetchData]));
  useEventStream('detections', useCallback((message: any) => {
    focusTimeRange(message?.acquisition_time);
    fetchDetections();
    fetchDetectionTracks();
    fetchImagery();
    fetchUploadJobs();
  }, [focusTimeRange, fetchDetections, fetchDetectionTracks, fetchImagery, fetchUploadJobs]));
  useEventStream('imagery', useCallback((message: any) => {
    focusTimeRange(message?.acquisition_time);
    fetchImagery();
    fetchUploadJobs();
  }, [focusTimeRange, fetchImagery, fetchUploadJobs]));
  useEventStream('ops', useCallback((message: any) => {
    if (String(message?.type || '').startsWith('imagery_') || message?.type === 'upload_received') {
      focusTimeRange(message?.acquisition_time);
      fetchUploadJobs();
    }
  }, [focusTimeRange, fetchUploadJobs]));

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
    const categoryMeta = DETECTION_CATEGORIES[category];
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
    <div className="grid h-full min-h-0 grid-cols-[320px_minmax(0,1fr)_320px] gap-px bg-sentinel-line">
      <section className="sentinel-panel min-h-0 border-0">
        <div className="sentinel-panel-header">
          <Layers className="h-4 w-4" />
          <span>Layers / Classes</span>
          <button type="button" onClick={fetchDetections} className="sentinel-icon-btn ml-auto h-6 w-6">
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="sentinel-scroll">
          <div className="border-b border-sentinel-line p-2">
            <div className="grid grid-cols-3 border border-sentinel-line-2">
              {['BASE', 'SAT', 'TERRAIN'].map((item, index) => (
                <button key={item} className={`h-7 text-[10px] ${index === 0 ? 'bg-sentinel-panel-2 text-sentinel-text' : 'text-sentinel-muted'}`} type="button">
                  {item}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-2 border-b border-sentinel-line bg-sentinel-panel-2 px-3 py-2">
            <span className="sentinel-label flex-1">Overlays</span>
            <button type="button" onClick={() => setShowBbox((value) => !value)} className={`sentinel-btn h-6 ${showBbox ? 'primary' : ''}`}>
              BBOX
            </button>
          </div>

          {[
            { key: 'satellite', label: 'Satellite Imagery', metric: imagery.length, color: 'text-sentinel-info' },
            { key: 'detections', label: 'AI Detections', metric: visibleDetectionCount, color: 'text-sentinel-accent' },
            { key: 'tracks', label: 'Active Tracks', metric: data.tracks.length, color: 'text-sentinel-info' },
            { key: 'static', label: 'Static Features', metric: data.static.length, color: 'text-sentinel-crit' },
            { key: 'grid', label: 'Tactical Grid', metric: 'WGS84', color: 'text-sentinel-muted' },
          ].map((layer) => {
            const active = activeLayers[layer.key as keyof typeof activeLayers];
            return (
              <button
                key={layer.key}
                type="button"
                onClick={() => setActiveLayers((prev) => ({ ...prev, [layer.key]: !active }))}
                className="sentinel-row w-full grid-cols-[22px_1fr_auto] text-left"
              >
                <span className={active ? layer.color : 'text-sentinel-muted'}>{active ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}</span>
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
                      {detectionGroupMode === 'CAT' && <span style={{ color: groupColor }}><CategoryIcon category={category} /></span>}
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
                      return (
                        <div key={item.rawClass} className="grid grid-cols-[22px_18px_1fr_auto_auto] items-center gap-2 px-3 py-1.5">
                          <button type="button" style={{ color: hidden ? 'var(--ink-2)' : item.color }} onClick={() => toggleDetectionClassVisibility(item.rawClass)}>
                            {hidden ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                          </button>
                          <span style={{ color: hidden ? 'var(--ink-2)' : item.color }}>
                            <DetectionSubclassIcon label={item.rawClass} category={item.category} className="h-3 w-3" />
                          </span>
                          <button type="button" className="min-w-0 text-left" onClick={() => soloDetectionClass(item.rawClass)}>
                            <span className={`block truncate text-[11px] ${hidden ? 'text-sentinel-muted' : 'text-slate-200'}`}>{item.label}{solo ? ' / SOLO' : ''}</span>
                          </button>
                          <span className={`sentinel-tag ${threatClass(item.threatLevel)}`}>{item.threatLevel || DETECTION_CATEGORIES[item.category].short}</span>
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
                  onClick={() => setSelectedImagery(selectedImagery === img.id ? null : img.id)}
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

      <section className="relative flex min-h-0 min-w-0 flex-col bg-sentinel-bg">
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
            <MapFitToImagery imagery={selectedImageryData} />
            <MapFitToDetections geojson={filteredDetectionsGeoJSON} filterKey={detectionClassFilter} />

            <TileLayer
              url={CARTO_BASEMAP_URL}
              subdomains="abcd"
              maxZoom={20}
              opacity={1}
              attribution="&copy; OpenStreetMap &copy; CARTO"
            />

            <ImageOverlay url="/world_map.svg" bounds={[[-85, -180], [85, 180]]} opacity={0.32} />

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

            {activeLayers.satellite && selectedImageryData && (
              <TileLayer
                url={`${TILE_PROXY_URL}/cog/tiles/{z}/{x}/{y}?url=${encodeURIComponent(selectedImageryData.file_path)}`}
                opacity={imageryOpacity}
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
              const categoryMeta = DETECTION_CATEGORIES[category];
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
                        <span style={{ color: categoryMeta.color }}><DetectionSubclassIcon label={props.original_class || props.class || props.label} category={category} /></span>
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
              const categoryMeta = DETECTION_CATEGORIES[category];
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
                        <span style={{ color: categoryMeta.color }}><DetectionSubclassIcon label={props.original_class || props.class || props.label} category={category} /></span>
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

            {activeLayers.detections && showBbox && filteredDetectionsGeoJSON.features?.length > 0 && (
              <CanvasGeoJSON
                key={`detections-${detectionsLayerVersion}-${detectionClassFilter || 'all'}-${hiddenDetectionCategories.join('|')}-${hiddenDetectionLabels.join('|')}-${filteredDetectionsGeoJSON.features.length}`}
                data={filteredDetectionsGeoJSON}
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
          </div>

          {isLoading && (
            <div className="absolute left-1/2 top-1/2 z-[500] -translate-x-1/2 -translate-y-1/2 border border-sentinel-line bg-sentinel-panel px-4 py-2 text-xs text-slate-300">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 animate-pulse rounded-full bg-sentinel-accent" />
                Loading detections
              </div>
            </div>
          )}

          {processingUploads.length > 0 && (
            <div className="absolute bottom-24 left-4 z-[500] w-96 max-w-[calc(100%-2rem)] border border-sentinel-line bg-sentinel-panel p-3">
              <div className="mb-2 flex items-center justify-between text-xs font-bold uppercase tracking-widest text-sentinel-muted">
                <span>Imagery Processing</span>
                <span>{processingUploads.length}</span>
              </div>
              <div className="space-y-2">
                {processingUploads.map((job) => {
                  const progress = uploadProgress(job);
                  return (
                    <div key={job.upload_id} className="border border-sentinel-line bg-sentinel-bg px-2 py-2">
                      <div className="flex items-center justify-between gap-2 text-xs">
                        <span className="truncate font-semibold text-slate-200">{job.filename}</span>
                        <span className="font-mono text-sentinel-muted">{progress}%</span>
                      </div>
                      <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-sentinel-muted">
                        <span className="uppercase">{uploadStage(job)}</span>
                        <span className="truncate">{uploadMessage(job)}</span>
                      </div>
                      <div className="mt-2 h-1.5 w-full bg-sentinel-panel-2">
                        <div className={`h-full transition-all duration-500 ${uploadProgressClass(job)}`} style={{ width: `${progress}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        <div className="h-20 border-t border-sentinel-line bg-sentinel-panel px-3 py-2">
          <div className="mb-1 flex items-center gap-3">
            <button type="button" className="sentinel-icon-btn h-6 w-6" onClick={() => setTimelinePlaying((value) => !value)}>
              {timelinePlaying ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            </button>
            <button type="button" className="sentinel-icon-btn h-6 w-6" onClick={fetchDetections}>
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
            <span className="sentinel-label">Timeline</span>
            <div className="grid grid-cols-3 border border-sentinel-line-2">
              {[15, 30, 60].map((minutes) => (
                <button key={minutes} type="button" onClick={() => setRecentWindow(minutes)} className={`h-6 px-3 text-[10px] ${timelineWindowMinutes === minutes ? 'bg-sentinel-panel-2 text-slate-100' : 'text-sentinel-muted'}`}>
                  {minutes}M
                </button>
              ))}
            </div>
            <span className="ml-auto font-mono text-[10px] text-sentinel-muted">{new Date(timeRange.start).toLocaleTimeString()} / {new Date(timeRange.end).toLocaleTimeString()}</span>
          </div>
          <div className="relative flex h-9 items-end gap-px border border-sentinel-line bg-sentinel-bg p-0.5">
            {timelineBuckets.map((value, index) => {
              const inWindow = index >= 60 - timelineWindowMinutes;
              return (
                <div
                  key={index}
                  className={inWindow ? 'bg-sentinel-accent' : 'bg-sentinel-line-2'}
                  style={{ flex: 1, height: `${Math.max(4, (value / maxTimelineBucket) * 100)}%`, opacity: inWindow ? 0.45 + (value / maxTimelineBucket) * 0.55 : 0.35 }}
                />
              );
            })}
            <div className="absolute bottom-0 top-0 w-px bg-sentinel-accent" style={{ left: `${((60 - timelineWindowMinutes) / 60) * 100}%` }} />
          </div>
        </div>
      </section>

      <section className="sentinel-panel min-h-0 border-0">
        <div className="sentinel-panel-header">
          <Crosshair className="h-4 w-4" />
          <span>Selection</span>
          <span className="sentinel-tag acc ml-auto">DETAIL</span>
        </div>
        <div className="sentinel-scroll">
          {selectedDetection ? (
            <>
              <div className="border-b border-sentinel-line p-3">
                {(() => {
                  const category = detectionCategoryForFeature(selectedDetection);
                  const categoryMeta = DETECTION_CATEGORIES[category];
                  return (
                    <>
                      <div className="font-mono text-[10px] text-sentinel-muted">DET-{selectedDetection.properties?.id} / {selectedDetection.properties?.parent_class || selectedDetection.properties?.class}</div>
                      <div className="mt-1 flex items-center gap-2">
                        <span style={{ color: categoryMeta.color }}><CategoryIcon category={category} /></span>
                        <div className="text-lg font-semibold text-slate-100">{selectedDetection.properties?.label || detectionClassLabel(selectedDetection.properties?.class)}</div>
                      </div>
                    </>
                  );
                })()}
                <div className="mt-2 flex flex-wrap gap-2">
                  <span className={`sentinel-tag ${threatClass(selectedDetection.properties?.threat_level)}`}>{selectedDetection.properties?.threat_level || 'low'}</span>
                  <span className="sentinel-tag">{selectedDetection.properties?.allegiance || 'unknown'}</span>
                  <span className="sentinel-tag info">{Math.round(Number(selectedDetection.properties?.confidence || 0) * 100)}% CONF</span>
                  <span className="sentinel-tag acc">{selectedDetection.properties?.review_status || selectedDetection.properties?.metadata?.review_status || 'review'}</span>
                </div>
              </div>
              <div className="border-b border-sentinel-line p-3 text-xs leading-relaxed text-sentinel-muted">
                {selectedDetection.properties?.ontology?.description || 'Detection ontology unavailable.'}
                <div className="mt-2 grid grid-cols-[92px_1fr] gap-y-1 font-mono text-[10px]">
                  <span>ASSESS</span><span>{selectedDetection.properties?.assessment_status || selectedDetection.properties?.ontology?.assessment_status || 'unconfirmed'}</span>
                  <span>ORIGINAL</span><span>{selectedDetection.properties?.original_class || selectedDetection.properties?.metadata?.original_class || selectedDetection.properties?.class || 'unknown'}</span>
                  <span>PROFILE</span><span>{selectedDetection.properties?.threshold_profile || selectedDetection.properties?.metadata?.threshold_profile || 'n/a'}</span>
                  <span>COVERAGE</span><span>{selectedDetection.properties?.coverage_fraction ? `${Math.round(Number(selectedDetection.properties.coverage_fraction) * 100)}%` : 'n/a'}</span>
                  <span>THREAT SCORE</span><span>{Number(selectedDetection.properties?.threat_confidence || selectedDetection.properties?.ontology?.threat_confidence || 0).toFixed(2)}</span>
                  <span>EVIDENCE</span><span>{(selectedDetection.properties?.evidence || selectedDetection.properties?.ontology?.evidence || []).join(' / ') || 'none'}</span>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2 border-b border-sentinel-line p-3">
                <button type="button" disabled={isActionBusy} onClick={() => tagDetection(selectedDetection.properties.id, 'friendly')} className="sentinel-btn justify-center disabled:opacity-40"><Shield className="h-3.5 w-3.5" /> Friendly</button>
                <button type="button" disabled={isActionBusy} onClick={() => tagDetection(selectedDetection.properties.id, 'hostile')} className="sentinel-btn justify-center disabled:opacity-40"><Swords className="h-3.5 w-3.5" /> Hostile</button>
                <button type="button" disabled={isActionBusy} onClick={() => tagDetection(selectedDetection.properties.id, 'neutral')} className="sentinel-btn justify-center disabled:opacity-40"><CircleHelp className="h-3.5 w-3.5" /> Neutral</button>
                <button type="button" disabled={isActionBusy} onClick={() => tagDetection(selectedDetection.properties.id, 'unknown')} className="sentinel-btn justify-center disabled:opacity-40">Clear</button>
                <button
                  type="button"
                  disabled={isActionBusy}
                  onClick={() => pinTrack(selectedDetection.properties.id)}
                  className="sentinel-btn col-span-2 justify-center disabled:opacity-40"
                  title="Force-create a track from this detection regardless of confidence"
                >
                  <Crosshair className="h-3.5 w-3.5" /> Track Object
                </button>
              </div>
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
            </>
          ) : (
            <div className="border-b border-sentinel-line p-3 text-xs text-sentinel-muted">Select a detection polygon to inspect classification details.</div>
          )}

          <div className="sentinel-panel-header">
            <Satellite className="h-4 w-4" />
            <span>Selected Imagery</span>
          </div>
          <div className="border-b border-sentinel-line p-3">
            <div className="text-sm font-semibold text-slate-100">{selectedImageryData?.name || 'No imagery selected'}</div>
            <div className="mt-2 grid grid-cols-[80px_1fr] gap-y-1 font-mono text-[11px]">
              <span className="text-sentinel-muted">SENSOR</span><span>{selectedImageryData?.sensor_type || 'n/a'}</span>
              <span className="text-sentinel-muted">CLOUD</span><span>{selectedImageryData?.cloud_cover ?? 'n/a'}%</span>
              <span className="text-sentinel-muted">ACQ</span><span className="truncate">{selectedImageryData?.acquisition_time ? new Date(selectedImageryData.acquisition_time).toLocaleString() : 'n/a'}</span>
            </div>
            {selectedImageryData && (
              <div className="mt-3">
                <div className="mb-1 sentinel-label">Opacity</div>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={imageryOpacity}
                  onChange={(event) => setImageryOpacity(parseFloat(event.target.value))}
                  className="w-full"
                />
              </div>
            )}
          </div>

          <div className="sentinel-panel-header">
            <Navigation className="h-4 w-4" />
            <span>Active Tracks</span>
            <span className="sentinel-tag info ml-auto">{data.tracks.length}</span>
          </div>
          {data.tracks.slice(0, 8).map((track: any) => (
            <div key={track.id} className="sentinel-row grid-cols-[1fr_auto]">
              <span className="min-w-0">
                <span className="block truncate text-xs text-slate-200">{track.properties?.callsign || track.asset_id || track.id}</span>
                <span className="block truncate font-mono text-[10px] text-sentinel-muted">{track.label}</span>
              </span>
              <span className="sentinel-tag info">LIVE</span>
            </div>
          ))}

          <div className="sentinel-panel-header">
            <Crosshair className="h-4 w-4" />
            <span>Detection Tracks</span>
            <span className="sentinel-tag info ml-auto">{detectionTracks.filter((t) => t.status !== 'lost').length}</span>
          </div>
          {detectionTracks.length === 0 && (
            <div className="border-b border-sentinel-line p-3 text-[11px] text-sentinel-muted">No detection tracks. Process imagery to generate tracks.</div>
          )}
          {detectionTracks.filter((t) => t.status !== 'lost').slice(0, 8).map((track) => {
            const color = trackColor(track.category);
            const isSelected = selectedDetectionTrack?.track_uid === track.track_uid;
            return (
              <button
                type="button"
                key={track.track_uid}
                className={`sentinel-row grid-cols-[1fr_auto] w-full text-left ${isSelected ? 'bg-sentinel-line-2' : ''}`}
                onClick={() => setSelectedDetectionTrack(isSelected ? null : track)}
              >
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5">
                    <span style={{ color }} className="text-[8px]">●</span>
                    <span className="truncate text-xs text-slate-200">{track.primary_class}</span>
                    <span className={`sentinel-tag ${threatClass(track.threat_level)}`}>{track.threat_level}</span>
                    {track.pinned && <span className="sentinel-tag warn">PIN</span>}
                  </span>
                  <span className="block truncate font-mono text-[10px] text-sentinel-muted">
                    DT-{track.track_uid.slice(-6)} · {track.obs_count} obs · {relativeTime(track.last_seen)}
                  </span>
                </span>
                <span className={`sentinel-tag ${track.status === 'confirmed' || track.status === 'pinned' ? 'ok' : track.status === 'coast' ? 'warn' : 'info'}`}>
                  {track.status.toUpperCase()}
                </span>
              </button>
            );
          })}

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
              {actionStatus || (selectedDetection ? 'Detection action ready.' : 'Select a detection to enable actions.')}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

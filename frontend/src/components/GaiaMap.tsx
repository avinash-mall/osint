// Lightweight type aliases consumed by the new map/ sub-components
// (MapStage, LayerPanel, SelectionPanel, ChangeDetectionDialog). The
// monolith owns its own richer internal shapes; these exports are the
// public contract for incremental extraction.
export type Detection = any;
export type Pass = any;

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import axios from 'axios';
import {
  Activity,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Crosshair,
  Layers,
  Pause,
  Play,
  RefreshCw,
} from 'lucide-react';
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
} from '../utils/detectionTaxonomy';
import type { OntologyBranch } from '../utils/useOntology';
import {
  confidenceValue,
  detectionClassKeys,
  detectionLabel,
  makeDetectionStyle,
  timestampInRange,
  trackColorFor,
  type DetectionClassStat,
  type DetectionTrack,
} from './map/_helpers';
import { makeDetectionIcon } from './map/_icons';
import LayerPanel from './map/LayerPanel';
import MapStage, { type MapHandle } from './map/MapStage';
import SelectionPanel from './map/SelectionPanel';
import TimeMachineBar from './map/TimeMachineBar';
import { useAuth } from '../hooks/useAuth';
import {
  type AnalyticsKind,
  type AnalyticsPick,
} from './map/AnalyticsToolsPanel';
import type { AnalyticsResponse } from '../services/analytics';

const API_URL = import.meta.env.VITE_API_URL || '';
const DETECTION_CENTER_MARKER_LIMIT = 800;

// Module-level icon factories + detection helpers live in map/_helpers + map/_icons.

// Map event handlers + bounds helpers live in map/MapEventHandlers.

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
  const [layerOpacities, setLayerOpacities] = useState<{ base: number; terrain: number }>({ base: 1, terrain: 1 });
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
  const [detectionLabelSearch, setDetectionLabelSearch] = useState('');
  const [selectedDetection, setSelectedDetection] = useState<any | null>(null);
  const [detectionTracks, setDetectionTracks] = useState<DetectionTrack[]>([]);
  const [selectedDetectionTrack, setSelectedDetectionTrack] = useState<DetectionTrack | null>(null);
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
  const [bboxMode, setBboxMode] = useState<'hbb' | 'obb' | 'mask'>('obb');
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
  // Side-by-side imagery comparator — see SwipeControl.tsx.
  const [compareImageryId, setCompareImageryId] = useState<number | null>(null);
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
    borders: true,
    graticule: false,
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
  const mapStageRef = useRef<MapHandle>(null);

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

  // Map+ geometry mode â€” rewrite each feature's geometry into the requested
  // shape:
  //   hbb  â†’ axis-aligned envelope (Polygon) from the original geometry
  //   obb  â†’ polygon built from metadata.obb when present; falls back to mask
  //   mask â†’ the raw geometry as ingested (default for SAM3 outputs)
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
        // OBB â€” if metadata.obb is a polygon of [lon,lat] pairs, use it; else
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
  const showDetectionCenterMarkers =
    visibleDetectionCount > 0 && visibleDetectionCount <= DETECTION_CENTER_MARKER_LIMIT;
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
    // The class legend shows every class present in the timeframe globally â€”
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


  return (
    <div ref={workspaceRef} className="map-workspace" style={{ position: 'relative', height: '100%', width: '100%', background: 'var(--bg-0)', overflow: 'hidden' }}>
      {/* Full-bleed map column (rendered below, sandwiched between the floating
          left / right panels via z-index).  This is now the workspace canvas. */}
      {leftOpen ? (
      <LayerPanel
        onRefresh={fetchDetections}
        onCollapse={() => setLeftOpen(false)}
        activeBaseLayer={activeBaseLayer}
        setActiveBaseLayer={setActiveBaseLayer}
        layerOpacities={layerOpacities}
        setLayerOpacities={setLayerOpacities}
        mapZoom={mapZoom}
        overlaysOpen={overlaysOpen}
        setOverlaysOpen={setOverlaysOpen}
        activeLayers={activeLayers}
        setActiveLayers={setActiveLayers}
        imagery={imagery}
        visibleDetectionCount={visibleDetectionCount}
        tracksCount={data.tracks.length}
        staticCount={data.static.length}
        analyticsCounts={{
          viewshed: analyticsResults.viewshed?.result?.features?.length ?? 0,
          viewshedAvailable: !!analyticsResults.viewshed,
          los: analyticsResults.los?.result?.features?.length ?? 0,
          losAvailable: !!analyticsResults.los,
          routes: analyticsResults.routes?.result?.features?.length ?? 0,
          routesAvailable: !!analyticsResults.routes,
        }}
        detectionGroups={detectionGroups}
        detectionGroupMode={detectionGroupMode}
        setDetectionGroupMode={setDetectionGroupMode}
        detectionLabelSearch={detectionLabelSearch}
        setDetectionLabelSearch={setDetectionLabelSearch}
        expandedDetectionGroups={expandedDetectionGroups}
        hiddenDetectionCategories={hiddenDetectionCategories}
        hiddenDetectionLabels={hiddenDetectionLabels}
        detectionClassFilter={detectionClassFilter}
        maxDetectionLabelCount={maxDetectionLabelCount}
        branchById={branchById}
        categories={DETECTION_CATEGORIES}
        showAllDetectionClasses={showAllDetectionClasses}
        hideAllDetectionClasses={hideAllDetectionClasses}
        invertDetectionClasses={invertDetectionClasses}
        toggleDetectionGroupExpanded={toggleDetectionGroupExpanded}
        toggleDetectionGroupVisibility={toggleDetectionGroupVisibility}
        toggleDetectionClassVisibility={toggleDetectionClassVisibility}
        soloDetectionClass={soloDetectionClass}
        selectedImagery={selectedImagery}
        setSelectedImagery={setSelectedImagery}
      />
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

      <MapStage
        ref={mapStageRef}
        activeBaseLayer={activeBaseLayer}
        layerOpacities={layerOpacities}
        selectedImageryData={selectedImageryData}
        filteredDetectionsGeoJSON={filteredDetectionsGeoJSON}
        geomDisplayedDetectionsGeoJSON={geomDisplayedDetectionsGeoJSON}
        detectionsGeoJSON={detectionsGeoJSON}
        detectionClassFilter={detectionClassFilter}
        bboxMode={bboxMode}
        setBboxMode={setBboxMode}
        showDetectionCenterMarkers={showDetectionCenterMarkers}
        detectionIcon={detectionIcon}
        getDetectionStyle={getDetectionStyle}
        detectionCanvasRenderer={detectionCanvasRenderer}
        setSelectedDetection={setSelectedDetection}
        activeLayers={activeLayers}
        setActiveLayers={setActiveLayers}
        data={data}
        detectionTracks={detectionTracks}
        selectedDetectionTrack={selectedDetectionTrack}
        setSelectedDetectionTrack={setSelectedDetectionTrack}
        trackColor={trackColor}
        prithviOverlays={prithviOverlays}
        setPrithviOverlays={setPrithviOverlays}
        prithviGeojson={prithviGeojson}
        analyticsResults={analyticsResults}
        pendingPick={pendingPick}
        setLastMapClick={setLastMapClick}
        basemapGeoJSON={basemapGeoJSON}
        setMapBounds={setMapBounds}
        setMapZoom={setMapZoom}
        setCursor={setCursor}
        cursor={cursor}
        mapZoom={mapZoom}
        drawMode={drawMode}
        setDrawMode={setDrawMode}
        drawError={drawError}
        createManualDetection={createManualDetection}
        visibleDetectionCount={visibleDetectionCount}
        timelineWindowMinutes={timelineWindowMinutes}
        isLoading={isLoading}
        categories={DETECTION_CATEGORIES}
        branchById={branchById}
        compareImagery={
          compareImageryId
            ? imagery.find((p: any) => Number(p.id) === compareImageryId) || null
            : null
        }
        onClearCompare={() => setCompareImageryId(null)}
      />

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
                activePassId={selectedImagery ?? null}
                comparePassId={compareImageryId}
                onPassPin={(id) => setCompareImageryId(id === compareImageryId ? null : id)}
                onClearCompare={() => setCompareImageryId(null)}
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
            const overflowMarkers = visibleDetectionCount > DETECTION_CENTER_MARKER_LIMIT
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
      <SelectionPanel
        rightTab={rightTab}
        setRightTab={setRightTab}
        selectionTab={selectionTab}
        setSelectionTab={setSelectionTab}
        onClose={() => setRightOpen(false)}
        selectedDetection={selectedDetection}
        setSelectedDetection={setSelectedDetection}
        detectionTracks={detectionTracks}
        selectedImageryData={selectedImageryData}
        detectionsGeoJSON={detectionsGeoJSON}
        candidateLinks={candidateLinks}
        pendingPick={pendingPick}
        setPendingPick={setPendingPick}
        lastMapClick={lastMapClick}
        setLastMapClick={setLastMapClick}
        activeLayers={activeLayers}
        setActiveLayers={setActiveLayers}
        analyticsResults={analyticsResults}
        setAnalyticsResults={setAnalyticsResults}
        data={data}
        isActionBusy={isActionBusy}
        actionStatus={actionStatus}
        categories={DETECTION_CATEGORIES}
        branchById={branchById}
        userRole={user?.role}
        onOpenFmv={onOpenFmv}
        actions={{
          tagDetection,
          deleteDetection,
          fetchDetections,
          addToLinkGraph,
          cueCollection,
          pinTrack,
          approveCandidate,
          rejectCandidate,
        }}
      />
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

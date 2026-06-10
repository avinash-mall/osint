/**
 * MapStage — the actual `<MapContainer>` + basemap + detection layer +
 * track polylines + analytics layers + floating map overlays.
 *
 * Extracted from the GaiaMap monolith (Phase D of the map split).
 *
 * All state lives in the parent `GaiaMap` orchestrator; this file is a
 * pure presentation component that reads props and emits map events.
 * A `forwardRef` exposes a small `MapHandle` so the orchestrator can
 * drive imperatives like "pan to this detection" without prop drilling.
 */

import L from 'leaflet';
import { forward as mgrsForward } from 'mgrs';
import {
  Crosshair,
  Eye,
  EyeOff,
  HelpCircle,
  Minus,
  Palette,
  Plus,
  Target,
  X,
} from 'lucide-react';
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import {
  Circle,
  CircleMarker,
  GeoJSON,
  MapContainer,
  Marker,
  Polygon,
  Polyline,
  Popup,
  TileLayer,
} from 'react-leaflet';

import {
  categoryFor,
  detectionClassLabel,
  type DetectionCategoryMap,
} from '../../utils/detectionTaxonomy';
import type { OntologyBranch } from '../../utils/useOntology';

import {
  detectionBadgePosition,
  detectionCategoryForFeature,
  detectionCenter,
  detectionDisplayLabel,
  geojsonToLatLngs,
  labelQuality,
  relativeTime,
  trackDashArray,
  type DetectionTrack,
} from './_helpers';
import { DetectionSubclassIcon, blueIcon, createIcon, emeraldIcon, redIcon } from './_icons';
import ManualDetectionDialog from './ManualDetectionDialog';
import MgrsGraticule from './MgrsGraticule';
import SwipeControl from './SwipeControl';
import type { AnalyticsPick } from './AnalyticsToolsPanel';
import type { AnalyticsResponse } from '../../services/analytics';
import {
  AnalyticsPickHandler,
  DrawRectHandler,
  MapBoundsUpdater,
  MapClickPicker,
  MapContextHandler,
  MapCursorTracker,
  MapFitToDetections,
  MapFitToImagery,
  MapZoomTracker,
} from './MapEventHandlers';
import { fetchDossier, type Dossier } from '../../services/dossier';
import RangeRingsDialog from './RangeRingsDialog';
import type { ActiveLayerMap, BaseLayer } from './LayerPanel';

const CARTO_BASEMAP_URL = '/basemap/{z}/{x}/{y}.png';
const TERRAIN_BASEMAP_URL = '/terrain/{z}/{x}/{y}.png';
const TILE_PROXY_URL = (import.meta as any).env?.VITE_TILE_PROXY_URL || '/tiles';

// The offline basemap + terrain bake stops at z=14 (see scripts/build_offline_basemap.py
// and docs/decisions/why-basemap-z14-cap.md). Past this zoom the overlay would stretch
// one tile across the viewport — unmount it; imagery is the source of truth at high zoom.
export const BASEMAP_OVERLAY_MAX_ZOOM = 14;

export type MapHandle = {
  /** Imperatively pan the map to a detection feature's bounds. */
  panToDetection: (feature: any) => void;
  /** Fly to a lat/lon at an optional zoom (default 13). Used by AI map-control directives. */
  flyTo: (lat: number, lon: number, zoom?: number) => void;
  /** Underlying Leaflet map (for ad-hoc consumers). */
  getMap: () => L.Map | null;
};

export type Props = {
  /* basemap selection */
  activeBaseLayer: BaseLayer;
  layerOpacities: Record<'base' | 'terrain', number>;
  selectedImageryData: any;

  /* detections (geojson + filters + rendering helpers) */
  filteredDetectionsGeoJSON: { features?: any[]; [k: string]: any };
  geomDisplayedDetectionsGeoJSON: { features?: any[]; [k: string]: any };
  detectionsGeoJSON: { features?: any[]; [k: string]: any };
  detectionClassFilter: string | null;
  showDetectionCenterMarkers: boolean;
  detectionIcon: (feature: any) => L.DivIcon;
  getDetectionStyle: (feature: any) => L.PathOptions;
  detectionCanvasRenderer: L.Canvas;
  setSelectedDetection: (feature: any) => void;

  /* track + asset layers */
  activeLayers: ActiveLayerMap;
  data: { static: any[]; tracks: any[] };
  detectionTracks: DetectionTrack[];
  selectedDetectionTrack: DetectionTrack | null;
  setSelectedDetectionTrack: (track: DetectionTrack | null) => void;
  trackColor: (category: string) => string;

  /* prithvi overlays */
  prithviOverlays: Record<string, boolean>;
  prithviGeojson: Record<string, any>;

  /* analytics overlays */
  analyticsResults: Record<string, AnalyticsResponse | null | undefined>;
  pendingPick: AnalyticsPick | null;
  setLastMapClick: (c: any) => void;

  /* satellite overpass (A1) — observer pick + ground-track polyline */
  satPickActive: boolean;
  onSatPick: (lat: number, lon: number) => void;
  satGroundTrack: [number, number][] | null;

  /* basemap countries layer */
  basemapGeoJSON: any;

  /* live map state callbacks */
  setMapBounds: (b: string) => void;
  setMapZoom: (z: number) => void;
  setCursor: (c: any) => void;
  cursor: { lat: number; lon: number };
  mapZoom: number;

  /* draw mode + manual detection */
  drawMode: boolean;
  setDrawMode: (v: boolean | ((cur: boolean) => boolean)) => void;
  drawError: string | null;
  createManualDetection: (bounds: L.LatLngBounds, opts: { object_class: string }) => Promise<void>;

  /* header / overlays */
  visibleDetectionCount: number;
  timelineWindowMinutes: number;
  isLoading: boolean;

  /* ontology helpers */
  categories: DetectionCategoryMap;
  branchById: Map<string, OntologyBranch>;

  /* side-by-side imagery compare (optional) */
  compareImagery?: any | null;
  onClearCompare?: () => void;

  /* product tour (top-toolbar button) */
  onLaunchTour?: () => void;
};

const MapStage = forwardRef<MapHandle, Props>(function MapStage(props, ref) {
  const {
    activeBaseLayer,
    layerOpacities,
    selectedImageryData,
    filteredDetectionsGeoJSON,
    geomDisplayedDetectionsGeoJSON,
    detectionsGeoJSON,
    detectionClassFilter,
    showDetectionCenterMarkers,
    detectionIcon,
    getDetectionStyle,
    detectionCanvasRenderer,
    setSelectedDetection,
    activeLayers,
    data,
    detectionTracks,
    selectedDetectionTrack,
    setSelectedDetectionTrack,
    trackColor,
    prithviOverlays,
    prithviGeojson,
    analyticsResults,
    pendingPick,
    setLastMapClick,
    satPickActive,
    onSatPick,
    satGroundTrack,
    basemapGeoJSON,
    setMapBounds,
    setMapZoom,
    setCursor,
    cursor,
    mapZoom,
    drawMode,
    setDrawMode,
    drawError,
    createManualDetection,
    visibleDetectionCount,
    timelineWindowMinutes,
    isLoading,
    categories,
    branchById,
    compareImagery,
    onClearCompare,
    onLaunchTour,
  } = props;

  const mapInstance = useRef<L.Map | null>(null);
  const [stagedManualBounds, setStagedManualBounds] = useState<L.LatLngBounds | null>(null);

  // Range Rings — session-only tactical overlays. The first map click while
  // `rangeRingMode` is true opens the dialog; the dialog returns a list of
  // radii (km) which are persisted in `rangeRings` until the operator
  // dismisses the page or right-clicks a ring centre.
  const [rangeRingMode, setRangeRingMode] = useState(false);
  const [stagedRingCenter, setStagedRingCenter] = useState<{ lat: number; lon: number } | null>(null);
  const [rangeRings, setRangeRings] = useState<Array<{ id: string; lat: number; lon: number; radiiKm: number[] }>>([]);

  // UX-AUDIT F12 — focus mode collapses the floating map chrome to the
  // viewport edges (a 24 px hover lip remains). Toggled by `F` or the
  // top-right button.
  const [focusMode, setFocusMode] = useState(false);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'f' && e.key !== 'F') return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      if (t && t.isContentEditable) return;
      setFocusMode((f) => !f);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  // B2 — tactical visual modes. A pure CSS filter applied to the Leaflet map
  // pane, cycled by the top-right palette button: DEFAULT → FLIR (thermal) →
  // NVG (night-vision green) → CRT (retro phosphor). Cosmetic only; no data
  // changes, no network. The filter CSS lives in index.css under `.map-vmode-*`.
  const VISUAL_MODES = ['default', 'flir', 'nvg', 'crt'] as const;
  type VisualMode = (typeof VISUAL_MODES)[number];
  const [visualMode, setVisualMode] = useState<VisualMode>('default');
  const cycleVisualMode = () =>
    setVisualMode((m) => VISUAL_MODES[(VISUAL_MODES.indexOf(m) + 1) % VISUAL_MODES.length]);

  // Tier C — offline right-click area dossier. A contextmenu on the map fetches
  // /api/dossier (country from baked ne_countries + nearby detection count) and
  // shows a Leaflet popup at the click point. No internet.
  const [dossier, setDossier] = useState<{ lat: number; lon: number; data: Dossier | null; loading: boolean } | null>(null);

  // Generic GeoJSON overlay subsystem: any component can drop a result layer on
  // the map by dispatching `sentinel:overlay-geojson` (e.g. ChangeDetectionDialog's
  // "Open on map"). Overlays are keyed by id (newest replaces); a `sentinel:overlay-clear`
  // event (or the on-map button) removes them.
  const [overlays, setOverlays] = useState<Array<{ id: string; label: string; featureCollection: any }>>([]);
  useEffect(() => {
    const onOverlay = (e: Event) => {
      const d = (e as CustomEvent).detail;
      if (!d?.featureCollection) return;
      const id = String(d.id || `overlay-${Date.now()}`);
      setOverlays((cur) => [...cur.filter((o) => o.id !== id), { id, label: String(d.label || id), featureCollection: d.featureCollection }]);
      try { const b = L.geoJSON(d.featureCollection).getBounds(); if (b.isValid()) mapInstance.current?.flyToBounds(b.pad(0.2), { animate: true, maxZoom: 15 }); } catch { /* ignore */ }
    };
    const onClear = (e: Event) => {
      const id = (e as CustomEvent).detail?.id;
      setOverlays((cur) => (id ? cur.filter((o) => o.id !== String(id)) : []));
    };
    window.addEventListener('sentinel:overlay-geojson', onOverlay);
    window.addEventListener('sentinel:overlay-clear', onClear);
    return () => {
      window.removeEventListener('sentinel:overlay-geojson', onOverlay);
      window.removeEventListener('sentinel:overlay-clear', onClear);
    };
  }, []);

  const openDossier = (lat: number, lon: number) => {
    setDossier({ lat, lon, data: null, loading: true });
    fetchDossier(lat, lon)
      .then((data) => setDossier({ lat, lon, data, loading: false }))
      .catch(() => setDossier({ lat, lon, data: null, loading: false }));
  };

  // F14 — wire the floating zoom controls to the live Leaflet instance.
  const zoomIn = () => mapInstance.current?.zoomIn();
  const zoomOut = () => mapInstance.current?.zoomOut();
  // Recenter on the operational context — the selected imagery footprint, then
  // the current detections — falling back to the default view only when there's
  // nothing loaded (rather than always jumping back to the Gulf).
  const recenter = () => {
    const map = mapInstance.current;
    if (!map) return;
    try {
      if (selectedImageryData?.footprint_geojson) {
        const geometry = typeof selectedImageryData.footprint_geojson === 'string'
          ? JSON.parse(selectedImageryData.footprint_geojson)
          : selectedImageryData.footprint_geojson;
        const b = L.geoJSON(geometry).getBounds();
        if (b.isValid()) { map.fitBounds(b.pad(0.15), { animate: true, maxZoom: 13 }); return; }
      }
      const feats = detectionsGeoJSON?.features || [];
      if (feats.length) {
        const b = L.geoJSON({ type: 'FeatureCollection', features: feats } as any).getBounds();
        if (b.isValid()) { map.fitBounds(b.pad(0.3), { animate: true, maxZoom: 14 }); return; }
      }
    } catch {
      // fall through to the default view
    }
    map.setView([25.0, 55.0], 6);
  };

  useImperativeHandle(ref, () => ({
    getMap: () => mapInstance.current,
    flyTo: (lat: number, lon: number, zoom?: number) =>
      mapInstance.current?.flyTo([lat, lon], zoom ?? 13, { animate: true }),
    panToDetection: (feature: any) => {
      if (!mapInstance.current || !feature?.geometry) return;
      try {
        const layer = L.geoJSON(feature.geometry as any);
        const b = layer.getBounds();
        if (b.isValid()) {
          mapInstance.current.flyToBounds(b.pad(0.4), { animate: true, maxZoom: 16 });
        }
      } catch {
        // ignore invalid geometry
      }
    },
  }), []);

  return (
    <section
      className={`relative flex min-h-0 min-w-0 flex-col bg-sentinel-bg${focusMode ? ' map-focus-on' : ''}`}
      style={{ position: 'absolute', inset: 0 }}
    >
      <div className={`relative min-h-0 flex-1 map-vmode-${visualMode}`}>
        <MapContainer
          center={[25.0, 55.0]}
          zoom={6}
          style={{ height: '100%', width: '100%', background: '#122231' }}
          zoomControl={false}
          ref={(m: any) => {
            mapInstance.current = m?.leafletElement || m || null;
          }}
        >
          <MapBoundsUpdater onBoundsChange={setMapBounds} />
          <MapCursorTracker onCursorChange={setCursor} />
          <MapZoomTracker onZoomChange={setMapZoom} />
          <AnalyticsPickHandler<AnalyticsPick>
            pickFor={pendingPick}
            onPicked={(lat, lon, pickFor) => setLastMapClick({ lat, lon, pickFor })}
          />
          <AnalyticsPickHandler<string>
            pickFor={satPickActive ? 'satellites.observer' : null}
            onPicked={(lat, lon) => onSatPick(lat, lon)}
          />
          <DrawRectHandler
            enabled={drawMode}
            onFinish={(bounds) => {
              setStagedManualBounds(bounds);
              setDrawMode(false);
            }}
          />
          <MapClickPicker
            enabled={rangeRingMode && !stagedRingCenter}
            onPicked={(lat, lon) => {
              setStagedRingCenter({ lat, lon });
              setRangeRingMode(false);
            }}
          />
          <MapContextHandler onContext={openDossier} />
          <MapFitToImagery imagery={selectedImageryData} />
          <MapFitToDetections geojson={filteredDetectionsGeoJSON} filterKey={detectionClassFilter} />

          {/* Basemap composition — see decisions/why-basemap-overlay-composition.md.
              Imagery is the analyst's ground truth: it renders at the bottom
              (zIndex 200) and the cartographic basemap is a reference overlay
              on top (zIndex 300). SAT mode = imagery alone; BASE/TERRAIN add
              the reference overlay; the opacity slider fades the overlay. */}

          {/* 1. SAT imagery — ground truth, bottom of the stack, full opacity.
                 Rendered whenever imagery is loaded, in every mode. */}
          {compareImagery && compareImagery.file_path && (
            <SwipeControl
              url={`${TILE_PROXY_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.webp?url=${encodeURIComponent(compareImagery.file_path)}`}
              maxNativeZoom={compareImagery.native_max_zoom ?? 18}
              label={compareImagery.name || `Pass ${compareImagery.id}`}
              onClose={() => onClearCompare?.()}
            />
          )}

          {activeLayers.satellite && selectedImageryData && (
            <TileLayer
              key={`sat-${selectedImageryData.id}`}
              url={`${TILE_PROXY_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.webp?url=${encodeURIComponent(selectedImageryData.file_path)}`}
              opacity={1}
              maxZoom={22}
              // Cap upstream tile fetches at the COG's true pixel resolution.
              // `native_max_zoom` comes from /api/imagery (per-pass GSD); past
              // it Leaflet upscales the cached tile client-side instead of
              // hammering TiTiler for upsampled tiles that aren't any sharper.
              // 18 is the conservative fallback for passes ingested before the
              // backend started reporting the field.
              maxNativeZoom={selectedImageryData.native_max_zoom ?? 18}
              // Keep a wider ring of tiles alive across zoom/pan so the map
              // does not degrade to bare tiles mid-gesture (default is 2).
              keepBuffer={6}
              // Don't chase intermediate zoom levels during a pinch/scroll —
              // those requests are thrown away the moment the gesture lands.
              updateWhenZooming={false}
              zIndex={200}
            />
          )}

          {/* 2. Cartographic fallback — only when there is no imagery, so SAT
                 mode never renders an empty stage. */}
          {!selectedImageryData && (
            <TileLayer
              key="base-fallback"
              url={activeBaseLayer === 'terrain' ? TERRAIN_BASEMAP_URL : CARTO_BASEMAP_URL}
              subdomains={activeBaseLayer === 'terrain' ? undefined : 'abcd'}
              maxZoom={20}
              maxNativeZoom={10}
              opacity={1}
              zIndex={100}
              attribution={
                activeBaseLayer === 'terrain'
                  ? '&copy; OpenStreetMap &copy; OpenTopoMap (CC-BY-SA)'
                  : '&copy; OpenStreetMap &copy; CARTO'
              }
            />
          )}

          {/* 3. Reference overlay — Carto/Terrain ABOVE the imagery (zIndex 300
                 > SAT zIndex 200) when the analyst picks BASE/TERRAIN. The
                 opacity slider drives how much of the imagery shows through.
                 Unmounted past z=BASEMAP_OVERLAY_MAX_ZOOM (the offline bake
                 ceiling) so the overlay never stretches one tile across the
                 viewport. */}
          {selectedImageryData && mapZoom <= BASEMAP_OVERLAY_MAX_ZOOM && activeBaseLayer === 'base' && (
            <TileLayer
              key="overlay-carto"
              url={CARTO_BASEMAP_URL}
              subdomains="abcd"
              maxZoom={BASEMAP_OVERLAY_MAX_ZOOM}
              maxNativeZoom={BASEMAP_OVERLAY_MAX_ZOOM}
              opacity={layerOpacities.base}
              zIndex={300}
              attribution="&copy; OpenStreetMap &copy; CARTO"
            />
          )}
          {selectedImageryData && mapZoom <= BASEMAP_OVERLAY_MAX_ZOOM && activeBaseLayer === 'terrain' && (
            <TileLayer
              key="overlay-terrain"
              url={TERRAIN_BASEMAP_URL}
              maxZoom={BASEMAP_OVERLAY_MAX_ZOOM}
              maxNativeZoom={BASEMAP_OVERLAY_MAX_ZOOM}
              opacity={layerOpacities.terrain}
              zIndex={300}
              attribution="&copy; OpenStreetMap &copy; OpenTopoMap (CC-BY-SA)"
            />
          )}

          {/* Prithvi overlays — hatched fills coloured per kind */}
          {(['flood', 'burn', 'crops'] as const).map((kind) => {
            if (!prithviOverlays[kind]) return null;
            const dataKind = prithviGeojson[kind];
            if (!dataKind || !dataKind.features || dataKind.features.length === 0) return null;
            const color =
              kind === 'flood' ? '#4ea1ff'
              : kind === 'burn' ? '#c46a30'
              : '#3dd68c';
            return (
              <GeoJSON
                key={`prithvi-${kind}`}
                data={dataKind as any}
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

          {activeLayers.graticule && <MgrsGraticule />}

          {rangeRings.flatMap((ring) => [
            ...ring.radiiKm.map((rKm, idx) => {
                const palette = ['#ffb14a', '#ff7a1a', '#ff5577'];
                const color = palette[Math.min(idx, palette.length - 1)];
              return (
                <Circle
                  key={`ring-${ring.id}-${rKm}`}
                  center={[ring.lat, ring.lon]}
                  radius={rKm * 1000}
                  pathOptions={{
                    color,
                    weight: 1.5,
                    opacity: 0.85,
                    fill: false,
                    dashArray: idx === 0 ? undefined : '4 4',
                  }}
                  eventHandlers={{
                    add: (e) => {
                      (e.target as L.Circle).bindTooltip(
                        `Range ${rKm.toFixed(1)} km`,
                        { sticky: true, opacity: 0.9 },
                      );
                    },
                  }}
                />
              );
            }),
            <CircleMarker
              key={`ring-center-${ring.id}`}
              center={[ring.lat, ring.lon]}
              radius={4}
              pathOptions={{
                color: '#ffb14a',
                weight: 1,
                fillColor: '#ffb14a',
                fillOpacity: 1,
              }}
              eventHandlers={{
                contextmenu: () => {
                  setRangeRings((cur) => cur.filter((r) => r.id !== ring.id));
                },
                add: (e) => {
                  (e.target as L.CircleMarker).bindTooltip(
                    `Range ring center · right-click to remove`,
                    { sticky: true, opacity: 0.9 },
                  );
                },
              }}
            />,
          ])}

          {activeLayers.borders && (
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
                const p = feature?.properties || {};
                const name = p.admin || p.name || p.iso_a3;
                if (name) layer.bindTooltip(String(name), { sticky: true, direction: 'top', opacity: 0.92 });
              }}
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
            const categoryMeta = categoryFor(category, categories);
            const p = feature.properties || {};
            return (
              <Marker
                key={`det-marker-${p.id || p.class}-${badgePosition[0]}-${badgePosition[1]}`}
                position={badgePosition}
                icon={detectionIcon(feature)}
                eventHandlers={{ click: () => setSelectedDetection(feature) }}
              >
                <Popup className="sentinel-popup">
                  <div className="border border-sentinel-line bg-sentinel-panel p-2 text-slate-200">
                    <div className="mb-2 flex items-center gap-2 border-b border-sentinel-line pb-1 text-xs font-bold uppercase tracking-wider">
                      <span style={{ color: categoryMeta.color }}><DetectionSubclassIcon iconKey={p.icon_key ?? null} label={p.original_class || p.class || p.label} category={category} branchById={branchById} /></span>
                      <span>{detectionDisplayLabel(p) || detectionClassLabel(p.class)}</span>
                    </div>
                    <div className="font-mono text-[11px] text-sentinel-muted">
                      CAT <span style={{ color: categoryMeta.color }}>{categoryMeta.label}</span><br />
                      PARENT {p.parent_class || p.class || 'unknown'}<br />
                      ORIG {p.original_class || p.metadata?.original_class || p.class || 'unknown'}<br />
                      LABEL_QUALITY {labelQuality(p) || 'inferred'}<br />
                      CONF {Math.round(Number(p.confidence || 0) * 100)}%
                    </div>
                  </div>
                </Popup>
              </Marker>
            );
          })}

          {activeLayers.detections && !showDetectionCenterMarkers && filteredDetectionsGeoJSON.features?.map((feature: any) => {
            const center = detectionCenter(feature);
            if (!center) return null;
            const category = detectionCategoryForFeature(feature);
            const categoryMeta = categoryFor(category, categories);
            const p = feature.properties || {};
            return (
              <CircleMarker
                key={`det-dot-${p.id || p.class}-${center[0]}-${center[1]}`}
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
                      <span style={{ color: categoryMeta.color }}><DetectionSubclassIcon iconKey={p.icon_key ?? null} label={p.original_class || p.class || p.label} category={category} branchById={branchById} /></span>
                      <span>{detectionDisplayLabel(p) || detectionClassLabel(p.class)}</span>
                    </div>
                    <div className="font-mono text-[11px] text-sentinel-muted">
                      CAT <span style={{ color: categoryMeta.color }}>{categoryMeta.label}</span><br />
                      PARENT {p.parent_class || p.class || 'unknown'}<br />
                      ORIG {p.original_class || p.metadata?.original_class || p.class || 'unknown'}<br />
                      LABEL_QUALITY {labelQuality(p) || 'inferred'}<br />
                      CONF {Math.round(Number(p.confidence || 0) * 100)}%
                    </div>
                  </div>
                </Popup>
              </CircleMarker>
            );
          })}

          {/* Position-uncertainty halos. Render a faint circle at each
              detection's centroid with radius = position_uncertainty_m when
              zoomed in tight (z>=14). Skipped when there are too many visible
              features. */}
          {activeLayers.detections
            && mapZoom >= 14
            && filteredDetectionsGeoJSON.features
            && filteredDetectionsGeoJSON.features.length > 0
            && filteredDetectionsGeoJSON.features.length <= 400
            && filteredDetectionsGeoJSON.features.map((feature: any) => {
              const center = detectionCenter(feature);
              const uncertainty = Number(feature?.properties?.position_uncertainty_m);
              if (!center || !Number.isFinite(uncertainty) || uncertainty <= 0) return null;
              const p = feature.properties || {};
              return (
                <Circle
                  key={`uncert-${p.id}-${center[0]}-${center[1]}`}
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

          {/* Detection bounding boxes — one <Polygon> per feature. The
              per-feature map mirrors the icon-marker layer above; it is
              reactive to data changes and a single bad geometry only skips
              that one box instead of silently killing the whole layer (the
              failure mode of the previous <GeoJSON> canvas layer). Uses the
              map's default SVG renderer — the shared L.canvas() renderer never
              painted (it gated both the old <GeoJSON> box layer and this one),
              so it is deliberately not used here. */}
          {activeLayers.detections && geomDisplayedDetectionsGeoJSON.features?.map((feature: any) => {
            const positions = geojsonToLatLngs(feature?.geometry);
            if (!positions) return null;
            const p = feature.properties || {};
            return (
              <Polygon
                key={`det-box-${p.id ?? p.class}`}
                positions={positions}
                pathOptions={getDetectionStyle(feature)}
                eventHandlers={{ click: () => setSelectedDetection(feature) }}
              />
            );
          })}

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
                    <Polyline positions={positions} pathOptions={{ color, weight: 6, opacity: 0.18 }} />
                  )}
                  {track.pinned && (
                    <Polyline positions={positions} pathOptions={{ color: '#ffffff', weight: isSelected ? 6 : 4, opacity: 0.25 }} />
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

          {activeLayers.los && analyticsResults.los?.result && (() => {
            const fc: any = analyticsResults.los.result;
            const allFeatures: any[] = Array.isArray(fc?.features) ? fc.features : [];
            const lineFeatures = allFeatures.filter((f) => f?.properties?.role !== 'obstruction');
            const obstructionFeatures = allFeatures.filter((f) => f?.properties?.role === 'obstruction'
              && f?.geometry?.type === 'Point');
            const lineCollection = { type: 'FeatureCollection', features: lineFeatures };
            return (
              <>
                <GeoJSON
                  key={`los-line-${analyticsResults.los.job.id}`}
                  data={lineCollection as any}
                  style={(feature) => {
                    const visible = !!feature?.properties?.visible;
                    return {
                      color: visible ? '#5ee0a0' : '#ff5577',
                      weight: 3,
                      opacity: 0.95,
                      dashArray: visible ? undefined : '6 4',
                    };
                  }}
                  onEachFeature={(feature, layer) => {
                    const p = feature?.properties || {};
                    const tip = `LOS · ${p.visible ? 'visible' : 'blocked'} · clearance ${Number(p.clearance_m || 0).toFixed(1)} m`;
                    layer.bindTooltip(tip, { sticky: true, opacity: 0.92 });
                  }}
                />
                {obstructionFeatures.map((feat, idx) => {
                  const [lon, lat] = feat.geometry.coordinates || [0, 0];
                  const p = feat.properties || {};
                  const elev = typeof p.elevation_m === 'number' ? `${p.elevation_m.toFixed(0)} m` : '—';
                  const clearance = typeof p.clearance_m === 'number' ? `${p.clearance_m.toFixed(1)} m` : '—';
                  const dist = typeof p.distance_m === 'number'
                    ? (p.distance_m >= 1000 ? `${(p.distance_m / 1000).toFixed(2)} km` : `${p.distance_m.toFixed(0)} m`)
                    : '—';
                  return (
                    <CircleMarker
                      key={`los-obs-${analyticsResults.los?.job.id}-${idx}`}
                      center={[lat, lon]}
                      radius={5}
                      pathOptions={{
                        color: '#ff5577',
                        weight: 1,
                        fillColor: '#ff5577',
                        fillOpacity: 0.85,
                      }}
                      eventHandlers={{
                        add: (e) => {
                          (e.target as L.CircleMarker).bindTooltip(
                            `OBSTRUCTION · ELEV ${elev} · BLOCKED ${clearance} · ${dist} out`,
                            { sticky: true, opacity: 0.92, direction: 'top' },
                          );
                        },
                      }}
                    />
                  );
                })}
              </>
            );
          })()}

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

          {activeLayers.isochrone && analyticsResults.isochrone?.result && (
            <GeoJSON
              key={`isochrone-${analyticsResults.isochrone.job.id}`}
              data={analyticsResults.isochrone.result as any}
              style={() => ({
                color: '#ffb14a',
                weight: 1.5,
                opacity: 0.9,
                fillColor: '#ffb14a',
                fillOpacity: 0.18,
              })}
              onEachFeature={(_feature, layer) => {
                const p: any = (analyticsResults.isochrone?.result as any)?.features?.[0]?.properties || {};
                const min = p.minutes != null ? `${p.minutes} min` : 'isochrone';
                const mode = (analyticsResults.isochrone?.result as any)?.mode;
                const tip = mode === 'osrm'
                  ? `Isochrone · ${min} · job ${analyticsResults.isochrone?.job.id}`
                  : `Isochrone · demo fixture · job ${analyticsResults.isochrone?.job.id}`;
                layer.bindTooltip(tip, { sticky: true, opacity: 0.92 });
              }}
            />
          )}

          {activeLayers.odflows && analyticsResults.odflows?.result && (
            <GeoJSON
              key={`odflows-${analyticsResults.odflows.job.id}`}
              data={analyticsResults.odflows.result as any}
              style={(feature) => {
                const flow = Number(feature?.properties?.flow ?? feature?.properties?.weight ?? 1);
                return {
                  color: '#c87aff',
                  weight: Math.min(8, 1.5 + flow),
                  opacity: 0.85,
                };
              }}
              onEachFeature={(feature, layer) => {
                const p = feature?.properties || {};
                const flow = p.flow ?? p.weight ?? 1;
                layer.bindTooltip(`OD flow · ${flow}`, { sticky: true, opacity: 0.92 });
              }}
            />
          )}

          {/* Satellite sub-satellite ground track (A1). Stored as [lon, lat];
              Leaflet wants [lat, lon]. Amber dashed polyline. */}
          {satGroundTrack && satGroundTrack.length > 1 && (
            <Polyline
              key={`sat-track-${satGroundTrack.length}-${satGroundTrack[0][0]}`}
              positions={satGroundTrack.map(([lon, lat]) => [lat, lon] as [number, number])}
              pathOptions={{ color: '#ffb020', weight: 2, opacity: 0.85, dashArray: '6, 5' }}
            />
          )}

          {/* Tier C — offline area dossier popup at the right-clicked point. */}
          {dossier && (
            <Popup
              position={[dossier.lat, dossier.lon]}
              eventHandlers={{ remove: () => setDossier(null) }}
              className="sentinel-popup"
            >
              <div className="border border-sentinel-line bg-sentinel-panel p-2 text-slate-200" style={{ minWidth: 180 }}>
                <div className="mb-2 border-b border-sentinel-line pb-1 text-xs font-bold uppercase tracking-wider">
                  Area dossier
                </div>
                {dossier.loading ? (
                  <div className="font-mono text-[11px] text-sentinel-muted">Loading…</div>
                ) : dossier.data ? (
                  <div className="space-y-1 font-mono text-[11px]">
                    <div>
                      <span className="text-sentinel-muted">COUNTRY </span>
                      <span className="text-slate-100">{dossier.data.country?.name ?? '— (international waters)'}</span>
                    </div>
                    {dossier.data.country?.iso_a3 && (
                      <div><span className="text-sentinel-muted">ISO3 </span>{dossier.data.country.iso_a3}</div>
                    )}
                    {dossier.data.country?.pop_est != null && (
                      <div><span className="text-sentinel-muted">POP </span>{dossier.data.country.pop_est.toLocaleString()}</div>
                    )}
                    {dossier.data.country?.gdp_md_est != null && (
                      <div><span className="text-sentinel-muted">GDP </span>${dossier.data.country.gdp_md_est.toLocaleString()}M</div>
                    )}
                    <div><span className="text-sentinel-muted">DETS ≤25km </span>{dossier.data.detections_within_25km}</div>
                    <div className="text-sentinel-muted">{dossier.lat.toFixed(4)}, {dossier.lon.toFixed(4)}</div>
                  </div>
                ) : (
                  <div className="font-mono text-[11px] text-sentinel-crit">Dossier unavailable</div>
                )}
              </div>
            </Popup>
          )}

          {/* Generic GeoJSON result overlays (e.g. change-detection) */}
          {overlays.map((ov) => (
            <GeoJSON
              key={ov.id}
              data={ov.featureCollection}
              style={(feature?: any) => {
                const score = Number(feature?.properties?.score ?? feature?.properties?.confidence ?? 0.6);
                return {
                  color: '#ff3bd0',
                  weight: 1.5,
                  opacity: 0.95,
                  fillColor: '#ff3bd0',
                  fillOpacity: 0.18 + Math.min(0.5, Math.max(0, score) * 0.5),
                };
              }}
            />
          ))}
        </MapContainer>

        {/* Floating overlays drawn on top of the map */}
        <div className="pointer-events-none absolute inset-0">
          {overlays.length > 0 && (
            <div className="pointer-events-auto absolute right-2 top-10 flex flex-col gap-1">
              {overlays.map((ov) => (
                <button
                  key={ov.id}
                  type="button"
                  onClick={() => setOverlays((cur) => cur.filter((o) => o.id !== ov.id))}
                  title="Remove this overlay"
                  className="flex items-center gap-1.5 border border-sentinel-line-2 bg-sentinel-panel px-2 py-1 font-mono text-[10px] text-sentinel-text hover:text-sentinel-accent"
                >
                  <span className="inline-block h-2 w-2" style={{ background: '#ff3bd0' }} />
                  {ov.label}
                  <X className="h-3 w-3" />
                </button>
              ))}
            </div>
          )}
          <div className="sentinel-grid" />
          <div className="map-focus-collapsible map-focus-left absolute left-2 top-2 font-mono text-[10px] tracking-wider text-sentinel-muted">WGS84 / MERCATOR / LIVE COP</div>
          <div className="map-focus-collapsible map-focus-right absolute right-2 top-2 font-mono text-[10px] tracking-wider text-sentinel-muted">AOR / CURRENT VIEW</div>
          <div className="absolute left-1/2 top-14 -translate-x-1/2 border border-sentinel-line-2 bg-sentinel-panel px-3 py-1 font-mono text-[11px]">
            <span className="text-sentinel-accent">{visibleDetectionCount}</span>
            <span className="text-sentinel-muted"> / {detectionsGeoJSON.features?.length || 0} detections / last {timelineWindowMinutes}m</span>
            {visibleDetectionCount > 0 && <span className="text-sentinel-muted"> / hover labels</span>}
          </div>
          <div className="map-focus-collapsible map-focus-left absolute left-3 bottom-4 border border-sentinel-line-2 bg-sentinel-panel px-3 py-2 font-mono text-[11px]">
            <div className="sentinel-label">cursor</div>
            <div>LAT {cursor.lat.toFixed(3).padStart(8, ' ')} deg</div>
            <div>LON {cursor.lon.toFixed(3).padStart(8, ' ')} deg</div>
            <div className="mt-1 text-sentinel-muted">
              MGRS <span className="text-slate-200 font-mono">
                {(() => {
                  try { return mgrsForward([cursor.lon, cursor.lat], 5); }
                  catch { return 'n/a'; }
                })()}
              </span>
            </div>
          </div>
          <div className="map-focus-collapsible map-focus-right absolute right-3 bottom-4 border border-sentinel-line-2 bg-sentinel-panel px-3 py-2 font-mono text-[11px]">
            <div className="sentinel-label">scale</div>
            <div className="flex items-center gap-2">
              <span className="h-px w-20 bg-slate-200" />
              <span>500 km</span>
            </div>
          </div>
          {/* F14 — 32×32 px controls, wired to the live map, keyboard hints
              in tooltips. F12 — the focus toggle lives in this cluster.
              Anchored at the viewport's bottom-right edge, overlapping the
              SelectionPanel's rightmost strip (cluster width 32 px >
              panel right-margin 14 px). z-[600] keeps it above the panel
              (z 500). Position is static — when the panel collapses to a
              36 px rail the cluster covers the rail's left ~22 px while
              the remaining ~14 px on the right stays clickable for
              re-expand. */}
          <div
            className="absolute z-[600] flex flex-col border border-sentinel-line-2 bg-sentinel-panel"
            style={{
              bottom: 14,
              right: 4,
            }}
          >
            <button
              type="button" onClick={zoomIn}
              data-tour="zoom-in"
              title="Zoom in (=)" aria-label="Zoom in"
              className="pointer-events-auto grid h-8 w-8 place-items-center border-b border-sentinel-line text-sentinel-muted hover:text-white"
            >
              <Plus className="h-4 w-4" />
            </button>
            <button
              type="button" onClick={zoomOut}
              data-tour="zoom-out"
              title="Zoom out (-)" aria-label="Zoom out"
              className="pointer-events-auto grid h-8 w-8 place-items-center border-b border-sentinel-line text-sentinel-muted hover:text-white"
            >
              <Minus className="h-4 w-4" />
            </button>
            <button
              type="button" onClick={recenter}
              data-tour="recenter"
              title="Recenter (0)" aria-label="Recenter map"
              className="pointer-events-auto grid h-8 w-8 place-items-center border-b border-sentinel-line text-sentinel-muted hover:text-white"
            >
              <Crosshair className="h-4 w-4" />
            </button>
            <button
              type="button" onClick={() => setFocusMode((f) => !f)}
              data-tour="focus-mode"
              title="Focus map (F)" aria-label="Toggle focus mode" aria-pressed={focusMode}
              className={`pointer-events-auto grid h-8 w-8 place-items-center border-b border-sentinel-line ${
                focusMode ? 'text-sentinel-accent' : 'text-sentinel-muted hover:text-white'
              }`}
            >
              {focusMode ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
            </button>
            <button
              type="button" onClick={cycleVisualMode}
              data-tour="visual-mode"
              title={`Visual mode: ${visualMode.toUpperCase()} (click to cycle DEFAULT/FLIR/NVG/CRT)`}
              aria-label="Cycle tactical visual mode"
              className={`pointer-events-auto grid h-8 w-8 place-items-center ${
                visualMode !== 'default' ? 'text-sentinel-accent' : 'text-sentinel-muted hover:text-white'
              }`}
            >
              <Palette className="h-4 w-4" />
            </button>
          </div>

          {/* Top-center action bar — Draw / Range ring / Product Tour. Layer-
              display state (GEOM box mode, Prithvi overlays, tracks visibility)
              lives in the LayerPanel Overlays section, not here. */}
          <div className="absolute left-1/2 top-3 z-[500] -translate-x-1/2 pointer-events-auto flex flex-row items-center gap-2">
            <button
              type="button"
              data-tour="draw-object"
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
            <button
              type="button"
              data-tour="range-ring"
              onClick={() => setRangeRingMode((v) => !v)}
              title={rangeRingMode ? 'Cancel range-ring placement' : 'Place range rings around a point'}
              className={`flex items-center gap-2 border px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest transition ${
                rangeRingMode
                  ? 'border-sentinel-accent bg-sentinel-accent/15 text-sentinel-accent'
                  : 'border-sentinel-line-2 bg-sentinel-panel text-sentinel-text hover:border-sentinel-accent/60'
              }`}
            >
              <Target className="h-3.5 w-3.5" />
              {rangeRingMode ? 'Cancel ring' : 'Range ring'}
            </button>
            {onLaunchTour && (
              <button
                type="button"
                data-tour="product-tour-btn"
                onClick={onLaunchTour}
                title="Take a guided tour of the Map workspace"
                className="flex items-center gap-2 border px-3 py-1.5 font-mono text-[11px] uppercase tracking-widest border-sentinel-line-2 bg-sentinel-panel text-sentinel-text hover:border-sentinel-accent/60"
              >
                <HelpCircle className="h-3.5 w-3.5" />
                Product Tour
              </button>
            )}
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
      <ManualDetectionDialog
        bounds={stagedManualBounds}
        onConfirm={async (cls) => {
          const bounds = stagedManualBounds;
          setStagedManualBounds(null);
          if (bounds) {
            await createManualDetection(bounds, { object_class: cls });
          }
        }}
        onCancel={() => setStagedManualBounds(null)}
      />
      <RangeRingsDialog
        center={stagedRingCenter}
        onConfirm={(radiiKm) => {
          if (stagedRingCenter) {
            const id = `${stagedRingCenter.lat.toFixed(5)}-${stagedRingCenter.lon.toFixed(5)}-${Date.now()}`;
            setRangeRings((cur) => [...cur, { id, lat: stagedRingCenter.lat, lon: stagedRingCenter.lon, radiiKm }]);
          }
          setStagedRingCenter(null);
        }}
        onCancel={() => setStagedRingCenter(null)}
      />
    </section>
  );
});

export default MapStage;

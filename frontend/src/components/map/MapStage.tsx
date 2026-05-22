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
import {
  Crosshair,
  Eye,
  EyeOff,
  Minus,
  Plus,
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
  ZoomControl,
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
  geojsonToLatLngs,
  relativeTime,
  trackDashArray,
  type DetectionTrack,
} from './_helpers';
import { DetectionSubclassIcon, blueIcon, createIcon, emeraldIcon, redIcon } from './_icons';
import type { AnalyticsPick } from './AnalyticsToolsPanel';
import type { AnalyticsResponse } from '../../services/analytics';
import {
  AnalyticsPickHandler,
  DrawRectHandler,
  MapBoundsUpdater,
  MapCursorTracker,
  MapFitToDetections,
  MapFitToImagery,
  MapZoomTracker,
} from './MapEventHandlers';
import type { ActiveLayerMap, BaseLayer } from './LayerPanel';

const CARTO_BASEMAP_URL = '/basemap/{z}/{x}/{y}.png';
const TERRAIN_BASEMAP_URL = '/terrain/{z}/{x}/{y}.png';
const TILE_PROXY_URL = (import.meta as any).env?.VITE_TILE_PROXY_URL || '/tiles';

export type MapHandle = {
  /** Imperatively pan the map to a detection feature's bounds. */
  panToDetection: (feature: any) => void;
  /** Underlying Leaflet map (for ad-hoc consumers). */
  getMap: () => L.Map | null;
};

export type Props = {
  /* basemap selection */
  activeBaseLayer: BaseLayer;
  layerOpacities: Record<BaseLayer, number>;
  selectedImageryData: any;

  /* detections (geojson + filters + rendering helpers) */
  filteredDetectionsGeoJSON: { features?: any[]; [k: string]: any };
  geomDisplayedDetectionsGeoJSON: { features?: any[]; [k: string]: any };
  detectionsGeoJSON: { features?: any[]; [k: string]: any };
  detectionClassFilter: string | null;
  bboxMode: 'hbb' | 'obb' | 'mask';
  setBboxMode: (m: 'hbb' | 'obb' | 'mask') => void;
  showDetectionCenterMarkers: boolean;
  detectionIcon: (feature: any) => L.DivIcon;
  getDetectionStyle: (feature: any) => L.PathOptions;
  detectionCanvasRenderer: L.Canvas;
  setSelectedDetection: (feature: any) => void;

  /* track + asset layers */
  activeLayers: ActiveLayerMap;
  setActiveLayers: React.Dispatch<React.SetStateAction<ActiveLayerMap>>;
  data: { static: any[]; tracks: any[] };
  detectionTracks: DetectionTrack[];
  selectedDetectionTrack: DetectionTrack | null;
  setSelectedDetectionTrack: (track: DetectionTrack | null) => void;
  trackColor: (category: string) => string;

  /* prithvi overlays */
  prithviOverlays: Record<string, boolean>;
  setPrithviOverlays: (updater: any) => void;
  prithviGeojson: Record<string, any>;

  /* analytics overlays */
  analyticsResults: Record<string, AnalyticsResponse | null | undefined>;
  pendingPick: AnalyticsPick | null;
  setLastMapClick: (c: any) => void;

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
    bboxMode,
    setBboxMode,
    showDetectionCenterMarkers,
    detectionIcon,
    getDetectionStyle,
    detectionCanvasRenderer,
    setSelectedDetection,
    activeLayers,
    setActiveLayers,
    data,
    detectionTracks,
    selectedDetectionTrack,
    setSelectedDetectionTrack,
    trackColor,
    prithviOverlays,
    setPrithviOverlays,
    prithviGeojson,
    analyticsResults,
    pendingPick,
    setLastMapClick,
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
  } = props;

  const mapInstance = useRef<L.Map | null>(null);

  // The SAT base layer is rendered as an overlay on top of BASE/TERRAIN; this
  // ref records the last cartographic base the user picked so we can keep it
  // visible underneath when activeBaseLayer === 'sat'.
  const lastNonSatBaseRef = useRef<'base' | 'terrain'>(
    activeBaseLayer === 'terrain' ? 'terrain' : 'base',
  );
  useEffect(() => {
    if (activeBaseLayer === 'base' || activeBaseLayer === 'terrain') {
      lastNonSatBaseRef.current = activeBaseLayer;
    }
  }, [activeBaseLayer]);
  const effectiveBase: 'base' | 'terrain' =
    activeBaseLayer === 'sat' ? lastNonSatBaseRef.current : activeBaseLayer;

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

  // F14 — wire the floating zoom controls to the live Leaflet instance.
  const zoomIn = () => mapInstance.current?.zoomIn();
  const zoomOut = () => mapInstance.current?.zoomOut();
  const recenter = () => mapInstance.current?.setView([25.0, 55.0], 6);

  useImperativeHandle(ref, () => ({
    getMap: () => mapInstance.current,
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
      <div className="relative min-h-0 flex-1">
        <MapContainer
          center={[25.0, 55.0]}
          zoom={6}
          style={{ height: '100%', width: '100%', background: '#122231' }}
          zoomControl={false}
          ref={(m: any) => {
            mapInstance.current = m?.leafletElement || m || null;
          }}
        >
          <ZoomControl position="bottomright" />
          <MapBoundsUpdater onBoundsChange={setMapBounds} />
          <MapCursorTracker onCursorChange={setCursor} />
          <MapZoomTracker onZoomChange={setMapZoom} />
          <AnalyticsPickHandler<AnalyticsPick>
            pickFor={pendingPick}
            onPicked={(lat, lon, pickFor) => setLastMapClick({ lat, lon, pickFor })}
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

          {effectiveBase === 'base' && (
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
          {effectiveBase === 'terrain' && (
            <TileLayer
              key="base-terrain"
              url={TERRAIN_BASEMAP_URL}
              maxZoom={20}
              maxNativeZoom={10}
              opacity={layerOpacities.terrain}
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
                const p = feature?.properties || {};
                const name = p.admin || p.name || p.iso_a3;
                if (name) layer.bindTooltip(String(name), { sticky: true, direction: 'top', opacity: 0.92 });
              }}
            />
          )}

          {activeBaseLayer === 'sat' && activeLayers.satellite && selectedImageryData && (
            <TileLayer
              key={`sat-${selectedImageryData.id}`}
              url={`${TILE_PROXY_URL}/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.webp?url=${encodeURIComponent(selectedImageryData.file_path)}`}
              opacity={layerOpacities.sat}
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
                      <span>{p.label || detectionClassLabel(p.class)}</span>
                    </div>
                    <div className="font-mono text-[11px] text-sentinel-muted">
                      CAT <span style={{ color: categoryMeta.color }}>{categoryMeta.label}</span><br />
                      PARENT {p.parent_class || p.class || 'unknown'}<br />
                      ORIG {p.original_class || p.metadata?.original_class || p.class || 'unknown'}<br />
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
                      <span>{p.label || detectionClassLabel(p.class)}</span>
                    </div>
                    <div className="font-mono text-[11px] text-sentinel-muted">
                      CAT <span style={{ color: categoryMeta.color }}>{categoryMeta.label}</span><br />
                      PARENT {p.parent_class || p.class || 'unknown'}<br />
                      ORIG {p.original_class || p.metadata?.original_class || p.class || 'unknown'}<br />
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

        {/* Floating overlays drawn on top of the map */}
        <div className="pointer-events-none absolute inset-0">
          <div className="sentinel-grid" />
          <div className="map-focus-collapsible map-focus-left absolute left-2 top-2 font-mono text-[10px] tracking-wider text-sentinel-muted">WGS84 / MERCATOR / LIVE COP</div>
          <div className="map-focus-collapsible map-focus-right absolute right-2 top-2 font-mono text-[10px] tracking-wider text-sentinel-muted">AOR / CURRENT VIEW</div>
          <div className="absolute left-1/2 top-8 -translate-x-1/2 border border-sentinel-line-2 bg-sentinel-panel px-3 py-1 font-mono text-[11px]">
            <span className="text-sentinel-accent">{visibleDetectionCount}</span>
            <span className="text-sentinel-muted"> / {detectionsGeoJSON.features?.length || 0} detections / last {timelineWindowMinutes}m</span>
            {visibleDetectionCount > 0 && <span className="text-sentinel-muted"> / hover labels</span>}
          </div>
          <div className="map-focus-collapsible map-focus-left absolute left-3 bottom-4 border border-sentinel-line-2 bg-sentinel-panel px-3 py-2 font-mono text-[11px]">
            <div className="sentinel-label">cursor</div>
            <div>LAT {cursor.lat.toFixed(3).padStart(8, ' ')} deg</div>
            <div>LON {cursor.lon.toFixed(3).padStart(8, ' ')} deg</div>
            <div className="mt-1 text-sentinel-muted">MGRS <span className="text-slate-200">AUTO</span></div>
          </div>
          <div className="map-focus-collapsible map-focus-right absolute right-3 bottom-4 border border-sentinel-line-2 bg-sentinel-panel px-3 py-2 font-mono text-[11px]">
            <div className="sentinel-label">scale</div>
            <div className="flex items-center gap-2">
              <span className="h-px w-20 bg-slate-200" />
              <span>500 km</span>
            </div>
          </div>
          {/* F14 — 32×32 px controls, wired to the live map, keyboard hints
              in tooltips. F12 — the focus toggle lives in this cluster. */}
          <div className="absolute right-3 top-10 flex flex-col border border-sentinel-line-2 bg-sentinel-panel">
            <button
              type="button" onClick={zoomIn}
              title="Zoom in (=)" aria-label="Zoom in"
              className="pointer-events-auto grid h-8 w-8 place-items-center border-b border-sentinel-line text-sentinel-muted hover:text-white"
            >
              <Plus className="h-4 w-4" />
            </button>
            <button
              type="button" onClick={zoomOut}
              title="Zoom out (-)" aria-label="Zoom out"
              className="pointer-events-auto grid h-8 w-8 place-items-center border-b border-sentinel-line text-sentinel-muted hover:text-white"
            >
              <Minus className="h-4 w-4" />
            </button>
            <button
              type="button" onClick={recenter}
              title="Recenter (0)" aria-label="Recenter map"
              className="pointer-events-auto grid h-8 w-8 place-items-center border-b border-sentinel-line text-sentinel-muted hover:text-white"
            >
              <Crosshair className="h-4 w-4" />
            </button>
            <button
              type="button" onClick={() => setFocusMode((f) => !f)}
              title="Focus map (F)" aria-label="Toggle focus mode" aria-pressed={focusMode}
              className={`pointer-events-auto grid h-8 w-8 place-items-center ${
                focusMode ? 'text-sentinel-accent' : 'text-sentinel-muted hover:text-white'
              }`}
            >
              {focusMode ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
            </button>
          </div>

          {/* Top-center toolbar — geometry mode, Prithvi overlays, draw mode */}
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
                    k === 'hbb' ? 'Axis-aligned bounding box'
                    : k === 'obb' ? 'Oriented bounding box (from SAM3 metadata)'
                    : 'Mask polygon (raw geometry)'
                  }
                  className={`px-3 py-1 rounded-full transition ${
                    bboxMode === k ? 'bg-sentinel-accent text-slate-900 font-bold' : 'text-slate-300 hover:text-white'
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
                    onClick={() => setPrithviOverlays((cur: Record<string, boolean>) => ({ ...cur, [k]: !cur[k] }))}
                    title={`Toggle Prithvi ${k} overlay`}
                    className={`px-3 py-1 rounded-full transition ${
                      on ? 'bg-sentinel-accent/20 text-sentinel-accent' : 'text-slate-400 hover:text-white'
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
                  activeLayers.tracks ? 'bg-sentinel-accent/20 text-sentinel-accent' : 'text-slate-400 hover:text-white'
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
  );
});

export default MapStage;

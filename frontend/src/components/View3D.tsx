import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Shield, Swords } from 'lucide-react';
import { useEventStream } from '../hooks/useEventStream';

const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8080';
const TILE_PROXY_URL = import.meta.env.VITE_TILE_PROXY_URL || 'http://127.0.0.1:8090';
const MAX_3D_DETECTIONS = 350;
const MAX_3D_LABELS = 80;

// Tell CesiumJS where to find its vendored workers/assets.
(window as Window & { CESIUM_BASE_URL?: string }).CESIUM_BASE_URL = '/cesium/';

interface Aimpoint {
  id: string;
  label: string;
  latitude: number;
  longitude: number;
  radius_m?: number;
}

interface OpsTarget {
  id: string;
  properties: Record<string, any>;
  aipoints: Aimpoint[];
  readiness: 'ready' | 'tasked' | string;
  queue: string;
  task_count: number;
}

interface ImageryRow {
  id: number;
  name: string;
  file_path: string;
  sensor_type: string;
  acquisition_time?: string;
  cloud_cover?: number;
  footprint_geojson?: string | Record<string, any>;
}

interface View3DProps {
  fmvClipId?: number;
  targets?: OpsTarget[];
  selectedTarget?: OpsTarget | null;
  imagery?: ImageryRow[];
  selectedImagery?: ImageryRow | null;
  imageryOpacity?: number;
  showAimpoints?: boolean;
  showRanges?: boolean;
  events?: any[];
  onSelectTarget?: (targetId: string) => void;
}

function asNumber(value: any): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function targetLatLon(target?: OpsTarget | null): [number, number] | null {
  if (!target) return null;
  const lat = asNumber(target.properties.latitude);
  const lon = asNumber(target.properties.longitude);
  return lat === null || lon === null ? null : [lat, lon];
}

function parseGeoJson(input?: string | Record<string, any> | null): any | null {
  if (!input) return null;
  try {
    return typeof input === 'string' ? JSON.parse(input) : input;
  } catch {
    return null;
  }
}

function validLonLat(point: any): point is [number, number] {
  const lon = Number(point?.[0]);
  const lat = Number(point?.[1]);
  return Number.isFinite(lon) && Number.isFinite(lat) && lon >= -180 && lon <= 180 && lat >= -90 && lat <= 90;
}

function sanitizeRing(ring: any[]): Array<[number, number]> {
  const clean = (ring || [])
    .filter(validLonLat)
    .map(([lon, lat]) => [Number(lon), Number(lat)] as [number, number]);
  const deduped = clean.filter((point, index) => (
    index === 0 || point[0] !== clean[index - 1][0] || point[1] !== clean[index - 1][1]
  ));
  if (deduped.length > 3) {
    const first = deduped[0];
    const last = deduped[deduped.length - 1];
    if (first[0] === last[0] && first[1] === last[1]) deduped.pop();
  }
  const unique = new Set(deduped.map(([lon, lat]) => `${lon.toFixed(7)},${lat.toFixed(7)}`));
  if (unique.size < 3) return [];
  const bounds = ringBounds([deduped]);
  if (!bounds || bounds.west === bounds.east || bounds.south === bounds.north) return [];
  return deduped;
}

function polygonRingsFromGeometry(geometry: any): Array<Array<[number, number]>> {
  if (!geometry) return [];
  if (geometry.type === 'FeatureCollection') {
    return (geometry.features || []).flatMap((feature: any) => polygonRingsFromGeometry(feature));
  }
  if (geometry.type === 'Feature') {
    return polygonRingsFromGeometry(geometry.geometry);
  }
  if (geometry.type === 'Polygon') {
    const ring = geometry.coordinates?.[0] || [];
    const clean = sanitizeRing(ring);
    return clean.length >= 3 ? [clean] : [];
  }
  if (geometry.type === 'MultiPolygon') {
    return (geometry.coordinates || []).flatMap((polygon: any) => {
      const ring = polygon?.[0] || [];
      const clean = sanitizeRing(ring);
      return clean.length >= 3 ? [clean] : [];
    });
  }
  return [];
}

function footprintRings(imagery?: ImageryRow | null): Array<Array<[number, number]>> {
  return polygonRingsFromGeometry(parseGeoJson(imagery?.footprint_geojson));
}

function ringBounds(rings: Array<Array<[number, number]>>) {
  const points = rings.flat();
  if (!points.length) return null;
  const lons = points.map(([lon]) => lon);
  const lats = points.map(([, lat]) => lat);
  return {
    west: Math.min(...lons),
    south: Math.min(...lats),
    east: Math.max(...lons),
    north: Math.max(...lats),
  };
}

function displayName(target?: OpsTarget | null) {
  return target?.properties.name || target?.properties.callsign || target?.id || 'Target';
}

function recentEventText(event: any) {
  return event?.message || event?.type || 'Event';
}

function detectionLabel(feature: any) {
  return String(feature?.properties?.label || feature?.properties?.class || 'Unknown');
}

function detectionColor(feature: any) {
  const allegiance = String(feature?.properties?.allegiance || '').toLowerCase();
  if (allegiance === 'friendly') return '#34d399';
  if (allegiance === 'hostile') return '#fb7185';
  const threat = String(feature?.properties?.threat_level || '').toLowerCase();
  if (threat === 'critical') return '#f43f5e';
  if (threat === 'high') return '#f97316';
  if (threat === 'medium') return '#facc15';
  return '#38bdf8';
}

function threatClass(level?: string) {
  switch (String(level || '').toLowerCase()) {
    case 'critical':
      return 'border-rose-500/50 bg-rose-500/10 text-rose-200';
    case 'high':
      return 'border-orange-400/50 bg-orange-500/10 text-orange-100';
    case 'medium':
      return 'border-amber-400/50 bg-amber-500/10 text-amber-100';
    default:
      return 'border-slate-700 bg-slate-800 text-slate-300';
  }
}

function detectionRings(feature: any): Array<Array<[number, number]>> {
  return polygonRingsFromGeometry(feature);
}

function bboxString(bounds: ReturnType<typeof ringBounds> | null) {
  if (!bounds) return '';
  const values = [bounds.west, bounds.south, bounds.east, bounds.north];
  return values.every(Number.isFinite) ? values.join(',') : '';
}

function targetBbox(target?: OpsTarget | null, pad = 2) {
  const latLon = targetLatLon(target);
  if (!latLon) return null;
  const [lat, lon] = latLon;
  return {
    west: Math.max(-180, lon - pad),
    south: Math.max(-90, lat - pad),
    east: Math.min(180, lon + pad),
    north: Math.min(90, lat + pad),
  };
}

export default function View3D({
  fmvClipId: _fmvClipId,
  targets = [],
  selectedTarget,
  imagery = [],
  selectedImagery,
  imageryOpacity = 0.72,
  showAimpoints = true,
  showRanges = true,
  events = [],
  onSelectTarget,
}: View3DProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<any>(null);
  const cesiumRef = useRef<any>(null);
  const clickHandlerRef = useRef<any>(null);
  const rasterLayerRef = useRef<any>(null);
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detectionsGeoJSON, setDetectionsGeoJSON] = useState<any>({ type: 'FeatureCollection', features: [] });
  const [detectionClasses, setDetectionClasses] = useState<any[]>([]);
  const [hiddenDetectionClasses, setHiddenDetectionClasses] = useState<string[]>([]);
  const [selectedDetection, setSelectedDetection] = useState<any | null>(null);

  const selectedLatLon = useMemo(() => targetLatLon(selectedTarget), [selectedTarget]);
  const visibleFootprints = useMemo(() => {
    const rows = selectedImagery ? [selectedImagery, ...imagery.filter((row) => row.id !== selectedImagery.id)] : imagery;
    return rows.filter((row) => footprintRings(row).length > 0).slice(0, 20);
  }, [imagery, selectedImagery]);
  const detectionBbox = useMemo(() => (
    bboxString(ringBounds(footprintRings(selectedImagery))) || bboxString(targetBbox(selectedTarget))
  ), [selectedImagery, selectedTarget]);
  const visibleDetections = useMemo(() => (
    (detectionsGeoJSON.features || []).filter((feature: any) => !hiddenDetectionClasses.includes(String(feature?.properties?.class || 'Unknown')))
  ).slice(0, MAX_3D_DETECTIONS), [detectionsGeoJSON, hiddenDetectionClasses]);

  const fetchDetections = useCallback(async () => {
    if (!detectionBbox) {
      setDetectionsGeoJSON({ type: 'FeatureCollection', features: [] });
      setDetectionClasses([]);
      return;
    }
    try {
      const params = new URLSearchParams({ bbox: detectionBbox });
      const [geojsonResponse, classResponse] = await Promise.all([
        axios.get(`${API_URL}/api/detections/geojson?${params.toString()}`),
        axios.get(`${API_URL}/api/detections/classes?${new URLSearchParams({ bbox: detectionBbox, llm: 'true' }).toString()}`),
      ]);
      setDetectionsGeoJSON(geojsonResponse.data || { type: 'FeatureCollection', features: [] });
      setDetectionClasses(classResponse.data?.classes || []);
    } catch (err) {
      console.error('Error fetching 3D detections:', err);
    }
  }, [detectionBbox]);

  useEffect(() => {
    fetchDetections();
  }, [fetchDetections]);

  useEventStream('detections', () => {
    fetchDetections();
  });

  const tagDetection = async (detectionId: number, allegiance: string) => {
    await axios.patch(`${API_URL}/api/detections/${detectionId}/tag`, { allegiance });
    await fetchDetections();
    setSelectedDetection((current: any) => current?.properties?.id === detectionId
      ? { ...current, properties: { ...current.properties, allegiance } }
      : current);
  };

  useEffect(() => {
    if (!containerRef.current) return;
    let mounted = true;
    let viewer: any = null;

    import('cesium').then((Cesium) => {
      if (!mounted) return;
      try {
        const { Viewer, Ion, ImageryLayer, TileMapServiceImageryProvider, buildModuleUrl, Color } = Cesium;
        Ion.defaultAccessToken = '';

        let baseLayer: any;
        try {
          baseLayer = ImageryLayer.fromProviderAsync(
            TileMapServiceImageryProvider.fromUrl(buildModuleUrl('Assets/Textures/NaturalEarthII')),
          );
        } catch {
          baseLayer = undefined;
        }

        viewer = new Viewer(containerRef.current!, {
          baseLayer,
          baseLayerPicker: false,
          geocoder: false,
          homeButton: false,
          sceneModePicker: false,
          navigationHelpButton: false,
          animation: false,
          timeline: false,
          fullscreenButton: false,
          infoBox: false,
          selectionIndicator: false,
          creditContainer: document.createElement('div'),
        });

        viewer.scene.globe.enableLighting = false;
        viewer.scene.globe.baseColor = Color.fromCssColorString('#1f2937');
        viewer.scene.backgroundColor = Color.fromCssColorString('#020617');
        viewer.scene.screenSpaceCameraController.minimumZoomDistance = 500;
        viewer.scene.screenSpaceCameraController.maximumZoomDistance = 25000000;
        viewer.scene.screenSpaceCameraController.enableCollisionDetection = true;
        viewer.scene.globe.depthTestAgainstTerrain = false;
        viewerRef.current = viewer;
        cesiumRef.current = Cesium;
        setReady(true);
        setLoading(false);
      } catch (err) {
        setError(String(err));
        setLoading(false);
      }
    }).catch((err) => {
      setError(`Failed to load CesiumJS: ${err}`);
      setLoading(false);
    });

    return () => {
      mounted = false;
      if (clickHandlerRef.current) {
        clickHandlerRef.current.destroy();
        clickHandlerRef.current = null;
      }
      if (viewer && typeof viewer.destroy === 'function') {
        viewer.destroy();
      }
      viewerRef.current = null;
      cesiumRef.current = null;
    };
  }, []);

  useEffect(() => {
    const viewer = viewerRef.current;
    const Cesium = cesiumRef.current;
    if (!ready || !viewer || !Cesium) return;

    const {
      Cartesian2,
      Cartesian3,
      Color,
      HeightReference,
      LabelStyle,
      NearFarScalar,
      PolygonHierarchy,
      VerticalOrigin,
    } = Cesium;

    viewer.entities.removeAll();

    visibleFootprints.forEach((row) => {
      const selected = selectedImagery?.id === row.id;
      const fill = selected ? Color.CYAN.withAlpha(0.18) : Color.fromCssColorString('#1e90ff').withAlpha(0.08);
      const outline = selected ? Color.CYAN.withAlpha(0.95) : Color.fromCssColorString('#1e90ff').withAlpha(0.45);

      footprintRings(row).forEach((ring, index) => {
        const degrees = ring.flatMap(([lon, lat]) => [lon, lat]);
        viewer.entities.add({
          id: `imagery-${row.id}-${index}`,
          name: row.name,
          polygon: {
            hierarchy: new PolygonHierarchy(Cartesian3.fromDegreesArray(degrees)),
            material: fill,
            outline: true,
            outlineColor: outline,
          },
        });
      });
    });

    targets.forEach((target) => {
      const latLon = targetLatLon(target);
      if (!latLon) return;
      const [lat, lon] = latLon;
      const selected = selectedTarget?.id === target.id;
      const color = selected ? Color.fromCssColorString('#f87171') : target.readiness === 'tasked'
        ? Color.fromCssColorString('#34d399')
        : Color.fromCssColorString('#60a5fa');

      viewer.entities.add({
        id: `target-${target.id}`,
        name: displayName(target),
        position: Cartesian3.fromDegrees(lon, lat, 1200),
        point: {
          pixelSize: selected ? 14 : 9,
          color,
          outlineColor: Color.WHITE.withAlpha(0.9),
          outlineWidth: 1,
          heightReference: HeightReference.CLAMP_TO_GROUND,
        },
        label: {
          text: displayName(target),
          font: selected ? '700 13px sans-serif' : '12px sans-serif',
          fillColor: Color.WHITE,
          outlineColor: Color.BLACK,
          outlineWidth: 3,
          style: LabelStyle.FILL_AND_OUTLINE,
          pixelOffset: new Cartesian2(0, -20),
          verticalOrigin: VerticalOrigin.BOTTOM,
          scaleByDistance: new NearFarScalar(100000, 1, 8000000, 0.35),
        },
      });
    });

    visibleDetections.forEach((feature: any, featureIndex: number) => {
      const rings = detectionRings(feature);
      const props = feature.properties || {};
      const color = Color.fromCssColorString(detectionColor(feature));
      rings.forEach((ring, index) => {
        const degrees = ring.flatMap(([lon, lat]) => [lon, lat]);
        const showLabel = featureIndex < MAX_3D_LABELS;
        const entity = viewer.entities.add({
          id: `detection-${props.id}-${index}`,
          name: detectionLabel(feature),
          polygon: {
            hierarchy: new PolygonHierarchy(Cartesian3.fromDegreesArray(degrees)),
            material: color.withAlpha(0.22),
            outline: true,
            outlineColor: color.withAlpha(0.95),
          },
          label: showLabel ? {
            text: detectionLabel(feature),
            font: '11px sans-serif',
            fillColor: Color.WHITE,
            outlineColor: Color.BLACK,
            outlineWidth: 3,
            style: LabelStyle.FILL_AND_OUTLINE,
            pixelOffset: new Cartesian2(0, -16),
            verticalOrigin: VerticalOrigin.BOTTOM,
            scaleByDistance: new NearFarScalar(100000, 1, 7000000, 0.25),
          } : undefined,
          properties: { detectionFeature: feature },
        });
        if (ring.length) {
          const lon = ring.reduce((sum, point) => sum + point[0], 0) / ring.length;
          const lat = ring.reduce((sum, point) => sum + point[1], 0) / ring.length;
          entity.position = Cartesian3.fromDegrees(lon, lat, 700);
        }
      });
    });

    if (selectedTarget && selectedLatLon) {
      const [lat, lon] = selectedLatLon;
      if (showRanges) {
        [1000, 5000].forEach((radius, index) => {
          viewer.entities.add({
            id: `range-${selectedTarget.id}-${radius}`,
            position: Cartesian3.fromDegrees(lon, lat),
            ellipse: {
              semiMajorAxis: radius,
              semiMinorAxis: radius,
              material: Color.fromCssColorString('#bef264').withAlpha(index === 0 ? 0.1 : 0.045),
              outline: true,
              outlineColor: Color.fromCssColorString('#bef264').withAlpha(index === 0 ? 0.8 : 0.55),
              heightReference: HeightReference.CLAMP_TO_GROUND,
            },
          });
        });
      }

      if (showAimpoints) {
        selectedTarget.aipoints?.forEach((point) => {
          viewer.entities.add({
            id: `aimpoint-${point.id}`,
            name: point.label,
            position: Cartesian3.fromDegrees(point.longitude, point.latitude, 900),
            point: {
              pixelSize: 8,
              color: Color.fromCssColorString('#a3e635'),
              outlineColor: Color.BLACK.withAlpha(0.9),
              outlineWidth: 1,
              heightReference: HeightReference.CLAMP_TO_GROUND,
            },
            ellipse: {
              semiMajorAxis: point.radius_m || 120,
              semiMinorAxis: point.radius_m || 120,
              material: Color.fromCssColorString('#a3e635').withAlpha(0.1),
              outline: true,
              outlineColor: Color.fromCssColorString('#a3e635').withAlpha(0.75),
              heightReference: HeightReference.CLAMP_TO_GROUND,
            },
            label: {
              text: point.label,
              font: '11px sans-serif',
              fillColor: Color.WHITE,
              outlineColor: Color.BLACK,
              outlineWidth: 3,
              style: LabelStyle.FILL_AND_OUTLINE,
              pixelOffset: new Cartesian2(0, -18),
              verticalOrigin: VerticalOrigin.BOTTOM,
              scaleByDistance: new NearFarScalar(100000, 1, 6000000, 0.3),
            },
          });
        });

        if (selectedTarget.aipoints?.length) {
          viewer.entities.add({
            id: `aimpoint-route-${selectedTarget.id}`,
            polyline: {
              positions: [
                Cartesian3.fromDegrees(lon, lat, 1200),
                ...selectedTarget.aipoints.map((point) => Cartesian3.fromDegrees(point.longitude, point.latitude, 1200)),
              ],
              width: 2,
              material: Color.fromCssColorString('#a3e635').withAlpha(0.65),
              clampToGround: true,
            },
          });
        }
      }
    }
  }, [ready, targets, selectedTarget, selectedLatLon, visibleFootprints, selectedImagery, showAimpoints, showRanges, visibleDetections]);

  useEffect(() => {
    const viewer = viewerRef.current;
    const Cesium = cesiumRef.current;
    if (!ready || !viewer || !Cesium) return;

    if (clickHandlerRef.current) {
      clickHandlerRef.current.destroy();
      clickHandlerRef.current = null;
    }

    const { ScreenSpaceEventHandler, ScreenSpaceEventType } = Cesium;
    const handler = new ScreenSpaceEventHandler(viewer.scene.canvas);
    handler.setInputAction((movement: any) => {
      const picked = viewer.scene.pick(movement.position);
      const entityId = picked?.id?.id;
      if (typeof entityId === 'string' && entityId.startsWith('target-')) {
        onSelectTarget?.(entityId.slice('target-'.length));
      } else if (typeof entityId === 'string' && entityId.startsWith('detection-')) {
        const feature = picked?.id?.properties?.detectionFeature?.getValue?.() || picked?.id?.properties?.detectionFeature;
        if (feature) setSelectedDetection(feature);
      }
    }, ScreenSpaceEventType.LEFT_CLICK);
    clickHandlerRef.current = handler;

    return () => {
      handler.destroy();
      if (clickHandlerRef.current === handler) {
        clickHandlerRef.current = null;
      }
    };
  }, [ready, onSelectTarget]);

  useEffect(() => {
    const viewer = viewerRef.current;
    const Cesium = cesiumRef.current;
    if (!ready || !viewer || !Cesium) return;

    if (rasterLayerRef.current) {
      viewer.imageryLayers.remove(rasterLayerRef.current, true);
      rasterLayerRef.current = null;
    }
    if (!selectedImagery?.file_path) return;

    const { ImageryLayer, UrlTemplateImageryProvider } = Cesium;
    const provider = new UrlTemplateImageryProvider({
      url: `${TILE_PROXY_URL}/cog/tiles/{z}/{x}/{y}?url=${encodeURIComponent(selectedImagery.file_path)}`,
      maximumLevel: 22,
    });
    const layer = new ImageryLayer(provider, { alpha: imageryOpacity });
    viewer.imageryLayers.add(layer);
    rasterLayerRef.current = layer;

    return () => {
      if (rasterLayerRef.current === layer) {
        viewer.imageryLayers.remove(layer, true);
        rasterLayerRef.current = null;
      }
    };
  }, [ready, selectedImagery?.id, selectedImagery?.file_path, imageryOpacity]);

  useEffect(() => {
    const viewer = viewerRef.current;
    const Cesium = cesiumRef.current;
    if (!ready || !viewer || !Cesium) return;

    const { Cartesian3, Rectangle } = Cesium;
    if (selectedLatLon) {
      const [lat, lon] = selectedLatLon;
      viewer.camera.flyTo({
        destination: Cartesian3.fromDegrees(lon, lat, 120000),
        duration: 0.7,
      });
      return;
    }

    const bounds = ringBounds(footprintRings(selectedImagery));
    if (bounds) {
      viewer.camera.flyTo({
        destination: Rectangle.fromDegrees(bounds.west, bounds.south, bounds.east, bounds.north),
        duration: 0.7,
      });
    }
  }, [ready, selectedTarget?.id, selectedImagery?.id]);

  return (
    <div className="relative w-full h-full bg-slate-950">
      <div ref={containerRef} className="w-full h-full" />

      <div className="absolute top-4 left-4 z-[450] w-72 border border-lime-500/30 bg-slate-950/88 backdrop-blur px-3 py-3 text-xs shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-800 pb-2">
          <span className="uppercase tracking-wider text-lime-200">3D Ops Overlay</span>
          <span className="font-mono text-slate-400">{targets.length} targets / {visibleDetections.length} detections</span>
        </div>
        <div className="grid grid-cols-3 gap-2 py-3">
          <div className="border border-slate-800 bg-slate-900/70 px-2 py-2">
            <div className="text-[10px] uppercase text-slate-500">Footprints</div>
            <div className="text-cyan-200 font-mono">{visibleFootprints.length}</div>
          </div>
          <div className="border border-slate-800 bg-slate-900/70 px-2 py-2">
            <div className="text-[10px] uppercase text-slate-500">Aimpoints</div>
            <div className="text-lime-200 font-mono">{selectedTarget?.aipoints?.length || 0}</div>
          </div>
          <div className="border border-slate-800 bg-slate-900/70 px-2 py-2">
            <div className="text-[10px] uppercase text-slate-500">Tasks</div>
            <div className="text-emerald-200 font-mono">{selectedTarget?.task_count || 0}</div>
          </div>
        </div>
        <div className="mt-3 border-t border-slate-800 pt-2">
          <div className="text-[10px] uppercase text-slate-500 mb-2">Detection Filters</div>
          <div className="max-h-40 overflow-y-auto space-y-1 pr-1">
            {detectionClasses.map((item) => {
              const hidden = hiddenDetectionClasses.includes(item.class);
              return (
                <button
                  key={item.class}
                  type="button"
                  onClick={() => setHiddenDetectionClasses((current) => (
                    current.includes(item.class) ? current.filter((label) => label !== item.class) : [...current, item.class]
                  ))}
                  className={`w-full border px-2 py-1.5 text-left ${hidden ? 'border-slate-800 bg-slate-950 text-slate-500' : 'border-slate-700 bg-slate-900/80 text-slate-200'}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate">{item.label || item.class}</span>
                    <span className="font-mono">{item.count}</span>
                  </div>
                  <div className="mt-1 flex items-center gap-1 text-[10px]">
                    <span className={`px-1.5 py-0.5 border ${threatClass(item.threat_level)}`}>{item.threat_level || 'low'}</span>
                    <span className="truncate text-slate-500">{item.ontology?.category || 'ontology'}</span>
                  </div>
                </button>
              );
            })}
            {detectionClasses.length === 0 && <div className="text-slate-500">No detected classes.</div>}
          </div>
        </div>
        {selectedDetection && (
          <div className="mt-3 border-t border-slate-800 pt-2">
            <div className="text-[10px] uppercase text-slate-500">Selected Detection</div>
            <div className="text-slate-100 truncate">{detectionLabel(selectedDetection)}</div>
            <div className="text-[11px] text-slate-500 line-clamp-2">
              {selectedDetection.properties?.ontology?.description || 'Ontology unavailable.'}
            </div>
            <div className="mt-2 flex gap-2">
              <button onClick={() => tagDetection(selectedDetection.properties.id, 'friendly')} className="h-7 flex-1 border border-emerald-500/50 bg-emerald-500/10 text-emerald-100 flex items-center justify-center gap-1">
                <Shield className="w-3 h-3" /> Friendly
              </button>
              <button onClick={() => tagDetection(selectedDetection.properties.id, 'hostile')} className="h-7 flex-1 border border-rose-500/50 bg-rose-500/10 text-rose-100 flex items-center justify-center gap-1">
                <Swords className="w-3 h-3" /> Hostile
              </button>
            </div>
          </div>
        )}
        <div className="space-y-2">
          <div>
            <div className="text-[10px] uppercase text-slate-500">Selected Target</div>
            <div className="text-slate-100 truncate">{selectedTarget ? displayName(selectedTarget) : 'None'}</div>
            {selectedTarget && <div className="text-slate-500 truncate">{selectedTarget.queue}</div>}
          </div>
          <div>
            <div className="text-[10px] uppercase text-slate-500">Selected Imagery</div>
            <div className="text-cyan-100 truncate">{selectedImagery?.name || 'None'}</div>
            {selectedImagery && <div className="text-slate-500 truncate">{selectedImagery.sensor_type}</div>}
          </div>
        </div>
        {!!events.length && (
          <div className="mt-3 border-t border-slate-800 pt-2">
            <div className="text-[10px] uppercase text-slate-500 mb-1">Recent Activity</div>
            <div className="space-y-1">
              {events.slice(0, 3).map((event, index) => (
                <div key={`${event?.at || index}-${index}`} className="text-slate-300 truncate">
                  {recentEventText(event)}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {loading && (
        <div className="absolute inset-0 flex items-center justify-center text-slate-300 text-sm">
          Loading 3D globe...
        </div>
      )}
      {error && (
        <div className="absolute inset-0 flex items-center justify-center text-red-400 text-sm px-4 text-center">
          {error}
        </div>
      )}
    </div>
  );
}

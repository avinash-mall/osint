import { useEffect, useMemo, useRef, useState } from 'react';

const TILE_PROXY_URL = import.meta.env.VITE_TILE_PROXY_URL || 'http://localhost:8090';

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
    return ring.length >= 3 ? [ring.map(([lon, lat]: [number, number]) => [lon, lat])] : [];
  }
  if (geometry.type === 'MultiPolygon') {
    return (geometry.coordinates || []).flatMap((polygon: any) => {
      const ring = polygon?.[0] || [];
      return ring.length >= 3 ? [ring.map(([lon, lat]: [number, number]) => [lon, lat])] : [];
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

  const selectedLatLon = useMemo(() => targetLatLon(selectedTarget), [selectedTarget]);
  const visibleFootprints = useMemo(() => {
    const rows = selectedImagery ? [selectedImagery, ...imagery.filter((row) => row.id !== selectedImagery.id)] : imagery;
    return rows.filter((row) => footprintRings(row).length > 0).slice(0, 20);
  }, [imagery, selectedImagery]);

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
  }, [ready, targets, selectedTarget, selectedLatLon, visibleFootprints, selectedImagery, showAimpoints, showRanges]);

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
          <span className="font-mono text-slate-400">{targets.length} targets</span>
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

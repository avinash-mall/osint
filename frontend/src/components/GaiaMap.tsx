import { useEffect, useState, useCallback, useMemo } from 'react';
import { MapContainer, TileLayer, Marker, Popup, ZoomControl, Polyline, Circle, GeoJSON, ImageOverlay, useMap } from 'react-leaflet';
import L from 'leaflet';
import axios from 'axios';
import { Crosshair, Navigation, ShieldAlert, Activity, Layers, Clock, Eye, Satellite, Filter } from 'lucide-react';
import 'leaflet/dist/leaflet.css';
import { useEventStream } from '../hooks/useEventStream';
import { type UploadJob, isUploadActive, uploadMessage, uploadProgress, uploadProgressClass, uploadStage } from '../utils/uploadProgress';

// API Configuration
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080';
const TILE_PROXY_URL = import.meta.env.VITE_TILE_PROXY_URL || 'http://localhost:8090';

// Removed external CDN merge for offline support
delete (L.Icon.Default.prototype as any)._getIconUrl;

// Custom Icons
const createIcon = (color: string) => new L.Icon({
  iconUrl: `data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIlMjMzYjgyZjYiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMjEgMTBsLTkgMTVMMyAxMGw5LTl6Ii8+PC9zdmc+`.replace('%233b82f6', encodeURIComponent(color)),
  iconSize: [20, 20],
  iconAnchor: [10, 20],
  popupAnchor: [0, -20],
});

const blueIcon = createIcon('#3b82f6');
const redIcon = createIcon('#ef4444');
const emeraldIcon = createIcon('#10b981');

// Detection style by class
const getDetectionStyle = (feature: any) => {
  const cls = feature.properties?.class || 'Unknown';
  const colors: Record<string, string> = {
    'Vessel': '#3b82f6',
    'Aircraft': '#ef4444',
    'Facility': '#10b981',
    'Unknown': '#f59e0b'
  };
  const color = colors[cls] || '#f59e0b';
  return {
    color: color,
    weight: 2,
    opacity: 0.9,
    fillColor: color,
    fillOpacity: 0.15,
    dashArray: '4, 4'
  };
};

// Map bounds updater component
function MapBoundsUpdater({ onBoundsChange }: { onBoundsChange: (bounds: string) => void }) {
  const map = useMap();
  useEffect(() => {
    const handleMoveEnd = () => {
      const b = map.getBounds();
      const bbox = `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
      onBoundsChange(bbox);
    };
    map.on('moveend', handleMoveEnd);
    handleMoveEnd(); // initial
    return () => { map.off('moveend', handleMoveEnd); };
  }, [map, onBoundsChange]);
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

export default function GaiaMap() {
  const [data, setData] = useState<{ static: any[], tracks: any[] }>({ static: [], tracks: [] });
  const [imagery, setImagery] = useState<any[]>([]);
  const [detectionsGeoJSON, setDetectionsGeoJSON] = useState<any>({ type: 'FeatureCollection', features: [] });
  const [basemapGeoJSON, setBasemapGeoJSON] = useState<any>({ type: 'FeatureCollection', features: [] });
  const [uploadJobs, setUploadJobs] = useState<UploadJob[]>([]);
  const [selectedImagery, setSelectedImagery] = useState<number | null>(null);
  const [imageryOpacity, setImageryOpacity] = useState(0.8);
  const [timeRange, setTimeRange] = useState<{ start: string; end: string }>(() => {
    const now = new Date();
    const dayAgo = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    return {
      start: dayAgo.toISOString(),
      end: now.toISOString()
    };
  });
  const [mapBounds, setMapBounds] = useState<string>('');
  const [activeLayers, setActiveLayers] = useState({
    satellite: true,
    detections: true,
    tracks: true,
    static: true,
    grid: true
  });
  const [isLoading, setIsLoading] = useState(false);
  const processingUploads = useMemo(
    () => uploadJobs.filter((job) => job.media_type === 'imagery' && isUploadActive(job)).slice(0, 3),
    [uploadJobs],
  );

  const fetchData = useCallback(async () => {
      try {
        const response = await axios.get(`${API_URL}/api/geotime/features`);
        setData(response.data || { static: [], tracks: [] });
      } catch (error) {
        console.error("Error fetching geotime data:", error);
      }
  }, []);

  // Fetch static tracks and features
  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEventStream('geotime', useCallback(() => {
    fetchData();
  }, [fetchData]));

  useEffect(() => {
    const fetchBasemap = async () => {
      try {
        const response = await axios.get(`${API_URL}/api/basemap/countries`);
        setBasemapGeoJSON(response.data || { type: 'FeatureCollection', features: [] });
      } catch (error) {
        console.error("Error fetching offline basemap:", error);
      }
    };
    fetchBasemap();
  }, []);

  const fetchUploadJobs = useCallback(async () => {
    try {
      const response = await axios.get(`${API_URL}/api/ingest/uploads`);
      setUploadJobs(response.data.uploads || []);
    } catch (error) {
      console.error("Error fetching upload jobs:", error);
    }
  }, []);

  useEffect(() => {
    fetchUploadJobs();
  }, [fetchUploadJobs]);

  useEffect(() => {
    if (processingUploads.length === 0) return;
    const timer = window.setInterval(() => {
      fetchUploadJobs();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [processingUploads.length, fetchUploadJobs]);

  // Fetch imagery catalog
  const fetchImagery = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      params.append('start_time', timeRange.start);
      params.append('end_time', timeRange.end);
      const response = await axios.get(`${API_URL}/api/imagery?${params.toString()}`);
      const rows = response.data.imagery || [];
      setImagery(rows);
      setSelectedImagery((current) => (current && rows.some((row: any) => row.id === current) ? current : rows[0]?.id || null));
    } catch (error) {
      console.error("Error fetching imagery:", error);
    }
  }, [timeRange]);

  useEffect(() => {
    fetchImagery();
  }, [fetchImagery]);

  // Fetch detections GeoJSON
  const fetchDetections = useCallback(async () => {
    if (!mapBounds) return;
    setIsLoading(true);
    try {
      const params = new URLSearchParams();
      params.append('bbox', mapBounds);
      params.append('start_time', timeRange.start);
      params.append('end_time', timeRange.end);
      const response = await axios.get(`${API_URL}/api/detections/geojson?${params.toString()}`);
      setDetectionsGeoJSON(response.data || { type: 'FeatureCollection', features: [] });
    } catch (error) {
      console.error("Error fetching detections:", error);
    } finally {
      setIsLoading(false);
    }
  }, [mapBounds, timeRange]);

  useEffect(() => {
    fetchDetections();
  }, [fetchDetections]);

  useEventStream('detections', useCallback(() => {
    fetchDetections();
    fetchImagery();
    fetchUploadJobs();
  }, [fetchDetections, fetchImagery, fetchUploadJobs]));

  useEventStream('imagery', useCallback(() => {
    fetchImagery();
    fetchUploadJobs();
  }, [fetchImagery, fetchUploadJobs]));

  useEventStream('ops', useCallback((message: any) => {
    if (String(message?.type || '').startsWith('imagery_') || message?.type === 'upload_received') {
      fetchUploadJobs();
    }
  }, [fetchUploadJobs]));

  const onEachDetection = (feature: any, layer: L.Layer) => {
    const props = feature.properties;
    const popupContent = `
      <div style="font-family: sans-serif; min-width: 200px;">
        <div style="font-weight: bold; font-size: 14px; margin-bottom: 8px; color: #e2e8f0; border-bottom: 1px solid #334155; padding-bottom: 4px;">
          ${props.class} Detection
        </div>
        <div style="font-size: 12px; color: #94a3b8; line-height: 1.6;">
          <div>ID: <span style="color: #e2e8f0;">${props.id}</span></div>
          <div>Confidence: <span style="color: #e2e8f0;">${(props.confidence * 100).toFixed(1)}%</span></div>
          <div>Pass: <span style="color: #e2e8f0;">${props.pass_id}</span></div>
          <div>Time: <span style="color: #e2e8f0;">${new Date(props.created_at).toLocaleString()}</span></div>
        </div>
      </div>
    `;
    layer.bindPopup(popupContent);
  };

  const selectedImageryData = imagery.find(img => img.id === selectedImagery);

  return (
    <div className="w-full h-full relative bg-slate-900 flex flex-col">
      {/* Top Overlay Panel */}
      <div className="absolute top-4 left-4 z-[400] bg-slate-900/90 p-4 rounded border border-slate-700 backdrop-blur-md shadow-2xl w-80 pointer-events-auto">
        <h2 className="text-slate-100 font-bold tracking-widest text-sm mb-3 flex items-center gap-2 uppercase">
          <Activity className="w-4 h-4 text-emerald-500" /> Gaia Geo-Spatial
        </h2>
        <div className="flex flex-col gap-2">
          <div className="flex justify-between items-center p-2 bg-slate-800/50 rounded border border-slate-800">
             <span className="text-xs text-slate-400 font-semibold flex items-center gap-2"><Navigation className="w-3 h-3 text-blue-400"/> ACTIVE TRACKS</span>
             <span className="text-sm font-mono text-slate-200">{data.tracks.length}</span>
          </div>
          <div className="flex justify-between items-center p-2 bg-slate-800/50 rounded border border-slate-800">
             <span className="text-xs text-slate-400 font-semibold flex items-center gap-2"><ShieldAlert className="w-3 h-3 text-red-400"/> LAUNCH POINTS</span>
             <span className="text-sm font-mono text-slate-200">{data.static.filter((s: any) => s.label === 'LaunchPoint').length}</span>
          </div>
          <div className="flex justify-between items-center p-2 bg-slate-800/50 rounded border border-slate-800">
             <span className="text-xs text-slate-400 font-semibold flex items-center gap-2"><Eye className="w-3 h-3 text-emerald-400"/> DETECTIONS</span>
             <span className="text-sm font-mono text-slate-200">{detectionsGeoJSON.features?.length || 0}</span>
          </div>
          <div className="flex justify-between items-center p-2 bg-slate-800/50 rounded border border-slate-800">
             <span className="text-xs text-slate-400 font-semibold flex items-center gap-2"><Satellite className="w-3 h-3 text-indigo-400"/> SAT PASSES</span>
             <span className="text-sm font-mono text-slate-200">{imagery.length}</span>
          </div>
        </div>
      </div>

      {/* Layer Control Panel */}
      <div className="absolute top-4 right-4 z-[400] bg-slate-900/90 p-4 rounded border border-slate-700 backdrop-blur-md shadow-2xl w-56 pointer-events-auto">
        <h3 className="text-xs font-bold text-slate-400 tracking-widest uppercase mb-3 flex items-center gap-2">
          <Layers className="w-3 h-3" /> Layer Control
        </h3>
        <div className="flex flex-col gap-2">
          {[
            { key: 'satellite', label: 'Satellite Imagery', color: 'text-indigo-400' },
            { key: 'detections', label: 'AI Detections', color: 'text-emerald-400' },
            { key: 'tracks', label: 'Active Tracks', color: 'text-blue-400' },
            { key: 'static', label: 'Static Features', color: 'text-red-400' },
            { key: 'grid', label: 'Tactical Grid', color: 'text-slate-400' },
          ].map((layer) => (
            <label key={layer.key} className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer hover:text-white transition">
              <input
                type="checkbox"
                checked={activeLayers[layer.key as keyof typeof activeLayers]}
                onChange={(e) => setActiveLayers(prev => ({ ...prev, [layer.key]: e.target.checked }))}
                className="rounded border-slate-600 bg-slate-800 text-blue-500 focus:ring-blue-500"
              />
              <span className={layer.color}>{layer.label}</span>
            </label>
          ))}
        </div>

        {activeLayers.satellite && selectedImageryData && (
          <div className="mt-3 pt-3 border-t border-slate-700">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Opacity</div>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={imageryOpacity}
              onChange={(e) => setImageryOpacity(parseFloat(e.target.value))}
              className="w-full h-1 bg-slate-700 rounded-lg appearance-none cursor-pointer"
            />
          </div>
        )}
      </div>

      {/* Imagery Selector Panel */}
      {imagery.length > 0 && (
        <div className="absolute top-48 left-4 z-[400] bg-slate-900/90 p-3 rounded border border-slate-700 backdrop-blur-md shadow-2xl w-80 pointer-events-auto max-h-64 overflow-y-auto">
          <h3 className="text-xs font-bold text-slate-400 tracking-widest uppercase mb-2 flex items-center gap-2">
            <Satellite className="w-3 h-3" /> Available Imagery
          </h3>
          <div className="flex flex-col gap-1">
            {imagery.map((img) => (
              <button
                key={img.id}
                onClick={() => setSelectedImagery(selectedImagery === img.id ? null : img.id)}
                className={`text-left p-2 rounded text-xs transition border ${
                  selectedImagery === img.id
                    ? 'bg-indigo-500/20 border-indigo-500 text-indigo-300'
                    : 'bg-slate-800/50 border-slate-700 text-slate-400 hover:bg-slate-700 hover:text-slate-200'
                }`}
              >
                <div className="font-semibold truncate">{img.name}</div>
                <div className="text-[10px] opacity-70 mt-0.5">
                  {img.sensor_type} | {new Date(img.acquisition_time).toLocaleString()} | {img.cloud_cover}% cloud
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="flex-1 relative">
        <MapContainer 
          center={[25.0, 55.0]} 
          zoom={6} 
          style={{ height: '100%', width: '100%', background: '#0f172a' }}
          zoomControl={false}
        >
          <ZoomControl position="bottomright" />
          <MapBoundsUpdater onBoundsChange={setMapBounds} />
          <MapFitToImagery imagery={selectedImageryData} />

          {/* Base Layer */}
          <ImageOverlay
            url="/world_map.svg"
            bounds={[[-85, -180], [85, 180]]}
            opacity={0.58}
          />

          {/* Offline vector basemap fallback */}
          {activeLayers.grid && (
            <GeoJSON
              data={basemapGeoJSON}
              style={() => ({
                color: '#93c5fd',
                weight: 1.2,
                opacity: 0.92,
                fillColor: '#1d4ed8',
                fillOpacity: 0.18
              })}
            />
          )}

          {/* Satellite Imagery Layer */}
          {activeLayers.satellite && selectedImageryData && (
            <TileLayer
              url={`${TILE_PROXY_URL}/cog/tiles/{z}/{x}/{y}?url=${encodeURIComponent(selectedImageryData.file_path)}`}
              opacity={imageryOpacity}
              maxZoom={22}
            />
          )}

          {/* Static Features & Range Rings */}
          {activeLayers.static && data.static.map((loc: any) => {
            const isLaunchPoint = loc.label === 'LaunchPoint';
            const radius = loc.properties.threatRadius || 0;
            return (
              <div key={loc.id}>
                {isLaunchPoint && radius > 0 && (
                   <Circle 
                     center={[loc.properties.latitude, loc.properties.longitude]}
                     radius={radius}
                     pathOptions={{ color: '#ef4444', fillColor: '#ef4444', fillOpacity: 0.1, weight: 1, dashArray: '5, 5' }}
                   />
                )}
                <Marker 
                  position={[loc.properties.latitude, loc.properties.longitude]}
                  icon={isLaunchPoint ? redIcon : emeraldIcon}
                >
                  <Popup className="gotham-popup">
                    <div className="text-slate-200 bg-slate-900 p-2 rounded shadow-xl border border-slate-700 min-w-[200px]">
                      <h3 className="font-bold text-sm tracking-wider uppercase flex items-center gap-2 border-b border-slate-700 pb-1 mb-2">
                        {isLaunchPoint ? <ShieldAlert className="w-4 h-4 text-red-500" /> : <Crosshair className="w-4 h-4 text-emerald-500" />}
                        {loc.properties.name}
                      </h3>
                      <div className="text-xs font-mono text-slate-400 flex flex-col gap-1">
                        <span className="flex justify-between"><span>LAT:</span> <span className="text-slate-200">{loc.properties.latitude.toFixed(4)}</span></span>
                        <span className="flex justify-between"><span>LON:</span> <span className="text-slate-200">{loc.properties.longitude.toFixed(4)}</span></span>
                        {radius > 0 && <span className="flex justify-between"><span>RANGE:</span> <span className="text-red-400">{radius/1000}km</span></span>}
                      </div>
                    </div>
                  </Popup>
                </Marker>
              </div>
            );
          })}

          {/* Detection Overlay */}
          {activeLayers.detections && detectionsGeoJSON.features && detectionsGeoJSON.features.length > 0 && (
            <GeoJSON
              data={detectionsGeoJSON}
              style={getDetectionStyle}
              onEachFeature={onEachDetection}
            />
          )}

          {/* Tracks */}
          {activeLayers.tracks && data.tracks.map((track: any) => {
             const positions: [number, number][] = track.history.map((h: any) => [h.lat, h.lng]);
             const latest = track.latest;
             return (
               <div key={track.id}>
                 <Polyline 
                   positions={positions} 
                   pathOptions={{ color: '#3b82f6', weight: 2, opacity: 0.5, dashArray: '4, 6' }} 
                 />
                 {latest && (
                   <Marker position={[latest.latitude, latest.longitude]} icon={blueIcon}>
                     <Popup className="gotham-popup">
                        <div className="text-slate-200 bg-slate-900 p-2 rounded shadow-xl border border-slate-700 min-w-[200px]">
                          <h3 className="font-bold text-sm tracking-wider uppercase flex items-center gap-2 border-b border-slate-700 pb-1 mb-2">
                            <Navigation className="w-4 h-4 text-blue-500" />
                            {track.properties.callsign || track.asset_id}
                          </h3>
                          <div className="text-xs font-mono text-slate-400 flex flex-col gap-1">
                            <span className="flex justify-between"><span>TYPE:</span> <span className="text-slate-200">{track.label}</span></span>
                            <span className="flex justify-between"><span>SPEED:</span> <span className="text-slate-200">{track.properties.speed?.toFixed(1)} kts</span></span>
                            <span className="flex justify-between"><span>HDG:</span> <span className="text-slate-200">{latest.heading?.toFixed(0)}°</span></span>
                            <span className="flex justify-between"><span>LAT:</span> <span className="text-slate-200">{latest.latitude.toFixed(4)}</span></span>
                            <span className="flex justify-between"><span>LON:</span> <span className="text-slate-200">{latest.longitude.toFixed(4)}</span></span>
                          </div>
                        </div>
                     </Popup>
                   </Marker>
                 )}
               </div>
             )
          })}
        </MapContainer>

        {/* Loading Indicator */}
        {isLoading && (
          <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 z-[500] bg-slate-900/90 px-4 py-2 rounded border border-slate-700 text-xs text-slate-300">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse"></div>
              Loading detections...
            </div>
          </div>
        )}

        {processingUploads.length > 0 && (
          <div className="absolute bottom-4 left-4 z-[500] w-96 max-w-[calc(100%-2rem)] bg-slate-900/94 border border-slate-700 shadow-2xl p-3 pointer-events-auto">
            <div className="text-xs font-bold uppercase tracking-widest text-slate-400 mb-2 flex items-center justify-between">
              <span>Imagery Processing</span>
              <span>{processingUploads.length}</span>
            </div>
            <div className="space-y-2">
              {processingUploads.map((job) => {
                const progress = uploadProgress(job);
                return (
                  <div key={job.upload_id} className="border border-slate-800 bg-slate-950/80 px-2 py-2">
                    <div className="flex items-center justify-between gap-2 text-xs">
                      <span className="text-slate-200 font-semibold truncate">{job.filename}</span>
                      <span className="font-mono text-slate-400">{progress}%</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-slate-500">
                      <span className="uppercase">{uploadStage(job)}</span>
                      <span className="truncate">{uploadMessage(job)}</span>
                    </div>
                    <div className="mt-2 h-1.5 w-full bg-slate-800 overflow-hidden">
                      <div className={`h-full transition-all duration-500 ${uploadProgressClass(job)}`} style={{ width: `${progress}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Time Slider Panel */}
      <div className="h-20 bg-slate-900 border-t border-slate-800 p-4 flex items-center gap-4">
         <div className="text-xs font-bold text-slate-400 tracking-wider w-24 flex items-center gap-2">
           <Clock className="w-3 h-3" /> TIMELINE
         </div>
         <div className="flex-1 flex flex-col gap-1">
           <div className="flex justify-between text-[10px] text-slate-500 font-mono">
             <span>{new Date(timeRange.start).toLocaleString()}</span>
             <span>{new Date(timeRange.end).toLocaleString()}</span>
           </div>
           <div className="flex items-center gap-3">
             <input
               type="datetime-local"
               value={timeRange.start.slice(0, 16)}
               onChange={(e) => setTimeRange(prev => ({ ...prev, start: new Date(e.target.value).toISOString() }))}
               className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-300 focus:outline-none focus:border-blue-500"
             />
             <div className="flex-1 h-2 bg-slate-800 rounded relative">
               <div className="absolute top-0 left-0 right-0 bottom-0 bg-blue-500/30 rounded border border-blue-500"></div>
               <input
                 type="range"
                 min="0"
                 max="168"
                 value={Math.round((Date.now() - new Date(timeRange.end).getTime()) / (60 * 60 * 1000))}
                 onChange={(e) => {
                   const hoursAgo = Number(e.target.value);
                   const end = new Date(Date.now() - hoursAgo * 60 * 60 * 1000);
                   const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
                   setTimeRange({ start: start.toISOString(), end: end.toISOString() });
                 }}
                 className="absolute inset-x-0 -top-1 w-full h-4 opacity-80 cursor-pointer"
               />
             </div>
             <input
               type="datetime-local"
               value={timeRange.end.slice(0, 16)}
               onChange={(e) => setTimeRange(prev => ({ ...prev, end: new Date(e.target.value).toISOString() }))}
               className="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-300 focus:outline-none focus:border-blue-500"
             />
           </div>
         </div>
         <button
           onClick={fetchDetections}
           className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold uppercase tracking-wider rounded transition"
         >
           <Filter className="w-3 h-3 inline mr-1" /> Apply
         </button>
      </div>
    </div>
  );
}

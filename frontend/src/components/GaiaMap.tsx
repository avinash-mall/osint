import { useCallback, useEffect, useMemo, useState } from 'react';
import { Circle, GeoJSON, ImageOverlay, MapContainer, Marker, Polyline, Popup, TileLayer, useMap, useMapEvents, ZoomControl } from 'react-leaflet';
import L from 'leaflet';
import axios from 'axios';
import {
  Activity,
  CircleHelp,
  Crosshair,
  Eye,
  EyeOff,
  FileText,
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
import 'leaflet/dist/leaflet.css';
import { useEventStream } from '../hooks/useEventStream';
import { type UploadJob, isUploadActive, uploadMessage, uploadProgress, uploadProgressClass, uploadStage } from '../utils/uploadProgress';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080';
const TILE_PROXY_URL = import.meta.env.VITE_TILE_PROXY_URL || 'http://localhost:8090';

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
  return String(feature?.properties?.class || feature?.properties?.label || 'Unknown');
}

function detectionColor(label: string) {
  const colors: Record<string, string> = {
    Vessel: '#4ea1ff',
    Aircraft: '#ff3b30',
    Facility: '#3dd68c',
    Vehicle: '#ff7a1a',
    Ship: '#3dd68c',
    Plane: '#ff3b30',
    Building: '#aab2bb',
    Unknown: '#f5b400',
  };
  if (colors[label]) return colors[label];
  let hash = 0;
  for (let i = 0; i < label.length; i += 1) hash = (hash * 31 + label.charCodeAt(i)) % 360;
  return `hsl(${hash}, 78%, 62%)`;
}

function confidenceValue(feature: any) {
  const confidence = Number(feature?.properties?.confidence);
  return Number.isFinite(confidence) ? confidence : 0;
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

const getDetectionStyle = (feature: any) => {
  const color = detectionColor(detectionLabel(feature));
  return {
    color,
    weight: 1.3,
    opacity: 0.92,
    fillColor: color,
    fillOpacity: confidenceValue(feature) > 0.85 ? 0.12 : 0.045,
    dashArray: '3, 4',
  };
};

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

function MapFitToDetections({ geojson, enabled }: { geojson: any; enabled: boolean }) {
  const map = useMap();
  useEffect(() => {
    if (!enabled || !geojson?.features?.length) return;
    try {
      const bounds = L.geoJSON(geojson).getBounds();
      if (bounds.isValid()) {
        map.fitBounds(bounds.pad(0.25), { animate: true, maxZoom: 15 });
      }
    } catch {
      // Ignore invalid geometries; the GeoJSON layer itself will skip what Leaflet cannot draw.
    }
  }, [enabled, geojson, map]);
  return null;
}

type GaiaMapProps = {
  onOpenWorkbench?: () => void;
  onOpenGraph?: () => void;
};

export default function GaiaMap({ onOpenWorkbench, onOpenGraph }: GaiaMapProps) {
  const [data, setData] = useState<{ static: any[]; tracks: any[] }>({ static: [], tracks: [] });
  const [imagery, setImagery] = useState<any[]>([]);
  const [detectionsGeoJSON, setDetectionsGeoJSON] = useState<any>({ type: 'FeatureCollection', features: [] });
  const [detectionClasses, setDetectionClasses] = useState<any[]>([]);
  const [basemapGeoJSON, setBasemapGeoJSON] = useState<any>({ type: 'FeatureCollection', features: [] });
  const [uploadJobs, setUploadJobs] = useState<UploadJob[]>([]);
  const [selectedImagery, setSelectedImagery] = useState<number | null>(null);
  const [imageryOpacity, setImageryOpacity] = useState(0.8);
  const [hiddenDetectionLabels, setHiddenDetectionLabels] = useState<string[]>([]);
  const [detectionClassFilter, setDetectionClassFilter] = useState<string | null>(null);
  const [detectionsLayerVersion, setDetectionsLayerVersion] = useState(0);
  const [detectionLabelSearch, setDetectionLabelSearch] = useState('');
  const [selectedDetection, setSelectedDetection] = useState<any | null>(null);
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
    static: true,
    grid: true,
  });
  const [isLoading, setIsLoading] = useState(false);

  const selectedImageryData = imagery.find((img) => img.id === selectedImagery);
  const processingUploads = useMemo(
    () => uploadJobs.filter((job) => job.media_type === 'imagery' && isUploadActive(job)).slice(0, 3),
    [uploadJobs],
  );

  const detectionLabelStats = useMemo(() => {
    const classMeta = new Map(detectionClasses.map((item) => [item.class, item]));
    const stats = new Map<string, { label: string; count: number; maxConfidence: number; color: string; ontology?: any; threatLevel?: string; rawClass: string }>();
    for (const meta of detectionClasses) {
      const rawClass = String(meta.class || meta.label || 'Unknown');
      stats.set(rawClass, {
        label: meta?.label || rawClass,
        rawClass,
        count: Number(meta?.count || 0),
        maxConfidence: Number(meta?.max_confidence || 0),
        color: detectionColor(rawClass),
        ontology: meta?.ontology,
        threatLevel: meta?.threat_level,
      });
    }
    for (const feature of detectionsGeoJSON.features || []) {
      const rawClass = detectionLabel(feature);
      const meta = classMeta.get(rawClass);
      const existing = stats.get(rawClass) || {
        label: meta?.label || rawClass,
        rawClass,
        count: Number(meta?.count || 0),
        maxConfidence: 0,
        color: detectionColor(rawClass),
        ontology: meta?.ontology || feature?.properties?.ontology,
        threatLevel: meta?.threat_level || feature?.properties?.threat_level,
      };
      if (!meta) existing.count += 1;
      existing.maxConfidence = Math.max(existing.maxConfidence, confidenceValue(feature));
      stats.set(rawClass, existing);
    }
    return Array.from(stats.values()).sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
  }, [detectionsGeoJSON, detectionClasses]);

  const filteredDetectionsGeoJSON = useMemo(() => ({
    ...detectionsGeoJSON,
    features: (detectionsGeoJSON.features || []).filter((feature: any) => {
      const label = detectionLabel(feature);
      if (detectionClassFilter && label !== detectionClassFilter) return false;
      return !hiddenDetectionLabels.includes(label);
    }),
  }), [detectionsGeoJSON, detectionClassFilter, hiddenDetectionLabels]);

  const filteredDetectionLabelStats = useMemo(() => {
    const query = detectionLabelSearch.trim().toLowerCase();
    return query
      ? detectionLabelStats.filter((item) => `${item.label} ${item.ontology?.category || ''} ${item.threatLevel || ''}`.toLowerCase().includes(query))
      : detectionLabelStats;
  }, [detectionLabelSearch, detectionLabelStats]);

  const maxDetectionLabelCount = Math.max(1, ...detectionLabelStats.map((item) => item.count));
  const visibleDetectionCount = filteredDetectionsGeoJSON.features?.length || 0;
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
      const rows = response.data.imagery || [];
      setImagery(rows);
      setSelectedImagery((current) => (current && rows.some((row: any) => row.id === current) ? current : rows[0]?.id || null));
    } catch (error) {
      console.error('Error fetching imagery:', error);
    }
  }, [timeRange]);

  const fetchDetections = useCallback(async () => {
    if (!mapBounds) return;
    setIsLoading(true);
    try {
      const classParams = new URLSearchParams({ start_time: timeRange.start, end_time: timeRange.end, llm: 'true' });
      const geoParams = new URLSearchParams({ start_time: timeRange.start, end_time: timeRange.end });
      if (detectionClassFilter) {
        geoParams.set('det_class', detectionClassFilter);
      } else {
        geoParams.set('bbox', mapBounds);
      }
      const [geojsonResponse, classResponse] = await Promise.allSettled([
        axios.get(`${API_URL}/api/detections/geojson?${geoParams.toString()}`, { timeout: 10000 }),
        axios.get(`${API_URL}/api/detections/classes?${classParams.toString()}`, { timeout: 10000 }),
      ]);
      if (geojsonResponse.status === 'fulfilled') {
        setDetectionsGeoJSON(geojsonResponse.value.data || { type: 'FeatureCollection', features: [] });
        setDetectionsLayerVersion((version) => version + 1);
      }
      if (classResponse.status === 'fulfilled') {
        setDetectionClasses(classResponse.value.data?.classes || []);
      }
    } catch (error) {
      console.error('Error fetching detections:', error);
    } finally {
      setIsLoading(false);
    }
  }, [detectionClassFilter, mapBounds, timeRange]);

  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => { fetchUploadJobs(); }, [fetchUploadJobs]);
  useEffect(() => { fetchImagery(); }, [fetchImagery]);
  useEffect(() => { fetchDetections(); }, [fetchDetections]);

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
    fetchImagery();
    fetchUploadJobs();
  }, [focusTimeRange, fetchDetections, fetchImagery, fetchUploadJobs]));
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

  const openWorkbench = useCallback(async () => {
    setIsActionBusy(true);
    setActionStatus('Opening target workbench...');
    try {
      onOpenWorkbench?.();
    } catch (error) {
      console.error('Open workbench failed:', error);
      setActionStatus('Open workbench failed.');
    } finally {
      setIsActionBusy(false);
    }
  }, [onOpenWorkbench]);

  const onEachDetection = (feature: any, layer: L.Layer) => {
    const props = feature.properties;
    layer.bindPopup(`
      <div style="font-family: sans-serif; min-width: 210px;">
        <div style="font-weight: 700; font-size: 13px; margin-bottom: 8px; color: #e8ebee; border-bottom: 1px solid #373e46; padding-bottom: 4px;">
          ${props.label || props.class}
        </div>
        <div style="font-size: 12px; color: #aab2bb; line-height: 1.6;">
          <div>ID: <span style="color:#e8ebee">${props.id}</span></div>
          <div>Class: <span style="color:#e8ebee">${props.class}</span></div>
          <div>Confidence: <span style="color:#e8ebee">${(Number(props.confidence || 0) * 100).toFixed(1)}%</span></div>
          <div>Threat: <span style="color:#e8ebee">${props.threat_level || 'unknown'}</span></div>
          <div>Tag: <span style="color:#e8ebee">${props.allegiance || 'unknown'}</span></div>
        </div>
      </div>
    `);
    layer.bindTooltip(String(props.label || props.class || 'Detection'), {
      permanent: true,
      direction: 'center',
      className: 'sentinel-detection-label',
      opacity: 0.95,
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
              <span className="sentinel-label flex-1">Detection Classes / {detectionLabelStats.length}</span>
              <button
                type="button"
                className="sentinel-btn h-6"
                onClick={() => {
                  setDetectionClassFilter(null);
                  setHiddenDetectionLabels([]);
                }}
              >
                ALL
              </button>
              <button
                type="button"
                className="sentinel-btn h-6"
                onClick={() => {
                  setDetectionClassFilter(null);
                  setHiddenDetectionLabels(detectionLabelStats.map((item) => item.rawClass));
                }}
              >
                NONE
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

          {filteredDetectionLabelStats.length === 0 && (
            <div className="p-4 text-xs text-sentinel-muted">No detections in current view.</div>
          )}

          {filteredDetectionLabelStats.map((item) => {
            const hidden = Boolean(detectionClassFilter && detectionClassFilter !== item.rawClass) || hiddenDetectionLabels.includes(item.rawClass);
            const solo = detectionClassFilter === item.rawClass;
            return (
              <button
                key={item.rawClass}
                type="button"
                onClick={() => {
                  setDetectionClassFilter(item.rawClass);
                  setHiddenDetectionLabels(detectionLabelStats.filter((stat) => stat.rawClass !== item.rawClass).map((stat) => stat.rawClass));
                }}
                className={`w-full border-b border-sentinel-line px-3 py-2 text-left ${hidden ? 'text-sentinel-muted' : 'text-slate-200'}`}
              >
                <div className="grid grid-cols-[18px_1fr_auto_auto] items-center gap-2">
                  <span style={{ color: hidden ? 'var(--ink-2)' : item.color }}>{hidden ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}</span>
                  <span className="truncate text-xs">{item.label}{solo ? ' / SOLO' : ''}</span>
                  <span className={`sentinel-tag ${threatClass(item.threatLevel)}`}>{item.threatLevel || 'low'}</span>
                  <span className="font-mono text-[10px]" style={{ color: hidden ? 'var(--ink-2)' : item.color }}>{item.count}</span>
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <div className="h-1.5 flex-1 bg-sentinel-bg">
                    <div className="h-full" style={{ width: `${Math.max(4, (item.count / maxDetectionLabelCount) * 100)}%`, backgroundColor: item.color }} />
                  </div>
                  <span className="w-10 text-right font-mono text-[10px] text-sentinel-muted">{Math.round(item.maxConfidence * 100)}%</span>
                </div>
              </button>
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
            style={{ height: '100%', width: '100%', background: '#0b0d0f' }}
            zoomControl={false}
          >
            <ZoomControl position="bottomright" />
            <MapBoundsUpdater onBoundsChange={setMapBounds} />
            <MapCursorTracker onCursorChange={setCursor} />
            <MapFitToImagery imagery={selectedImageryData} />
            <MapFitToDetections geojson={filteredDetectionsGeoJSON} enabled={Boolean(detectionClassFilter)} />

            <ImageOverlay url="/world_map.svg" bounds={[[-85, -180], [85, 180]]} opacity={0.5} />

            {activeLayers.grid && (
              <GeoJSON
                data={basemapGeoJSON}
                style={() => ({
                  color: '#4ea1ff',
                  weight: 1,
                  opacity: 0.82,
                  fillColor: '#1d2227',
                  fillOpacity: 0.18,
                })}
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
                    <Popup className="gotham-popup">
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

            {activeLayers.detections && showBbox && filteredDetectionsGeoJSON.features?.length > 0 && (
              <GeoJSON
                key={`detections-${detectionsLayerVersion}-${detectionClassFilter || 'all'}-${hiddenDetectionLabels.join('|')}-${filteredDetectionsGeoJSON.features.length}`}
                data={filteredDetectionsGeoJSON}
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
                      <Popup className="gotham-popup">
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
          </MapContainer>

          <div className="pointer-events-none absolute inset-0">
            <div className="sentinel-grid" />
            <div className="absolute left-2 top-2 font-mono text-[10px] tracking-wider text-sentinel-muted">WGS84 / MERCATOR / LIVE COP</div>
            <div className="absolute right-2 top-2 font-mono text-[10px] tracking-wider text-sentinel-muted">AOR / CURRENT VIEW</div>
            <div className="absolute left-1/2 top-8 -translate-x-1/2 border border-sentinel-line-2 bg-sentinel-panel px-3 py-1 font-mono text-[11px]">
              <span className="text-sentinel-accent">{visibleDetectionCount}</span>
              <span className="text-sentinel-muted"> / {detectionsGeoJSON.features?.length || 0} detections / last {timelineWindowMinutes}m</span>
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
                <div className="font-mono text-[10px] text-sentinel-muted">DET-{selectedDetection.properties?.id} / {selectedDetection.properties?.class}</div>
                <div className="mt-1 text-lg font-semibold text-slate-100">{selectedDetection.properties?.label || selectedDetection.properties?.class}</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <span className={`sentinel-tag ${threatClass(selectedDetection.properties?.threat_level)}`}>{selectedDetection.properties?.threat_level || 'low'}</span>
                  <span className="sentinel-tag">{selectedDetection.properties?.allegiance || 'unknown'}</span>
                  <span className="sentinel-tag info">{Math.round(Number(selectedDetection.properties?.confidence || 0) * 100)}% CONF</span>
                </div>
              </div>
              <div className="border-b border-sentinel-line p-3 text-xs leading-relaxed text-sentinel-muted">
                {selectedDetection.properties?.ontology?.description || 'Detection ontology unavailable.'}
                <div className="mt-2 grid grid-cols-[92px_1fr] gap-y-1 font-mono text-[10px]">
                  <span>ASSESS</span><span>{selectedDetection.properties?.assessment_status || selectedDetection.properties?.ontology?.assessment_status || 'unconfirmed'}</span>
                  <span>THREAT SCORE</span><span>{Number(selectedDetection.properties?.threat_confidence || selectedDetection.properties?.ontology?.threat_confidence || 0).toFixed(2)}</span>
                  <span>EVIDENCE</span><span>{(selectedDetection.properties?.evidence || selectedDetection.properties?.ontology?.evidence || []).join(' / ') || 'none'}</span>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2 border-b border-sentinel-line p-3">
                <button type="button" disabled={isActionBusy} onClick={() => tagDetection(selectedDetection.properties.id, 'friendly')} className="sentinel-btn justify-center disabled:opacity-40"><Shield className="h-3.5 w-3.5" /> Friendly</button>
                <button type="button" disabled={isActionBusy} onClick={() => tagDetection(selectedDetection.properties.id, 'hostile')} className="sentinel-btn justify-center disabled:opacity-40"><Swords className="h-3.5 w-3.5" /> Hostile</button>
                <button type="button" disabled={isActionBusy} onClick={() => tagDetection(selectedDetection.properties.id, 'neutral')} className="sentinel-btn justify-center disabled:opacity-40"><CircleHelp className="h-3.5 w-3.5" /> Neutral</button>
                <button type="button" disabled={isActionBusy} onClick={() => tagDetection(selectedDetection.properties.id, 'unknown')} className="sentinel-btn justify-center disabled:opacity-40">Clear</button>
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
            <button
              type="button"
              disabled={isActionBusy}
              onClick={openWorkbench}
              className="sentinel-btn w-full justify-center disabled:cursor-not-allowed disabled:opacity-40"
            >
              <FileText className="h-3.5 w-3.5" /> Open Workbench
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

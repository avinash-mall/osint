import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Circle, ImageOverlay, MapContainer, Marker, Polyline, Popup, TileLayer, useMap, ZoomControl } from 'react-leaflet';
import L from 'leaflet';
import {
  Activity,
  BarChart3,
  Box,
  BrainCircuit,
  Cable,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  ClipboardList,
  Clock3,
  Crosshair,
  DatabaseZap,
  Eye,
  FileImage,
  FileText,
  Film,
  Layers,
  ListFilter,
  Map as MapIcon,
  RadioTower,
  RefreshCw,
  Route,
  Satellite,
  Search,
  Target,
  UploadCloud,
  X,
} from 'lucide-react';
import 'leaflet/dist/leaflet.css';
import { useEventStream } from '../hooks/useEventStream';
import View3D from './View3D';
import { type UploadJob, uploadMessage, uploadProgress, uploadProgressClass, uploadStage } from '../utils/uploadProgress';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080';
const TILE_PROXY_URL = import.meta.env.VITE_TILE_PROXY_URL || 'http://localhost:8090';

delete (L.Icon.Default.prototype as any)._getIconUrl;

interface Aimpoint {
  id: string;
  label: string;
  latitude: number;
  longitude: number;
  radius_m?: number;
}

interface CollectionTask {
  id: number;
  target_id: string;
  target_name?: string;
  asset_type: string;
  priority?: string;
  queue?: string;
  status: string;
  created_at: string;
  updated_at: string;
}

interface OpsTarget {
  id: string;
  properties: Record<string, any>;
  aipoints: Aimpoint[];
  readiness: 'ready' | 'tasked' | string;
  queue: string;
  task_count: number;
  collection_tasks: CollectionTask[];
}

interface FeedSource {
  id: number;
  name: string;
  feed_type: string;
  protocol: string;
  endpoint: string;
  parser?: string;
  enabled: boolean;
  status: string;
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

interface FmvClip {
  id: number;
  name: string;
  stream_url?: string;
  duration_seconds?: number;
  status: string;
}

type UtilityPanel = 'layers' | 'fmv' | 'analytics' | 'ped' | 'reports' | 'models';

function opsIcon(color: string, selected = false) {
  return L.divIcon({
    className: '',
    iconSize: selected ? [34, 34] : [26, 26],
    iconAnchor: selected ? [17, 17] : [13, 13],
    html: `
      <div style="
        width: ${selected ? 34 : 26}px;
        height: ${selected ? 34 : 26}px;
        border: 1px solid ${color};
        background: rgba(2, 6, 23, 0.86);
        box-shadow: 0 0 ${selected ? 18 : 10}px ${color}66;
        transform: rotate(45deg);
        display: grid;
        place-items: center;
      ">
        <div style="
          width: ${selected ? 11 : 8}px;
          height: ${selected ? 11 : 8}px;
          border-radius: 999px;
          background: ${color};
          transform: rotate(-45deg);
        "></div>
      </div>
    `,
  });
}

function aimpointIcon() {
  return L.divIcon({
    className: '',
    iconSize: [18, 18],
    iconAnchor: [9, 9],
    html: '<div style="width:18px;height:18px;border:1px solid #a3e635;background:rgba(22,101,52,.35);box-shadow:0 0 10px #a3e63588;"></div>',
  });
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

function priorityColor(priority?: string) {
  switch ((priority || '').toLowerCase()) {
    case 'high':
      return 'border-rose-500/60 bg-rose-500/10 text-rose-200';
    case 'medium':
      return 'border-amber-400/60 bg-amber-400/10 text-amber-100';
    case 'low':
      return 'border-sky-400/60 bg-sky-400/10 text-sky-100';
    default:
      return 'border-slate-600 bg-slate-800 text-slate-300';
  }
}

function MapFocus({ selected }: { selected: OpsTarget | null }) {
  const map = useMap();
  useEffect(() => {
    const latLon = targetLatLon(selected);
    if (latLon) {
      map.flyTo(latLon, Math.max(map.getZoom(), 5), { duration: 0.7 });
    }
  }, [map, selected]);
  return null;
}

function imageryBounds(imagery?: ImageryRow | null): L.LatLngBounds | null {
  if (!imagery?.footprint_geojson) return null;
  try {
    const geometry = typeof imagery.footprint_geojson === 'string'
      ? JSON.parse(imagery.footprint_geojson)
      : imagery.footprint_geojson;
    const bounds = L.geoJSON(geometry as any).getBounds();
    return bounds.isValid() ? bounds : null;
  } catch {
    return null;
  }
}

function MapFitToImagery({ imagery }: { imagery: ImageryRow | null }) {
  const map = useMap();
  useEffect(() => {
    const bounds = imageryBounds(imagery);
    if (bounds) {
      map.fitBounds(bounds.pad(0.15), { animate: true, maxZoom: 13 });
    }
  }, [map, imagery?.id]);
  return null;
}

export default function OperationsWorkspace() {
  const [targets, setTargets] = useState<OpsTarget[]>([]);
  const [summary, setSummary] = useState({ total: 0, ready: 0, tasked: 0 });
  const [selectedTargetId, setSelectedTargetId] = useState<string>('');
  const [query, setQuery] = useState('');
  const [queueFilter, setQueueFilter] = useState<'ready' | 'tasked' | 'all'>('ready');
  const [viewMode, setViewMode] = useState<'map' | 'globe'>('map');
  const [feeds, setFeeds] = useState<FeedSource[]>([]);
  const [imagery, setImagery] = useState<ImageryRow[]>([]);
  const [uploadJobs, setUploadJobs] = useState<UploadJob[]>([]);
  const [fmvClips, setFmvClips] = useState<FmvClip[]>([]);
  const [analyticsJobs, setAnalyticsJobs] = useState<any[]>([]);
  const [pedTasks, setPedTasks] = useState<any[]>([]);
  const [requirements, setRequirements] = useState<any[]>([]);
  const [reports, setReports] = useState<any[]>([]);
  const [models, setModels] = useState<any[]>([]);
  const [selectedImageryId, setSelectedImageryId] = useState<number | null>(null);
  const [activePanel, setActivePanel] = useState<UtilityPanel>('layers');
  const [imageryOpacity, setImageryOpacity] = useState(0.72);
  const [showDetections, setShowDetections] = useState(true);
  const [showAimpoints, setShowAimpoints] = useState(true);
  const [showRanges, setShowRanges] = useState(true);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [sensorType, setSensorType] = useState('Optical');
  const [autoProcess, setAutoProcess] = useState(true);
  const [busyAction, setBusyAction] = useState('');
  const [statusLine, setStatusLine] = useState('Ops workspace ready.');
  const [feedForm, setFeedForm] = useState({
    name: 'AIS Gulf Feed',
    feed_type: 'AIS',
    protocol: 'tcp',
    endpoint: 'tcp://localhost:4002',
    parser: 'nmea',
  });
  const [events, setEvents] = useState<any[]>([
    { type: 'workspace_loaded', message: 'Target operations console initialized', at: new Date().toISOString() },
  ]);

  const selectedTarget = useMemo(
    () => targets.find((target) => target.id === selectedTargetId) || targets[0] || null,
    [selectedTargetId, targets],
  );

  const selectedImagery = useMemo(
    () => imagery.find((row) => row.id === selectedImageryId) || null,
    [imagery, selectedImageryId],
  );
  const imageryUploadJobs = useMemo(
    () => uploadJobs.filter((job) => job.media_type === 'imagery'),
    [uploadJobs],
  );

  const refreshOps = useCallback(async () => {
    const [
      targetResponse,
      feedResponse,
      imageryResponse,
      uploadResponse,
      fmvResponse,
      analyticsResponse,
      pedResponse,
      requirementResponse,
      reportResponse,
      modelResponse,
    ] = await Promise.all([
      axios.get(`${API_URL}/api/ops/targets`),
      axios.get(`${API_URL}/api/feeds`),
      axios.get(`${API_URL}/api/imagery`),
      axios.get(`${API_URL}/api/ingest/uploads`),
      axios.get(`${API_URL}/api/fmv/clips`),
      axios.get(`${API_URL}/api/analytics/jobs`),
      axios.get(`${API_URL}/api/ped/tasks`),
      axios.get(`${API_URL}/api/collection/requirements`),
      axios.get(`${API_URL}/api/reports`),
      axios.get(`${API_URL}/api/models`),
    ]);
    const nextTargets = targetResponse.data.targets || [];
    setTargets(nextTargets);
    setSummary(targetResponse.data.summary || { total: nextTargets.length, ready: 0, tasked: 0 });
    setFeeds(feedResponse.data.feeds || []);
    const imageryRows = imageryResponse.data.imagery || [];
    setImagery(imageryRows);
    setUploadJobs(uploadResponse.data.uploads || []);
    setFmvClips(fmvResponse.data.clips || []);
    setAnalyticsJobs(analyticsResponse.data.jobs || []);
    setPedTasks(pedResponse.data.tasks || []);
    setRequirements(requirementResponse.data.requirements || []);
    setReports(reportResponse.data.reports || []);
    setModels(modelResponse.data.models || []);
    setSelectedImageryId((current) => (current && imageryRows.some((row: ImageryRow) => row.id === current) ? current : imageryRows[0]?.id || null));
    setSelectedTargetId((current) => current || nextTargets[0]?.id || '');
  }, []);

  useEffect(() => {
    refreshOps().catch((error) => {
      console.error('Ops refresh failed:', error);
      setStatusLine('Unable to refresh target operations data.');
    });
  }, [refreshOps]);

  useEventStream('ops', useCallback((message: any) => {
    setEvents((prev) => [
      { ...message, at: new Date().toISOString(), message: message.type || 'ops_event' },
      ...prev,
    ].slice(0, 8));
    refreshOps().catch(() => undefined);
  }, [refreshOps]));

  useEventStream('imagery', useCallback((message: any) => {
    setEvents((prev) => [
      { ...message, at: new Date().toISOString(), message: message.type || 'imagery_event' },
      ...prev,
    ].slice(0, 8));
    refreshOps().catch(() => undefined);
  }, [refreshOps]));

  const filteredTargets = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return targets.filter((target) => {
      const props = target.properties || {};
      const matchesQuery = !normalized || [
        props.name,
        props.description,
        props.priority,
        props.type,
        target.queue,
      ].some((value) => String(value || '').toLowerCase().includes(normalized));
      const matchesQueue = queueFilter === 'all' || target.readiness === queueFilter;
      return matchesQuery && matchesQueue;
    });
  }, [query, queueFilter, targets]);

  const readyTargets = filteredTargets.filter((target) => target.readiness === 'ready');
  const taskedTargets = filteredTargets.filter((target) => target.readiness === 'tasked');

  const uploadImagery = async () => {
    if (!uploadFile || busyAction) return;
    setBusyAction('upload');
    setStatusLine('Uploading imagery collection...');
    try {
      const form = new FormData();
      form.append('file', uploadFile);
      form.append('sensor_type', sensorType);
      form.append('auto_process', String(autoProcess));
      const response = await axios.post(`${API_URL}/api/ingest/upload`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setUploadFile(null);
      setStatusLine(autoProcess ? `Upload queued: ${response.data.task_id}` : `Upload stored: ${response.data.filename}`);
      await refreshOps();
    } catch (error: any) {
      setStatusLine(error.response?.data?.detail || 'Imagery upload failed.');
    } finally {
      setBusyAction('');
    }
  };

  const connectStream = async () => {
    if (busyAction) return;
    setBusyAction('feed');
    setStatusLine('Connecting stream source...');
    try {
      const response = await axios.post(`${API_URL}/api/feeds/connect`, {
        ...feedForm,
        topic: 'feeds',
        enabled: true,
      });
      setStatusLine(`Connected source: ${response.data.feed.name}`);
      await refreshOps();
    } catch (error: any) {
      setStatusLine(error.response?.data?.detail || 'Stream connection failed.');
    } finally {
      setBusyAction('');
    }
  };

  const taskAsset = async () => {
    if (!selectedTarget || busyAction) return;
    setBusyAction('task');
    setStatusLine(`Tasking collection asset for ${selectedTarget.properties.name || 'target'}...`);
    try {
      const response = await axios.post(`${API_URL}/api/collection/tasks`, {
        target_id: selectedTarget.id,
        target_name: selectedTarget.properties.name,
        asset_type: 'ISR',
        priority: selectedTarget.properties.priority,
        queue: selectedTarget.queue,
        notes: 'Created from Target Ops workspace.',
        aipoints: selectedTarget.aipoints || [],
      });
      setStatusLine(`Task proposed: #${response.data.task.id}`);
      await refreshOps();
    } catch (error: any) {
      setStatusLine(error.response?.data?.detail || 'Unable to task asset.');
    } finally {
      setBusyAction('');
    }
  };

  const createRequirement = async () => {
    if (!selectedTarget || busyAction) return;
    setBusyAction('requirement');
    setStatusLine('Creating collection requirement...');
    try {
      const response = await axios.post(`${API_URL}/api/collection/requirements`, {
        title: `Collect ${selectedTarget.properties.name || 'selected target'}`,
        description: 'Generated from Target Ops workspace.',
        priority: selectedTarget.properties.priority || 'Medium',
        status: 'approved',
        target_id: selectedTarget.id,
        aoi: { aimpoints: selectedTarget.aipoints || [] },
      });
      setStatusLine(`Requirement CR-${response.data.requirement.id} created.`);
      setActivePanel('ped');
      await refreshOps();
    } catch (error: any) {
      setStatusLine(error.response?.data?.detail || 'Unable to create requirement.');
    } finally {
      setBusyAction('');
    }
  };

  const runAnalytics = async (kind: 'change' | 'viewshed' | 'los' | 'routes' | 'pol') => {
    if (busyAction) return;
    setBusyAction(kind);
    setStatusLine(`Running ${kind} analytic...`);
    try {
      const latLon = selectedLatLon || [25.078, 55.179];
      const payload = {
        target_id: selectedTarget?.id,
        observer: { latitude: latLon[0], longitude: latLon[1] },
        destination: { latitude: latLon[0] + 0.08, longitude: latLon[1] + 0.08 },
        radius_m: 5000,
      };
      const response = await axios.post(`${API_URL}/api/analytics/${kind}`, payload);
      setStatusLine(`${kind} analytic complete: job #${response.data.job.id}`);
      setActivePanel('analytics');
      await refreshOps();
    } catch (error: any) {
      setStatusLine(error.response?.data?.detail || `${kind} analytic failed.`);
    } finally {
      setBusyAction('');
    }
  };

  const createReport = async () => {
    if (!selectedTarget || busyAction) return;
    setBusyAction('report');
    setStatusLine('Generating target package...');
    try {
      const response = await axios.post(`${API_URL}/api/reports/target-packages`, {
        target_id: selectedTarget.id,
        title: `Target Package - ${selectedTarget.properties.name || selectedTarget.id}`,
      });
      setStatusLine(`Report ready: #${response.data.report.id}`);
      setActivePanel('reports');
      await refreshOps();
    } catch (error: any) {
      setStatusLine(error.response?.data?.detail || 'Report generation failed.');
    } finally {
      setBusyAction('');
    }
  };

  const queueTraining = async () => {
    if (busyAction) return;
    setBusyAction('training');
    setStatusLine('Queueing local training job...');
    try {
      const response = await axios.post(`${API_URL}/api/training/jobs`, {
        name: `Ops local refresh ${new Date().toLocaleTimeString()}`,
        epochs: 1,
      });
      setStatusLine(`Training job queued: #${response.data.job.id}`);
      setActivePanel('models');
      await refreshOps();
    } catch (error: any) {
      setStatusLine(error.response?.data?.detail || 'Training queue failed.');
    } finally {
      setBusyAction('');
    }
  };

  const selectedLatLon = targetLatLon(selectedTarget);
  const mapCenter: [number, number] = selectedLatLon || [25.0, 55.0];

  return (
    <div className="h-full min-h-0 w-full bg-black text-slate-200 overflow-hidden grid grid-cols-[390px_minmax(0,1fr)_330px]">
      <aside className="h-full border-r border-lime-500/30 bg-slate-950/96 flex flex-col min-w-0">
        <div className="h-11 border-b border-lime-500/30 flex items-center gap-2 px-3 text-xs uppercase tracking-wider">
          <CircleDot className="w-4 h-4 text-lime-300" />
          <button className="h-7 px-2 border border-lime-500/40 text-lime-200 bg-lime-500/10 flex items-center gap-2">
            <Target className="w-3.5 h-3.5" /> Targets
          </button>
          <button className="h-7 w-8 border border-slate-700 text-slate-400 grid place-items-center" title="Filters">
            <ListFilter className="w-3.5 h-3.5" />
          </button>
          <button className="h-7 w-8 border border-slate-700 text-slate-400 grid place-items-center" title="Air tasking">
            <Satellite className="w-3.5 h-3.5" />
          </button>
          <div className="ml-auto text-lime-300">
            <Search className="w-4 h-4" />
          </div>
        </div>

        <div className="p-3 border-b border-slate-800">
          <div className="grid grid-cols-[86px_minmax(0,1fr)_24px] gap-2 items-center">
            <div className="text-[11px] text-slate-400">Collection:</div>
            <select className="h-8 bg-slate-800 border border-slate-600 text-xs px-2 text-slate-200">
              <option>OP RADIANT SPHERE</option>
              <option>LOCAL DEMO COLLECTION</option>
            </select>
            <button className="h-8 border border-slate-700 grid place-items-center text-slate-400" title="Clear">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        <div className="p-3 border-b border-slate-800">
          <div className="relative">
            <Search className="absolute left-3 top-2.5 w-4 h-4 text-slate-500" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search targets..."
              className="w-full h-9 bg-slate-900 border border-slate-700 pl-9 pr-3 text-sm text-slate-200 outline-none focus:border-lime-500/70"
            />
          </div>
          <div className="grid grid-cols-3 gap-1 mt-3 text-[11px]">
            {(['ready', 'tasked', 'all'] as const).map((key) => (
              <button
                key={key}
                onClick={() => setQueueFilter(key)}
                className={`h-7 uppercase border ${queueFilter === key ? 'border-lime-400 text-lime-200 bg-lime-500/10' : 'border-slate-700 text-slate-400 bg-slate-900'}`}
              >
                {key}
              </button>
            ))}
          </div>
        </div>

        <div className="px-3 py-2 border-b border-slate-800 text-xs">
          <div className="flex items-center justify-between h-8 text-slate-300">
            <span className="flex items-center gap-2"><ChevronRight className="w-4 h-4 text-blue-300" /> Ready to Task</span>
            <span className="rounded-full bg-blue-500 px-2 py-0.5 text-white text-[10px]">{summary.ready}</span>
          </div>
          <div className="flex items-center justify-between h-8 text-slate-400">
            <span className="flex items-center gap-2"><CheckCircle2 className="w-4 h-4 text-emerald-300" /> Tasked</span>
            <span className="rounded-full bg-emerald-500/80 px-2 py-0.5 text-white text-[10px]">{summary.tasked}</span>
          </div>
        </div>

        <div className="flex-1 overflow-auto custom-scrollbar p-2 space-y-2">
          {(queueFilter === 'tasked' ? taskedTargets : queueFilter === 'ready' ? readyTargets : filteredTargets).map((target) => {
            const props = target.properties || {};
            const selected = selectedTarget?.id === target.id;
            return (
              <button
                key={target.id}
                onClick={() => setSelectedTargetId(target.id)}
                className={`w-full text-left border p-3 transition ${selected ? 'border-lime-400 bg-lime-500/12 shadow-[inset_3px_0_0_#a3e635]' : 'border-slate-800 bg-slate-900/75 hover:border-slate-600'}`}
              >
                <div className="flex items-start gap-3">
                  <div className={`mt-0.5 h-6 w-6 border grid place-items-center ${selected ? 'border-rose-400 text-rose-300' : 'border-rose-500/60 text-rose-400'}`}>
                    <Crosshair className="w-3.5 h-3.5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <div className="font-semibold text-sm truncate">{props.name || target.id}</div>
                      <span className="text-xs text-slate-400">{props.priority === 'High' ? 'P5' : 'P3'}</span>
                    </div>
                    <div className="text-xs text-slate-400 truncate">{props.type || 'Building'}{props.category ? ` - ${props.category}` : ''}</div>
                    {target.aipoints?.length > 0 && (
                      <div className="mt-2 border-t border-slate-800 pt-2 text-[11px] text-slate-400">
                        <div className="flex items-center gap-1 text-slate-300 mb-1">
                          <ChevronRight className="w-3 h-3 rotate-90" /> Aimpoints ({target.aipoints.length})
                        </div>
                        <div className="grid grid-cols-2 gap-x-2 gap-y-1 font-mono">
                          {target.aipoints.slice(0, 3).map((point) => (
                            <span key={point.id} className="truncate">{point.id}</span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
                <div className="mt-3 flex items-center justify-between text-[10px]">
                  <span className="text-slate-500">{target.queue}</span>
                  <span className={`px-2 py-0.5 border ${target.readiness === 'tasked' ? 'border-emerald-500/50 text-emerald-300' : 'border-blue-500/50 text-blue-300'}`}>
                    {target.readiness === 'tasked' ? `${target.task_count} proposed tasking` : 'MTS/MNF'}
                  </span>
                </div>
              </button>
            );
          })}
          {filteredTargets.length === 0 && (
            <div className="h-32 grid place-items-center text-xs text-slate-500 font-mono">NO MATCHING TARGETS</div>
          )}
        </div>
      </aside>

      <main className="relative min-w-0 bg-black overflow-hidden">
        <div className="absolute top-0 left-1/2 -translate-x-1/2 z-[500] h-11 min-w-[680px] max-w-[80%] border-x border-b border-lime-500/30 bg-slate-950/92 flex items-center text-xs">
          <div className="h-full px-3 border-r border-lime-500/30 flex items-center font-bold text-slate-300">SELECTED:</div>
          <div className="h-full px-3 border-r border-slate-800 flex items-center gap-3 min-w-0 flex-1">
            <Crosshair className="w-5 h-5 text-rose-400" />
            <div className="min-w-0">
              <div className="font-semibold text-slate-100 truncate">{selectedTarget?.properties.name || 'No target selected'}</div>
              <div className="text-[10px] text-slate-500 truncate">{selectedTarget?.properties.type || 'Target'}{selectedTarget?.properties.category ? ` - ${selectedTarget.properties.category}` : ''}</div>
            </div>
          </div>
          <button className="h-full w-11 border-r border-slate-800 grid place-items-center text-lime-300" title="Focus">
            <Crosshair className="w-4 h-4" />
          </button>
          <button className="h-full w-11 border-r border-slate-800 grid place-items-center text-slate-400" title="Watch">
            <Eye className="w-4 h-4" />
          </button>
          <div className="h-full px-3 border-r border-slate-800 flex items-center text-slate-300">Aimpoints ({selectedTarget?.aipoints?.length || 0})</div>
          <button
            onClick={taskAsset}
            disabled={!selectedTarget || busyAction === 'task'}
            className="h-8 mx-2 px-3 border border-lime-400/70 text-lime-100 bg-lime-500/10 flex items-center gap-2 disabled:opacity-50"
          >
            Task Asset <ChevronRight className="w-4 h-4" />
          </button>
        </div>

        <div className="absolute top-4 right-4 z-[500] border border-lime-500/30 bg-slate-950/90 flex flex-col">
          <button onClick={() => setViewMode('map')} className={`h-10 w-10 grid place-items-center border-b border-lime-500/20 ${viewMode === 'map' ? 'text-lime-300 bg-lime-500/10' : 'text-slate-400'}`} title="2D map">
            <MapIcon className="w-4 h-4" />
          </button>
          <button onClick={() => setViewMode('globe')} className={`h-10 w-10 grid place-items-center border-b border-lime-500/20 ${viewMode === 'globe' ? 'text-lime-300 bg-lime-500/10' : 'text-slate-400'}`} title="3D globe">
            <Box className="w-4 h-4" />
          </button>
          <button onClick={refreshOps} className="h-10 w-10 grid place-items-center text-slate-400" title="Refresh">
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>

        {viewMode === 'globe' ? (
          <View3D
            targets={targets}
            selectedTarget={selectedTarget}
            imagery={imagery}
            selectedImagery={selectedImagery}
            imageryOpacity={imageryOpacity}
            showAimpoints={showAimpoints}
            showRanges={showRanges}
            events={events}
            onSelectTarget={setSelectedTargetId}
          />
        ) : (
          <MapContainer center={mapCenter} zoom={selectedLatLon ? 5 : 4} style={{ height: '100%', width: '100%', background: '#020617' }} zoomControl={false}>
            <ZoomControl position="bottomright" />
            <MapFocus selected={selectedTarget} />
            <MapFitToImagery imagery={selectedImagery} />
            <ImageOverlay url="/world_map.svg" bounds={[[-85, -180], [85, 180]]} opacity={0.34} />
            {selectedImagery && (
              <TileLayer
                url={`${TILE_PROXY_URL}/cog/tiles/{z}/{x}/{y}?url=${encodeURIComponent(selectedImagery.file_path)}`}
                opacity={imageryOpacity}
                maxZoom={22}
              />
            )}

            {targets.map((target) => {
              const latLon = targetLatLon(target);
              if (!latLon) return null;
              const selected = selectedTarget?.id === target.id;
              const color = selected ? '#f87171' : target.readiness === 'tasked' ? '#34d399' : '#60a5fa';
              return (
                <Marker key={target.id} position={latLon} icon={opsIcon(color, selected)}>
                  <Popup className="gotham-popup">
                    <div className="bg-slate-950 text-slate-200 border border-slate-700 p-3 min-w-[210px]">
                      <div className="font-bold text-sm">{target.properties.name}</div>
                      <div className="text-xs text-slate-400 mt-1">{target.queue}</div>
                      <button
                        onClick={() => setSelectedTargetId(target.id)}
                        className="mt-3 h-7 px-3 border border-lime-500/50 text-lime-200 bg-lime-500/10 text-xs"
                      >
                        Select target
                      </button>
                    </div>
                  </Popup>
                </Marker>
              );
            })}

            {selectedTarget && showRanges && selectedLatLon && (
              <>
                <Circle center={selectedLatLon} radius={1000} pathOptions={{ color: '#bef264', fillOpacity: 0.04, weight: 1 }} />
                <Circle center={selectedLatLon} radius={5000} pathOptions={{ color: '#bef264', fillOpacity: 0.02, weight: 1, dashArray: '6 6' }} />
              </>
            )}

            {selectedTarget && showAimpoints && selectedTarget.aipoints?.map((point) => (
              <Circle
                key={`${point.id}-ring`}
                center={[point.latitude, point.longitude]}
                radius={point.radius_m || 120}
                pathOptions={{ color: '#a3e635', fillColor: '#a3e635', fillOpacity: 0.12, weight: 1 }}
              />
            ))}
            {selectedTarget && showAimpoints && selectedTarget.aipoints?.map((point) => (
              <Marker key={point.id} position={[point.latitude, point.longitude]} icon={aimpointIcon()}>
                <Popup>
                  <div className="text-xs">
                    <strong>{point.label}</strong>
                    <div>{point.id}</div>
                  </div>
                </Popup>
              </Marker>
            ))}
            {selectedTarget && showAimpoints && selectedLatLon && selectedTarget.aipoints?.length > 1 && (
              <Polyline
                positions={[selectedLatLon, ...selectedTarget.aipoints.map((point) => [point.latitude, point.longitude] as [number, number])]}
                pathOptions={{ color: '#a3e635', weight: 1, opacity: 0.45, dashArray: '3 8' }}
              />
            )}
          </MapContainer>
        )}

        <div className="absolute left-0 right-0 bottom-0 z-[500] h-20 border-t border-lime-500/20 bg-slate-950/92 px-4 py-2 flex items-center gap-4">
          <div className="w-36 text-xs uppercase tracking-wider text-slate-400 flex items-center gap-2">
            <Clock3 className="w-4 h-4 text-lime-300" /> Activity
          </div>
          <div className="flex-1 h-full flex items-center gap-2 overflow-hidden">
            {events.map((event, index) => (
              <div key={`${event.at}-${index}`} className="min-w-52 h-11 border border-slate-800 bg-slate-900/80 px-3 py-2">
                <div className="text-[10px] text-slate-500 font-mono">{new Date(event.at).toLocaleTimeString()}</div>
                <div className="text-xs text-slate-300 truncate">{event.message || event.type}</div>
              </div>
            ))}
          </div>
          <div className="w-64 text-xs text-lime-200 font-mono truncate">{statusLine}</div>
        </div>
      </main>

      <aside className="h-full border-l border-lime-500/30 bg-slate-950/96 flex flex-col min-w-0 overflow-hidden">
        <div className="h-11 border-b border-lime-500/30 px-3 flex items-center justify-between">
          <div className="text-xs uppercase tracking-wider text-slate-300 flex items-center gap-2">
            <Layers className="w-4 h-4 text-lime-300" /> Mission Layers
          </div>
          <span className="text-[10px] text-slate-500">{summary.total} targets</span>
        </div>

        <div className="grid grid-cols-6 border-b border-slate-800">
          {[
            { key: 'layers', icon: Layers, label: 'Layers' },
            { key: 'fmv', icon: Film, label: 'FMV' },
            { key: 'analytics', icon: BarChart3, label: 'Analytics' },
            { key: 'ped', icon: ClipboardList, label: 'PED' },
            { key: 'reports', icon: FileText, label: 'Reports' },
            { key: 'models', icon: BrainCircuit, label: 'Models' },
          ].map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                onClick={() => setActivePanel(item.key as UtilityPanel)}
                className={`h-10 grid place-items-center border-r border-slate-800 ${activePanel === item.key ? 'text-lime-300 bg-lime-500/10' : 'text-slate-500 hover:text-slate-200'}`}
                title={item.label}
              >
                <Icon className="w-4 h-4" />
              </button>
            );
          })}
        </div>

        {activePanel === 'layers' && <div className="p-3 border-b border-slate-800 space-y-2">
          <label className="flex items-center justify-between text-xs text-slate-300">
            <span className="flex items-center gap-2"><Crosshair className="w-3.5 h-3.5 text-lime-300" /> Aimpoints</span>
            <input type="checkbox" checked={showAimpoints} onChange={(event) => setShowAimpoints(event.target.checked)} />
          </label>
          <label className="flex items-center justify-between text-xs text-slate-300">
            <span className="flex items-center gap-2"><Activity className="w-3.5 h-3.5 text-blue-300" /> Range rings</span>
            <input type="checkbox" checked={showRanges} onChange={(event) => setShowRanges(event.target.checked)} />
          </label>
          <label className="flex items-center justify-between text-xs text-slate-300">
            <span className="flex items-center gap-2"><Eye className="w-3.5 h-3.5 text-emerald-300" /> AI detections</span>
            <input type="checkbox" checked={showDetections} onChange={(event) => setShowDetections(event.target.checked)} />
          </label>
        </div>}

        {activePanel === 'layers' && <div className="p-3 border-b border-slate-800">
          <div className="text-xs uppercase tracking-wider text-slate-400 mb-2 flex items-center gap-2">
            <FileImage className="w-4 h-4 text-blue-300" /> Upload Imagery
          </div>
          <label className="h-20 border border-dashed border-slate-700 bg-slate-900/80 hover:border-blue-400/60 grid place-items-center cursor-pointer">
            <div className="text-center">
              <UploadCloud className="w-6 h-6 text-slate-500 mx-auto mb-1" />
              <div className="text-xs text-slate-300 truncate max-w-64">{uploadFile ? uploadFile.name : 'Select imagery, FMV, or vector'}</div>
            </div>
            <input
              type="file"
              accept=".tif,.tiff,.jp2,.j2k,.nc,.netcdf,.png,.jpg,.jpeg,.mp4,.mov,.m4v,.ts,.geojson,.json,.kml,.kmz,.gpkg,.zip"
              className="hidden"
              onChange={(event) => setUploadFile(event.target.files?.[0] || null)}
            />
          </label>
          <div className="grid grid-cols-[1fr_auto] gap-2 mt-2">
            <select value={sensorType} onChange={(event) => setSensorType(event.target.value)} className="h-8 bg-slate-900 border border-slate-700 text-xs px-2">
              <option>Optical</option>
              <option>Radar</option>
              <option>Thermal</option>
              <option>MASINT</option>
              <option>FMV</option>
            </select>
            <label className="h-8 px-2 border border-slate-700 bg-slate-900 text-[11px] flex items-center gap-2">
              <input type="checkbox" checked={autoProcess} onChange={(event) => setAutoProcess(event.target.checked)} />
              Auto
            </label>
          </div>
          <button
            onClick={uploadImagery}
            disabled={!uploadFile || busyAction === 'upload'}
            className="mt-2 h-8 w-full border border-blue-500/50 bg-blue-500/15 text-blue-100 text-xs uppercase tracking-wider flex items-center justify-center gap-2 disabled:opacity-45"
          >
            <DatabaseZap className="w-3.5 h-3.5" /> Upload / Ingest
          </button>
        </div>}

        {activePanel === 'layers' && <div className="p-3 border-b border-slate-800">
          <div className="text-xs uppercase tracking-wider text-slate-400 mb-2 flex items-center gap-2">
            <RadioTower className="w-4 h-4 text-emerald-300" /> Connect Stream
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input value={feedForm.name} onChange={(event) => setFeedForm((prev) => ({ ...prev, name: event.target.value }))} className="col-span-2 h-8 bg-slate-900 border border-slate-700 text-xs px-2" />
            <select value={feedForm.feed_type} onChange={(event) => setFeedForm((prev) => ({ ...prev, feed_type: event.target.value }))} className="h-8 bg-slate-900 border border-slate-700 text-xs px-2">
              <option>AIS</option>
              <option>ADS-B</option>
              <option>RF/SIGINT</option>
              <option>FMV</option>
              <option>OSINT</option>
              <option>Webhook</option>
            </select>
            <select value={feedForm.protocol} onChange={(event) => setFeedForm((prev) => ({ ...prev, protocol: event.target.value }))} className="h-8 bg-slate-900 border border-slate-700 text-xs px-2">
              <option value="tcp">TCP</option>
              <option value="udp">UDP</option>
              <option value="http">HTTP</option>
              <option value="https">HTTPS</option>
              <option value="websocket">WebSocket</option>
              <option value="file">File</option>
              <option value="serial">Serial</option>
            </select>
            <select value={feedForm.parser} onChange={(event) => setFeedForm((prev) => ({ ...prev, parser: event.target.value }))} className="h-8 bg-slate-900 border border-slate-700 text-xs px-2">
              <option value="nmea">NMEA</option>
              <option value="json">JSON</option>
              <option value="csv">CSV</option>
              <option value="klv">MISB KLV</option>
              <option value="raw">Raw</option>
            </select>
            <input value={feedForm.endpoint} onChange={(event) => setFeedForm((prev) => ({ ...prev, endpoint: event.target.value }))} className="h-8 bg-slate-900 border border-slate-700 text-xs px-2 font-mono" />
          </div>
          <button
            onClick={connectStream}
            disabled={busyAction === 'feed'}
            className="mt-2 h-8 w-full border border-emerald-500/50 bg-emerald-500/15 text-emerald-100 text-xs uppercase tracking-wider flex items-center justify-center gap-2 disabled:opacity-45"
          >
            <Cable className="w-3.5 h-3.5" /> Connect Source
          </button>
        </div>}

        <div className="flex-1 overflow-auto custom-scrollbar p-3 space-y-3">
          {activePanel === 'layers' && <section>
            <div className="text-xs uppercase tracking-wider text-slate-400 mb-2 flex items-center justify-between">
              <span>Imagery</span>
              <span>{imagery.length}</span>
            </div>
            <div className="space-y-1">
              {imagery.slice(0, 5).map((row) => (
                <button
                  key={row.id}
                  onClick={() => setSelectedImageryId(selectedImageryId === row.id ? null : row.id)}
                  className={`w-full text-left border px-2 py-2 text-xs ${selectedImageryId === row.id ? 'border-blue-400 bg-blue-500/10 text-blue-100' : 'border-slate-800 bg-slate-900/70 text-slate-300'}`}
                >
                  <div className="font-semibold truncate">{row.name}</div>
                  <div className="text-[10px] text-slate-500 truncate">{row.sensor_type} {row.cloud_cover !== undefined ? `| ${row.cloud_cover}% cloud` : ''}</div>
                </button>
              ))}
              {selectedImagery && (
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={imageryOpacity}
                  onChange={(event) => setImageryOpacity(Number(event.target.value))}
                  className="w-full"
                />
              )}
              {imagery.length === 0 && <div className="text-xs text-slate-500 border border-slate-800 p-3">No imagery cataloged yet.</div>}
            </div>
          </section>}

          {activePanel === 'layers' && <section>
            <div className="text-xs uppercase tracking-wider text-slate-400 mb-2 flex items-center justify-between">
              <span>Recent Uploads</span>
              <span>{imageryUploadJobs.length}</span>
            </div>
            <div className="space-y-1">
              {imageryUploadJobs.slice(0, 5).map((job) => {
                const progress = uploadProgress(job);
                const message = uploadMessage(job);
                return (
                <div key={job.upload_id} className="border border-slate-800 bg-slate-900/70 px-2 py-2 text-xs">
                  <div className="font-semibold text-slate-300 truncate">{job.filename}</div>
                  <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-slate-500">
                    <span className="uppercase">{uploadStage(job)}</span>
                    <span>{progress}%</span>
                  </div>
                  <div className="mt-1 h-1.5 w-full bg-slate-800 overflow-hidden">
                    <div className={`h-full transition-all duration-500 ${uploadProgressClass(job)}`} style={{ width: `${progress}%` }} />
                  </div>
                  <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-slate-500">
                    <span className="truncate">{message || job.status}</span>
                    {job.celery_task_id && <span className="truncate">{job.celery_task_id.slice(0, 8)}</span>}
                  </div>
                </div>
                );
              })}
              {imageryUploadJobs.length === 0 && (
                <div className="text-xs text-slate-500 border border-slate-800 p-3">No imagery uploads yet.</div>
              )}
            </div>
          </section>}

          {activePanel === 'layers' && <section>
            <div className="text-xs uppercase tracking-wider text-slate-400 mb-2 flex items-center justify-between">
              <span>Active Sources</span>
              <span>{feeds.filter((feed) => feed.enabled).length}</span>
            </div>
            <div className="space-y-1">
              {feeds.slice(0, 6).map((feed) => (
                <div key={feed.id} className="border border-slate-800 bg-slate-900/70 px-2 py-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold truncate">{feed.name}</span>
                    <span className="text-emerald-300">{feed.status}</span>
                  </div>
                  <div className="text-[10px] text-slate-500 truncate">{feed.feed_type} / {feed.protocol} / {feed.parser || 'raw'}</div>
                </div>
              ))}
              {feeds.length === 0 && <div className="text-xs text-slate-500 border border-slate-800 p-3">No streams connected.</div>}
            </div>
          </section>}

          {activePanel === 'layers' && <section>
            <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">Selected Target</div>
            <div className="border border-slate-800 bg-slate-900/70 p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="font-semibold text-sm truncate">{selectedTarget?.properties.name || 'None'}</div>
                <span className={`text-[10px] px-2 py-0.5 border ${priorityColor(selectedTarget?.properties.priority)}`}>
                  {selectedTarget?.properties.priority || 'Unknown'}
                </span>
              </div>
              <div className="mt-2 text-xs text-slate-400 leading-relaxed">
                {selectedTarget?.properties.description || 'No target description available.'}
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
                <div className="border border-slate-800 bg-slate-950 p-2">
                  <div className="text-slate-500">Queue</div>
                  <div className="text-slate-200 truncate">{selectedTarget?.queue || '-'}</div>
                </div>
                <div className="border border-slate-800 bg-slate-950 p-2">
                  <div className="text-slate-500">Tasks</div>
                  <div className="text-slate-200">{selectedTarget?.task_count || 0}</div>
                </div>
              </div>
            </div>
          </section>}

          {activePanel === 'fmv' && (
            <section className="space-y-3">
              <div className="text-xs uppercase tracking-wider text-slate-400 flex items-center justify-between">
                <span className="flex items-center gap-2"><Film className="w-4 h-4 text-cyan-300" /> FMV Clips</span>
                <span>{fmvClips.length}</span>
              </div>
              {fmvClips.slice(0, 6).map((clip) => (
                <div key={clip.id} className="border border-slate-800 bg-slate-900/70 p-3 text-xs">
                  <div className="font-semibold truncate">{clip.name}</div>
                  <div className="mt-1 text-slate-500">{clip.status} / {Math.round(clip.duration_seconds || 0)}s</div>
                  {clip.stream_url && (
                    <video src={clip.stream_url} controls className="mt-2 w-full bg-black border border-slate-800" />
                  )}
                </div>
              ))}
              {fmvClips.length === 0 && <div className="text-xs text-slate-500 border border-slate-800 p-3">Upload an MP4/MOV/TS file with Auto enabled to create an HLS/KLV clip.</div>}
            </section>
          )}

          {activePanel === 'analytics' && (
            <section className="space-y-3">
              <div className="text-xs uppercase tracking-wider text-slate-400 flex items-center gap-2">
                <BarChart3 className="w-4 h-4 text-amber-300" /> GEOINT Analytics
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button onClick={() => runAnalytics('change')} className="h-9 border border-amber-500/40 bg-amber-500/10 text-xs text-amber-100">Change</button>
                <button onClick={() => runAnalytics('viewshed')} className="h-9 border border-emerald-500/40 bg-emerald-500/10 text-xs text-emerald-100">Viewshed</button>
                <button onClick={() => runAnalytics('los')} className="h-9 border border-blue-500/40 bg-blue-500/10 text-xs text-blue-100">LOS</button>
                <button onClick={() => runAnalytics('routes')} className="h-9 border border-purple-500/40 bg-purple-500/10 text-xs text-purple-100 flex items-center justify-center gap-1"><Route className="w-3.5 h-3.5" /> Routes</button>
                <button onClick={() => runAnalytics('pol')} className="col-span-2 h-9 border border-lime-500/40 bg-lime-500/10 text-xs text-lime-100">Pattern of Life</button>
              </div>
              {analyticsJobs.slice(0, 8).map((job) => (
                <div key={job.id} className="border border-slate-800 bg-slate-900/70 px-2 py-2 text-xs">
                  <div className="flex justify-between gap-2">
                    <span className="font-semibold">{job.job_type}</span>
                    <span className="text-emerald-300">{job.status}</span>
                  </div>
                  <div className="text-[10px] text-slate-500">{new Date(job.created_at).toLocaleString()}</div>
                </div>
              ))}
            </section>
          )}

          {activePanel === 'ped' && (
            <section className="space-y-3">
              <button
                onClick={createRequirement}
                disabled={!selectedTarget || busyAction === 'requirement'}
                className="h-9 w-full border border-lime-500/50 bg-lime-500/10 text-xs uppercase tracking-wider text-lime-100 disabled:opacity-50"
              >
                Create Requirement
              </button>
              <div className="text-xs uppercase tracking-wider text-slate-400">Requirements ({requirements.length})</div>
              {requirements.slice(0, 4).map((requirement) => (
                <div key={requirement.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs">
                  <div className="font-semibold truncate">CR-{requirement.id} {requirement.title}</div>
                  <div className="text-slate-500">{requirement.priority} / {requirement.status}</div>
                </div>
              ))}
              <div className="text-xs uppercase tracking-wider text-slate-400">PED Tasks ({pedTasks.length})</div>
              {pedTasks.slice(0, 6).map((task) => (
                <div key={task.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs">
                  <div className="font-semibold truncate">{task.title}</div>
                  <div className="text-emerald-300">{task.status}</div>
                </div>
              ))}
            </section>
          )}

          {activePanel === 'reports' && (
            <section className="space-y-3">
              <button
                onClick={createReport}
                disabled={!selectedTarget || busyAction === 'report'}
                className="h-9 w-full border border-blue-500/50 bg-blue-500/10 text-xs uppercase tracking-wider text-blue-100 disabled:opacity-50"
              >
                Generate Target Package
              </button>
              {reports.slice(0, 8).map((report) => (
                <div key={report.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs">
                  <div className="font-semibold truncate">{report.title}</div>
                  <div className="text-slate-500">{report.report_type} / {report.status}</div>
                </div>
              ))}
              {reports.length === 0 && <div className="text-xs text-slate-500 border border-slate-800 p-3">No target packages generated yet.</div>}
            </section>
          )}

          {activePanel === 'models' && (
            <section className="space-y-3">
              <button
                onClick={queueTraining}
                disabled={busyAction === 'training'}
                className="h-9 w-full border border-cyan-500/50 bg-cyan-500/10 text-xs uppercase tracking-wider text-cyan-100 disabled:opacity-50"
              >
                Queue 1-Epoch Training
              </button>
              {models.slice(0, 6).map((model) => (
                <div key={model.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs">
                  <div className="font-semibold truncate">{model.name}</div>
                  <div className="text-slate-500">{model.version} / {model.promoted ? 'promoted' : model.status}</div>
                </div>
              ))}
              {models.length === 0 && <div className="text-xs text-slate-500 border border-slate-800 p-3">No model registry rows yet.</div>}
            </section>
          )}
        </div>
      </aside>
    </div>
  );
}

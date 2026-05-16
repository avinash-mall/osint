import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { GeoJSON, MapContainer, Marker, Polyline, TileLayer, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Download,
  Film,
  Map as MapIcon,
  Maximize2,
  Pause,
  Play,
  SkipBack,
  SkipForward,
} from 'lucide-react';
import { useEventStream } from '../hooks/useEventStream';
import { AffGlyph, type Affiliation } from './atoms';
import {
  categoryFor,
  detectionClassLabel,
  useDetectionCategories,
} from '../utils/detectionTaxonomy';
import ObjectDetailsForm from './ObjectDetailsForm';
import { useAuth } from '../hooks/useAuth';

const API_URL = import.meta.env.VITE_API_URL || '';
const CARTO_BASEMAP_URL = '/basemap/{z}/{x}/{y}.png';

type Clip = {
  id: number;
  name: string;
  file_path: string;
  hls_path: string | null;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  status: string;
  stream_url: string;
  metadata: any;
  created_at?: string;
  updated_at?: string;
};

type Telemetry = {
  source?: string;
  timestamp_seconds?: number;
  platform_latitude?: number;
  platform_longitude?: number;
  platform_heading?: number;
  frame_center_latitude?: number;
  frame_center_longitude?: number;
  sensor_azimuth?: number;
  sensor_elevation?: number;
};

type FrameRow = {
  frame_index: number;
  timestamp_seconds: number;
  telemetry: Telemetry;
  footprint: any | null;
};

type Detection = {
  id: number;
  clip_id: number;
  frame_index: number;
  class: string;
  confidence: number;
  bbox: any;
  metadata: any;
  created_at?: string;
};

type DetectionGroup = {
  key: string;
  className: string;
  trackId: string | null;
  count: number;
  first: number;
  last: number;
  topConfidence: number;
};

const flyMarkerIcon = L.divIcon({
  className: 'fmv-marker-frame-center',
  html: '<div style="width:14px;height:14px;border-radius:50%;background:#7cf;border:2px solid #001;box-shadow:0 0 6px #7cf;"></div>',
  iconSize: [14, 14],
  iconAnchor: [7, 7],
});

const platformMarkerIcon = L.divIcon({
  className: 'fmv-marker-platform',
  html: '<div style="width:10px;height:10px;border-radius:2px;background:#fdb44b;border:2px solid #001;"></div>',
  iconSize: [10, 10],
  iconAnchor: [5, 5],
});

function FmvMapCursorTracker({
  onCursorChange,
}: {
  onCursorChange: (cursor: { lat: number; lon: number } | null) => void;
}) {
  useMapEvents({
    mousemove(event) {
      onCursorChange({ lat: event.latlng.lat, lon: event.latlng.lng });
    },
    mouseout() {
      onCursorChange(null);
    },
  });
  return null;
}

/** Track lifecycle sparkline: SVG polyline showing detection density across
 *  the track's [first..last] frame range. Bucketed into 24 columns. */
function LifecycleSparkline({
  first,
  last,
  groupKey,
  detections,
  accent,
}: {
  first: number;
  last: number;
  groupKey: string;
  detections: Detection[];
  accent: string;
}) {
  const buckets = 24;
  if (last <= first) {
    return null;
  }
  const counts = new Array(buckets).fill(0);
  // groupKey looks like "t:42" or "c:tank"; derive a matcher.
  const isTrack = groupKey.startsWith('t:');
  const idValue = groupKey.slice(2);
  let max = 1;
  for (const d of detections) {
    if (isTrack) {
      const tid = (d.metadata?.track_id ?? '').toString();
      if (tid !== idValue) continue;
    } else {
      if (d.class !== idValue) continue;
    }
    if (d.frame_index < first || d.frame_index > last) continue;
    const b = Math.min(buckets - 1, Math.floor(((d.frame_index - first) / Math.max(1, last - first)) * buckets));
    counts[b] += 1;
    if (counts[b] > max) max = counts[b];
  }
  const w = 100;
  const h = 18;
  let path = `M0,${h}`;
  for (let i = 0; i < buckets; i++) {
    const x = (i / Math.max(1, buckets - 1)) * w;
    const y = h - (counts[i] / max) * h;
    path += ` L${x.toFixed(1)},${y.toFixed(1)}`;
  }
  return (
    <div
      style={{
        position: 'relative',
        marginTop: 4,
        height: h,
        background: 'var(--bg-3)',
        borderRadius: 2,
        overflow: 'hidden',
      }}
      title={`Lifecycle: ${last - first + 1} frames, density bucketed into ${buckets}`}
    >
      <svg width="100%" height="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
        <polyline
          points={Array.from({ length: buckets }, (_, i) => `${(i / (buckets - 1)) * w},${h - (counts[i] / max) * h}`).join(' ')}
          fill="none"
          stroke={accent}
          strokeWidth="1"
          opacity="0.85"
        />
        <path d={`${path} L${w},${h} Z`} fill={accent} opacity="0.15" />
      </svg>
      <span style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 2, background: '#5ee0a0' }} />
      <span style={{ position: 'absolute', right: 0, top: 0, bottom: 0, width: 2, background: accent }} />
    </div>
  );
}

function fmt(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '00:00';
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function normalizeBbox(det: Detection): { xyxy?: [number, number, number, number]; xyxyNormalized?: boolean; obb?: number[][] } {
  // The worker stores fmv_detections.bbox as a 4-element normalized cxcywh
  // array [cx, cy, w, h] in [0, 1] (see backend/worker.py around line 1543).
  // Legacy rows from before that fix may still hold pixel [x, y, w, h], so
  // any value > 1.5 means treat the array as pixels. Other ingest paths
  // use the SAM3-native {bbox_xyxy: [x1, y1, x2, y2]} object form, which
  // is always in pixel space.
  const raw = det.bbox ?? det.metadata?.bbox ?? null;
  let xyxy: [number, number, number, number] | undefined;
  let xyxyNormalized = false;
  if (Array.isArray(raw) && raw.length === 4) {
    const [a, b, c, d] = raw.map(Number);
    const looksPixel = [a, b, c, d].some((v) => Number.isFinite(v) && Math.abs(v) > 1.5);
    if (looksPixel) {
      xyxy = [a, b, a + c, b + d];
    } else {
      xyxy = [a - c / 2, b - d / 2, a + c / 2, b + d / 2];
      xyxyNormalized = true;
    }
  } else if (raw && typeof raw === 'object') {
    const arr = (raw as any).bbox_xyxy || (raw as any).xyxy;
    if (Array.isArray(arr) && arr.length === 4) {
      xyxy = arr.map(Number) as [number, number, number, number];
    } else {
      const cx = Number((raw as any).cx), cy = Number((raw as any).cy);
      const w = Number((raw as any).w), h = Number((raw as any).h);
      if ([cx, cy, w, h].every(Number.isFinite)) {
        xyxy = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2];
      }
    }
  }
  const obbRaw = (raw && typeof raw === 'object' ? (raw as any).obb : null) || det.metadata?.obb;
  // SAM3 video (and the DOTA-OBB head) emit OBB as a flat 8-number list
  // [x1,y1,x2,y2,x3,y3,x4,y4] (format `yolo_obb_normalized_xyxyxyxy`).
  // Some legacy paths produce a nested [[x,y],…] form. Accept both and
  // always return nested pairs to the renderer below.
  let obb: number[][] | undefined;
  if (Array.isArray(obbRaw) && obbRaw.length > 0) {
    if (typeof obbRaw[0] === 'number') {
      const pairs: number[][] = [];
      for (let i = 0; i + 1 < obbRaw.length; i += 2) {
        pairs.push([Number(obbRaw[i]), Number(obbRaw[i + 1])]);
      }
      obb = pairs;
    } else if (Array.isArray(obbRaw[0])) {
      obb = (obbRaw as any[]).map((pt: any) => [Number(pt[0]), Number(pt[1])]);
    }
  }
  return { xyxy, xyxyNormalized, obb };
}

function trackIdOf(det: Detection): string | null {
  return det.metadata?.track_id || det.bbox?.track_id || null;
}

type SidePanelTab = 'tracks' | 'detections' | 'clips' | 'detail';

type DetectionsSort = 'time_asc' | 'time_desc' | 'conf_desc' | 'class_asc';

/**
 * Three states for the synced map:
 *   - hidden:  no map visible; a small icon button reveals the PiP overlay.
 *   - pip:     300x190 picture-in-picture overlay in the bottom-right of the
 *              video pane. Matches the design's default and is the new
 *              "minimized" state.
 *   - split:   1/3 of the workspace as a side pane next to the video. This
 *              is the legacy "expanded" behaviour, kept for analysts who
 *              want a wider map while scrubbing.
 */
type MapMode = 'hidden' | 'pip' | 'split';

type FmvPlayerProps = {
  /** Bubble synced-map cursor coords up to the global status bar. */
  onCursorChange?: (cursor: { lat: number; lon: number } | null) => void;
  /** Open the GEOINT workspace focused on a clip's primary detection. */
  onOpenMap?: (detectionId: number) => void;
  /** Cross-workspace navigation: focus a specific clip on mount. */
  crossNav?: {
    workspace: 'map' | 'fmv' | 'graph' | 'admin';
    fmvClipId?: number;
    detectionId?: number;
  } | null;
  consumeCrossNav?: () => void;
};

export default function FmvPlayer({
  onCursorChange,
  onOpenMap,
  crossNav,
  consumeCrossNav,
}: FmvPlayerProps = {}) {
  const [clips, setClips] = useState<Clip[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [frames, setFrames] = useState<FrameRow[]>([]);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [trackingError, setTrackingError] = useState<string | null>(null);
  const [trackingProgress, setTrackingProgress] = useState<{ window: number; windows: number } | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [hoverPct, setHoverPct] = useState<number | null>(null);
  // 3-state PiP/split/hidden map (default to PiP matching the new design).
  const [mapMode, setMapMode] = useState<MapMode>('pip');
  const [rightOpen, setRightOpen] = useState(true);
  const [sideTab, setSideTab] = useState<SidePanelTab>('tracks');
  const [trackFilter, setTrackFilter] = useState<'in-frame' | 'all'>('in-frame');
  const [detectionFilter, setDetectionFilter] = useState<'in-frame' | 'all'>('all');
  const [detectionsSort, setDetectionsSort] = useState<DetectionsSort>('time_asc');
  const [selectedDetectionId, setSelectedDetectionId] = useState<number | null>(null);
  const [mapCursor, setMapCursor] = useState<{ lat: number; lon: number } | null>(null);
  const { user } = useAuth();

  // FMV+ stream counters — incremented on every ws frame; the per-second
  // delta is recomputed on a 1 s tick.
  const [ndjsonTotal, setNdjsonTotal] = useState(0);
  const [ndjsonDelta, setNdjsonDelta] = useState(0);
  const ndjsonLastTotalRef = useRef(0);
  const ndjsonNewTracksRef = useRef(0);
  const [ndjsonNewTracksDelta, setNdjsonNewTracksDelta] = useState(0);
  // Re-ID cluster: cosine-similar tracks sharing a DINOv3-LVD embedding
  const [reidCluster, setReidCluster] = useState<{
    anchorTrack: string | null;
    members: Array<{ id: number; track_id: string | null; similarity: number; class: string }>;
  }>({ anchorTrack: null, members: [] });

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const dragRef = useRef(false);

  const { categories } = useDetectionCategories();

  const selectedClip = useMemo(
    () => clips.find((c) => c.id === selectedId) || null,
    [clips, selectedId],
  );

  // -------------------- Data fetching --------------------

  const fetchClips = useCallback(async () => {
    try {
      const res = await axios.get(`${API_URL}/api/fmv/clips`);
      setClips(res.data.clips || []);
    } catch (err) {
      console.error('fetchClips failed', err);
    }
  }, []);

  const fetchFrames = useCallback(async (clipId: number) => {
    try {
      const res = await axios.get(`${API_URL}/api/fmv/clips/${clipId}/klv`, {
        params: { limit: 10000 },
      });
      setFrames(res.data.frames || []);
    } catch (err) {
      console.error('fetchFrames failed', err);
      setFrames([]);
    }
  }, []);

  const fetchDetections = useCallback(async (clipId: number) => {
    try {
      const res = await axios.get(`${API_URL}/api/fmv/clips/${clipId}/detections`);
      setDetections(res.data.detections || []);
    } catch (err) {
      console.error('fetchDetections failed', err);
      setDetections([]);
    }
  }, []);

  useEffect(() => {
    fetchClips();
  }, [fetchClips]);

  // Bubble cursor coords up to the global status bar.
  useEffect(() => {
    if (!onCursorChange) return;
    onCursorChange(mapCursor);
  }, [mapCursor, onCursorChange]);
  useEffect(() => {
    if (!onCursorChange) return;
    return () => onCursorChange(null);
  }, [onCursorChange]);

  // Consume cross-workspace navigation: focus a clip and (optionally) a
  // detection on mount or when the parent updates the target.
  useEffect(() => {
    if (!crossNav) return;
    if (crossNav.fmvClipId) setSelectedId(crossNav.fmvClipId);
    if (crossNav.detectionId) setSelectedDetectionId(crossNav.detectionId);
    consumeCrossNav?.();
  }, [crossNav, consumeCrossNav]);

  // Load the FMV inference profile on mount. The backend swaps profiles on
  // demand; unmount should not call /unload because that endpoint restarts the
  // inference container and can interrupt queued or in-flight FMV tracking.
  useEffect(() => {
    let cancelled = false;
    axios
      .post(`${API_URL}/api/inference/load`, null, { params: { profile: 'fmv' }, timeout: 600_000 })
      .catch((err) => {
        if (cancelled) return;
        setTrackingError(
          err?.response?.data?.detail || err?.message || 'inference profile load failed',
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (selectedId == null) {
      setFrames([]);
      setDetections([]);
      return;
    }
    fetchFrames(selectedId);
    fetchDetections(selectedId);
  }, [selectedId, fetchFrames, fetchDetections]);

  // WebSocket — refresh on backend events.
  const onOps = useCallback(
    (msg: any) => {
      if (msg?.type === 'fmv_clip_ready') fetchClips();
    },
    [fetchClips],
  );
  useEventStream('ops', onOps);

  const onClipChannel = useCallback(
    (msg: any) => {
      if (!selectedId) return;
      if (msg?.type === 'fmv_clip_ready') {
        fetchFrames(selectedId);
        fetchDetections(selectedId);
      }
      if (msg?.type === 'fmv_detection' || msg?.type === 'fmv_detections_complete') {
        setTrackingError(null);
        if (msg?.type === 'fmv_detections_complete') setTrackingProgress(null);
        fetchDetections(selectedId);
        setNdjsonTotal((n) => n + (msg?.type === 'fmv_detection' ? 1 : 0));
        if (msg?.new_track) ndjsonNewTracksRef.current += 1;
      }
      if (msg?.type === 'fmv_detections_progress') {
        setTrackingError(null);
        setTrackingProgress({ window: msg.window || 0, windows: msg.windows || 0 });
        fetchDetections(selectedId);
        // count progress frames as ndjson chunks
        setNdjsonTotal((n) => n + 1);
      }
      if (msg?.type === 'fmv_detections_failed') {
        setTrackingError(msg.error || 'tracking failed');
        setTrackingProgress(null);
      }
    },
    [selectedId, fetchFrames, fetchDetections],
  );
  useEventStream(selectedId ? `fmv:${selectedId}` : 'fmv:none', onClipChannel);

  // Compute per-second NDJSON delta on a 1 s tick.
  useEffect(() => {
    const id = window.setInterval(() => {
      const total = ndjsonTotal;
      setNdjsonDelta(Math.max(0, total - ndjsonLastTotalRef.current));
      ndjsonLastTotalRef.current = total;
      setNdjsonNewTracksDelta(ndjsonNewTracksRef.current);
      ndjsonNewTracksRef.current = 0;
    }, 1000);
    return () => window.clearInterval(id);
  }, [ndjsonTotal]);

  // Reset counters when switching clips.
  useEffect(() => {
    setNdjsonTotal(0);
    setNdjsonDelta(0);
    ndjsonLastTotalRef.current = 0;
    ndjsonNewTracksRef.current = 0;
    setNdjsonNewTracksDelta(0);
    setReidCluster({ anchorTrack: null, members: [] });
  }, [selectedId]);

  // Re-ID cluster — fetch when the selected detection has an embedding.
  useEffect(() => {
    if (!selectedDetectionId) {
      setReidCluster({ anchorTrack: null, members: [] });
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const { data } = await axios.get(
          `${API_URL}/api/fmv/detections/${selectedDetectionId}/similar`,
          { params: { k: 8 } },
        );
        if (cancelled) return;
        const members = (data?.results || [])
          .filter((r: any) => Number(r.similarity || 0) >= 0.86)
          .map((r: any) => ({
            id: r.id,
            track_id: r.track_id || (r.metadata?.track_id ?? null),
            similarity: Number(r.similarity || 0),
            class: r.class || 'unknown',
          }));
        const anchorTrack = detections.find((d) => d.id === selectedDetectionId)?.metadata?.track_id;
        setReidCluster({ anchorTrack: anchorTrack ? String(anchorTrack) : null, members });
      } catch {
        if (!cancelled) setReidCluster({ anchorTrack: null, members: [] });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedDetectionId, detections]);

  // Poll while clip is transcoding so the UI flips to "ready" without
  // requiring a WS event.
  useEffect(() => {
    if (!selectedClip || selectedClip.status === 'ready') return;
    const id = window.setInterval(fetchClips, 3000);
    return () => window.clearInterval(id);
  }, [selectedClip, fetchClips]);

  // Poll detections every 5s while we have a selected ready clip with zero
  // detections — covers the gap before SAM3 finishes / WS reconnects.
  useEffect(() => {
    if (!selectedId || !selectedClip || detections.length > 0) return;
    if (selectedClip.status !== 'ready' && selectedClip.status !== 'queued') return;
    const id = window.setInterval(() => fetchDetections(selectedId), 5000);
    return () => window.clearInterval(id);
  }, [selectedId, selectedClip, detections.length, fetchDetections]);

  // -------------------- Derived structures --------------------

  const fps = selectedClip?.fps || 30;
  const videoWidth = selectedClip?.width || 1920;
  const videoHeight = selectedClip?.height || 1080;

  // Per-track sorted timeline so the canvas draw loop can find the
  // nearest-prior detection at any source frame (the worker only stores
  // sampled frames now — interpolation happens here).
  const trackTimelines = useMemo(() => {
    const map = new Map<string, Detection[]>();
    for (const d of detections) {
      const tid = (d.metadata?.track_id ?? null);
      // Composite key so different prompts with the same numeric track_id
      // (a coincidence across separate inference sessions) stay separate.
      const key = `${d.class}#${tid ?? `f${d.frame_index}`}`;
      const arr = map.get(key);
      if (arr) arr.push(d);
      else map.set(key, [d]);
    }
    for (const arr of map.values()) arr.sort((a, b) => a.frame_index - b.frame_index);
    return map;
  }, [detections]);

  // Max source-frame staleness before we stop drawing a track. At native
  // fps this is roughly the window stride, beyond which the box would
  // misrepresent where the object actually is.
  const detectionMaxAgeFrames = useMemo(() => Math.max(1, Math.round(fps * 1.5)), [fps]);

  // Per-frame lookup used by the canvas. Returns the most recent
  // detection for each track within `detectionMaxAgeFrames` of the
  // requested frame index.
  const detectionsForFrame = useCallback((frameIdx: number): Array<{ det: Detection; ageFrames: number }> => {
    const out: Array<{ det: Detection; ageFrames: number }> = [];
    for (const arr of trackTimelines.values()) {
      // Binary search for the last detection with frame_index <= frameIdx.
      let lo = 0, hi = arr.length - 1, best = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (arr[mid].frame_index <= frameIdx) { best = mid; lo = mid + 1; }
        else hi = mid - 1;
      }
      if (best < 0) continue;
      const det = arr[best];
      const age = frameIdx - det.frame_index;
      if (age > detectionMaxAgeFrames) continue;
      out.push({ det, ageFrames: age });
    }
    return out;
  }, [trackTimelines, detectionMaxAgeFrames]);

  const sortedFrames = useMemo(
    () => [...frames].sort((a, b) => a.frame_index - b.frame_index),
    [frames],
  );

  const hasRealTelemetry = useMemo(
    () => frames.some((f) => f.telemetry?.source && f.telemetry.source !== 'fixture'),
    [frames],
  );

  const platformPath = useMemo<[number, number][]>(() => {
    const path: [number, number][] = [];
    for (const f of sortedFrames) {
      const lat = f.telemetry?.platform_latitude ?? f.telemetry?.frame_center_latitude;
      const lon = f.telemetry?.platform_longitude ?? f.telemetry?.frame_center_longitude;
      if (typeof lat === 'number' && typeof lon === 'number') path.push([lat, lon]);
    }
    return path;
  }, [sortedFrames]);

  const detectionGroups = useMemo<DetectionGroup[]>(() => {
    const groups = new Map<string, DetectionGroup>();
    for (const d of detections) {
      const tid = trackIdOf(d);
      const key = tid ? `t:${tid}` : `c:${d.class}`;
      const existing = groups.get(key);
      if (existing) {
        existing.count += 1;
        existing.first = Math.min(existing.first, d.frame_index);
        existing.last = Math.max(existing.last, d.frame_index);
        existing.topConfidence = Math.max(existing.topConfidence, d.confidence || 0);
      } else {
        groups.set(key, {
          key,
          className: d.class,
          trackId: tid,
          count: 1,
          first: d.frame_index,
          last: d.frame_index,
          topConfidence: d.confidence || 0,
        });
      }
    }
    return Array.from(groups.values()).sort((a, b) => a.first - b.first);
  }, [detections]);

  // Timeline detection density (200 buckets max).
  const histogram = useMemo(() => {
    const totalFrames = Math.max(1, Math.floor((duration || selectedClip?.duration_seconds || 1) * fps));
    const buckets = Math.min(200, Math.max(40, totalFrames));
    const counts = new Array(buckets).fill(0);
    let max = 0;
    for (const d of detections) {
      const b = Math.min(buckets - 1, Math.floor((d.frame_index / totalFrames) * buckets));
      counts[b] += 1;
      if (counts[b] > max) max = counts[b];
    }
    return { buckets, counts, max };
  }, [detections, duration, selectedClip, fps]);

  // -------------------- Video / HLS attachment --------------------

  useEffect(() => {
    const v = videoRef.current;
    if (!v || !selectedClip?.stream_url || selectedClip.status !== 'ready') return;
    const src = selectedClip.stream_url;

    let hls: any = null;
    let cancelled = false;

    if (src.endsWith('.m3u8')) {
      if (v.canPlayType('application/vnd.apple.mpegurl')) {
        v.src = src;
      } else {
        import('hls.js').then(({ default: Hls }) => {
          if (cancelled) return;
          if (Hls.isSupported()) {
            hls = new Hls({ lowLatencyMode: false });
            hls.loadSource(src);
            hls.attachMedia(v);
          } else {
            v.src = src;
          }
        });
      }
    } else {
      v.src = src;
    }

    return () => {
      cancelled = true;
      if (hls) hls.destroy();
      v.removeAttribute('src');
      v.load();
    };
  }, [selectedClip?.stream_url, selectedClip?.status]);

  // Video event listeners.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onTime = () => setCurrentTime(v.currentTime);
    const onMeta = () => {
      setDuration(v.duration || selectedClip?.duration_seconds || 0);
      const canvas = canvasRef.current;
      if (canvas) {
        canvas.width = v.videoWidth || videoWidth;
        canvas.height = v.videoHeight || videoHeight;
      }
    };
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    v.addEventListener('timeupdate', onTime);
    v.addEventListener('loadedmetadata', onMeta);
    v.addEventListener('play', onPlay);
    v.addEventListener('pause', onPause);
    return () => {
      v.removeEventListener('timeupdate', onTime);
      v.removeEventListener('loadedmetadata', onMeta);
      v.removeEventListener('play', onPlay);
      v.removeEventListener('pause', onPause);
    };
  }, [selectedClip, videoWidth, videoHeight]);

  // -------------------- Canvas overlay (rAF) --------------------

  useEffect(() => {
    const v = videoRef.current;
    const canvas = canvasRef.current;
    if (!v || !canvas) return;

    const draw = () => {
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const frameIdx = Math.round(v.currentTime * fps);
      const dets = detectionsForFrame(frameIdx);
      const sx = canvas.width / videoWidth;
      const sy = canvas.height / videoHeight;
      ctx.lineWidth = 2;
      ctx.font = '12px ui-monospace, monospace';
      for (const { det: d, ageFrames } of dets) {
        const cat = categoryFor((d.metadata?.branch_id as string) || 'Other', categories);
        const color = cat.color;
        // Dim the box when the most recent detection for this track is
        // more than half-a-second stale — visual cue that the tracker
        // hasn't seen this object recently.
        const aged = ageFrames > Math.max(1, Math.round(fps * 0.5));
        const strokeAlpha = aged ? 0.5 : 1.0;
        ctx.globalAlpha = strokeAlpha;
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        const { xyxy, xyxyNormalized, obb } = normalizeBbox(d);
        // SAM3 video emits obb in `yolo_obb_normalized_xyxyxyxy` form (0..1).
        // Scale by image dimensions; sx/sy then map image px → canvas px.
        const obbNormalized = (d.metadata?.obb_format || '').includes('normalized');
        const obbScaleX = obbNormalized ? videoWidth * sx : sx;
        const obbScaleY = obbNormalized ? videoHeight * sy : sy;
        const bboxScaleX = xyxyNormalized ? videoWidth * sx : sx;
        const bboxScaleY = xyxyNormalized ? videoHeight * sy : sy;
        if (obb && obb.length >= 3) {
          ctx.beginPath();
          ctx.moveTo(obb[0][0] * obbScaleX, obb[0][1] * obbScaleY);
          for (let i = 1; i < obb.length; i++) ctx.lineTo(obb[i][0] * obbScaleX, obb[i][1] * obbScaleY);
          ctx.closePath();
          ctx.stroke();
          const top = obb.reduce((acc, p) => (p[1] < acc[1] ? p : acc), obb[0]);
          const lx = top[0] * obbScaleX;
          const ly = top[1] * obbScaleY;
          const label = `${detectionClassLabel(d.class)} ${Math.round((d.confidence || 0) * 100)}%`;
          const m = ctx.measureText(label);
          ctx.globalAlpha = aged ? 0.5 : 0.85;
          ctx.fillRect(lx, ly - 14, m.width + 6, 14);
          ctx.globalAlpha = 1;
          ctx.fillStyle = '#000';
          ctx.fillText(label, lx + 3, ly - 3);
          ctx.fillStyle = color;
        } else if (xyxy) {
          const [x1, y1, x2, y2] = xyxy;
          ctx.strokeRect(x1 * bboxScaleX, y1 * bboxScaleY, (x2 - x1) * bboxScaleX, (y2 - y1) * bboxScaleY);
          const label = `${detectionClassLabel(d.class)} ${Math.round((d.confidence || 0) * 100)}%`;
          const m = ctx.measureText(label);
          ctx.globalAlpha = aged ? 0.5 : 0.85;
          ctx.fillRect(x1 * bboxScaleX, y1 * bboxScaleY - 14, m.width + 6, 14);
          ctx.globalAlpha = 1;
          ctx.fillStyle = '#000';
          ctx.fillText(label, x1 * bboxScaleX + 3, y1 * bboxScaleY - 3);
          ctx.fillStyle = color;
        }
        ctx.globalAlpha = 1;
      }
      rafRef.current = requestAnimationFrame(draw);
    };

    rafRef.current = requestAnimationFrame(draw);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [detectionsForFrame, categories, fps, videoWidth, videoHeight]);

  // Resize canvas to match the video element's rendered size AND position.
  // The wrapper is a flex container that centers the <video>; when its aspect
  // ratio differs from the video's, the video letterboxes inside it. The
  // canvas is absolutely positioned within the same wrapper, so it must track
  // the video's offsetLeft/offsetTop — otherwise overlays draw shifted by the
  // letterbox margin.
  useEffect(() => {
    const wrapper = wrapperRef.current;
    const canvas = canvasRef.current;
    const v = videoRef.current;
    if (!wrapper || !canvas || !v) return;
    const sync = () => {
      canvas.style.left = `${v.offsetLeft}px`;
      canvas.style.top = `${v.offsetTop}px`;
      canvas.style.width = `${v.clientWidth}px`;
      canvas.style.height = `${v.clientHeight}px`;
    };
    sync();
    const obs = new ResizeObserver(sync);
    obs.observe(v);
    obs.observe(wrapper);
    return () => obs.disconnect();
  }, [selectedClip]);

  // -------------------- Transport --------------------

  const seekTo = useCallback(
    (t: number) => {
      const v = videoRef.current;
      if (!v) return;
      v.currentTime = Math.max(0, Math.min(duration || v.duration || 0, t));
    },
    [duration],
  );

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play();
    else v.pause();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!selectedClip || selectedClip.status !== 'ready') return;
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return;
      if (e.code === 'Space') {
        e.preventDefault();
        togglePlay();
      } else if (e.code === 'ArrowLeft') {
        seekTo((videoRef.current?.currentTime || 0) - 1 / fps);
      } else if (e.code === 'ArrowRight') {
        seekTo((videoRef.current?.currentTime || 0) + 1 / fps);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [togglePlay, seekTo, selectedClip, fps]);

  // Drag scrubber.
  const onTimelineDown = (e: React.MouseEvent<HTMLDivElement>) => {
    dragRef.current = true;
    const rect = e.currentTarget.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    seekTo(pct * (duration || 0));
  };
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragRef.current) return;
      const strip = document.getElementById('fmv-timeline-strip');
      if (!strip) return;
      const rect = strip.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      seekTo(pct * (duration || 0));
    };
    const onUp = () => {
      dragRef.current = false;
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [seekTo, duration]);

  // -------------------- Current frame derived map state --------------------

  const currentFrameIdx = Math.round(currentTime * fps);

  const currentFrame = useMemo<FrameRow | null>(() => {
    if (!sortedFrames.length) return null;
    // Pick the nearest frame at or before the current index.
    let best: FrameRow | null = null;
    for (const f of sortedFrames) {
      if (f.frame_index <= currentFrameIdx) best = f;
      else break;
    }
    return best || sortedFrames[0];
  }, [sortedFrames, currentFrameIdx]);

  const frameCenter = useMemo<[number, number] | null>(() => {
    const t = currentFrame?.telemetry;
    if (!t) return null;
    const lat = t.frame_center_latitude ?? t.platform_latitude;
    const lon = t.frame_center_longitude ?? t.platform_longitude;
    return typeof lat === 'number' && typeof lon === 'number' ? [lat, lon] : null;
  }, [currentFrame]);

  const platformPosition = useMemo<[number, number] | null>(() => {
    const t = currentFrame?.telemetry;
    if (!t) return null;
    const lat = t.platform_latitude ?? t.frame_center_latitude;
    const lon = t.platform_longitude ?? t.frame_center_longitude;
    return typeof lat === 'number' && typeof lon === 'number' ? [lat, lon] : null;
  }, [currentFrame]);

  const mapCenter = useMemo<[number, number]>(() => {
    if (frameCenter) return frameCenter;
    if (platformPath.length) return platformPath[0];
    return [25.078, 55.179];
  }, [frameCenter, platformPath]);

  const hasTelemetry = platformPath.length > 0;
  // Only the "split" mode reserves space in the grid; PiP overlays the video.
  const mapSplitVisible = mapMode === 'split' && hasTelemetry;
  const mapPipVisible = mapMode === 'pip' && hasTelemetry;

  // Tracks classified by an aff (best-effort: hostile/friend/neutral/unknown
  // derived from the threat metadata stored on the detection).  Keeps the
  // tracks list visually consistent with the map COP's affiliation system.
  const affOf = (det: Detection | undefined): Affiliation => {
    const m: any = det?.metadata ?? {};
    if (m.affiliation && ['friend', 'hostile', 'neutral', 'unknown'].includes(m.affiliation)) {
      return m.affiliation as Affiliation;
    }
    const threat = m.threat_level || m.threat || '';
    if (threat === 'critical' || threat === 'high') return 'hostile';
    if (threat === 'medium') return 'unknown';
    if (threat === 'low') return 'neutral';
    return 'unknown';
  };

  // -------------------- Render --------------------

  // -- Telemetry HUD rows derived from the current frame telemetry.
  const hudRows: Array<[string, string]> = (() => {
    const t = currentFrame?.telemetry;
    if (!t) return [];
    const fmtNum = (v: number | undefined, suffix = '') =>
      v == null || !Number.isFinite(v) ? '—' : `${v.toFixed(4)}${suffix}`;
    const fmtInt = (v: number | undefined, suffix = '') =>
      v == null || !Number.isFinite(v) ? '—' : `${Math.round(v)}${suffix}`;
    const platformLat = t.platform_latitude ?? t.frame_center_latitude;
    const platformLon = t.platform_longitude ?? t.frame_center_longitude;
    const rows: Array<[string, string]> = [
      ['CLIP',   selectedClip?.name?.slice(0, 18) || '—'],
      ['SRC',    t.source?.toUpperCase() || '—'],
      ['TIME',   t.timestamp_seconds != null ? fmt(t.timestamp_seconds) : '—'],
      ['LAT',    fmtNum(platformLat, '°')],
      ['LON',    fmtNum(platformLon, '°')],
      ['HDG',    fmtInt(t.platform_heading, '°')],
    ];
    if (t.sensor_azimuth != null) rows.push(['AZ', fmtInt(t.sensor_azimuth, '°')]);
    if (t.sensor_elevation != null) rows.push(['EL', fmtInt(t.sensor_elevation, '°')]);
    return rows;
  })();

  // -- Event marker positions on the scrubber: cluster the densest histogram
  //    bins into diamond markers, color-graded by intensity.
  const eventMarkers = (() => {
    if (!histogram.max || !duration) return [];
    const out: Array<{ pct: number; tone: 'critical' | 'high' | 'medium' }> = [];
    const min = histogram.max * 0.7;
    histogram.counts.forEach((c, i) => {
      if (c < min) return;
      const pct = (i + 0.5) / histogram.buckets;
      const tone = c >= histogram.max * 0.95 ? 'critical' : c >= histogram.max * 0.85 ? 'high' : 'medium';
      out.push({ pct, tone });
    });
    return out;
  })();

  // -- "Processed" fraction for the YouTube-style buffered bar.  Once
  //    detections complete this is 1.0; while a window-batch is running
  //    we extrapolate from `trackingProgress`.  Falls back to "all
  //    frames before the latest detection" for clips with no progress
  //    event yet (so a clip with detections shows correctly).
  const processedFrac = (() => {
    if (trackingProgress && trackingProgress.windows > 0) {
      return Math.max(0, Math.min(1, trackingProgress.window / trackingProgress.windows));
    }
    if (!detections.length || !duration) return selectedClip?.status === 'ready' ? 1 : 0;
    const totalFrames = Math.max(1, Math.floor(duration * fps));
    const last = detections.reduce((m, d) => Math.max(m, d.frame_index), 0);
    return Math.max(0, Math.min(1, last / totalFrames));
  })();
  const processingActive = trackingProgress != null && trackingProgress.windows > 0;

  // -- Track rows derived from detectionGroups; the right Tracks tab
  //    needs a track-id, label, aff, conf, frames range, and an
  //    "in-frame" flag synced to the scrubber.
  type TrackRow = {
    key: string;
    label: string;
    trackId: string | null;
    aff: Affiliation;
    conf: number;
    first: number;
    last: number;
    frames: number;
    inFrame: boolean;
  };
  const trackRows: TrackRow[] = detectionGroups.map((g) => {
    // pick a representative detection for affiliation
    const rep = detections.find((d) =>
      (g.trackId ? trackIdOf(d) === g.trackId : d.class === g.className),
    );
    return {
      key: g.key,
      label: detectionClassLabel(g.className),
      trackId: g.trackId,
      aff: affOf(rep),
      conf: g.topConfidence,
      first: g.first,
      last: g.last,
      frames: g.count,
      inFrame: currentFrameIdx >= g.first && currentFrameIdx <= g.last,
    };
  });
  const inFrameCount = trackRows.filter((t) => t.inFrame).length;
  const visibleTrackRows = trackFilter === 'all' ? trackRows : trackRows.filter((t) => t.inFrame);

  const accent = 'var(--accent)';

  return (
    <div
      className={`fmv-shell ${rightOpen ? 'is-sidebar-open' : 'is-sidebar-collapsed'}`}
      style={{
        height: '100%',
        gap: 1,
        background: 'var(--line)',
        transition: 'grid-template-columns .18s ease',
        minHeight: 0,
      }}
    >
      {/* === LEFT COLUMN: video + (optional) synced map + transport === */}
      <section className="fmv-primary" style={{ display: 'flex', flexDirection: 'column', background: 'var(--bg-0)', minWidth: 0, minHeight: 0 }}>
        <div
          className={`fmv-stage-grid ${mapSplitVisible ? '' : 'is-map-hidden'}`}
          style={{
            flex: 1,
            minHeight: 0,
            gap: 1,
            background: 'var(--line)',
            transition: 'grid-template-columns .18s ease',
          }}
        >
          {/* VIDEO PANE ---------------------------------------------------- */}
          <div
            ref={wrapperRef}
            style={{
              position: 'relative',
              overflow: 'hidden',
              background: '#000',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              minWidth: 0,
            }}
          >
            {!selectedClip && (
              <div style={{ color: 'var(--ink-2)', fontSize: 12, textAlign: 'center', padding: 24 }}>
                <Film size={36} style={{ opacity: 0.5 }} />
                <div style={{ marginTop: 12 }}>Select a clip from the Clips tab.</div>
              </div>
            )}
            {selectedClip && (
              <>
                <video
                  ref={videoRef}
                  style={{ maxWidth: '100%', maxHeight: '100%', display: 'block' }}
                  playsInline
                  controls={false}
                />
                <canvas
                  ref={canvasRef}
                  style={{ position: 'absolute', left: 0, top: 0, pointerEvents: 'none' }}
                />

                {/* Telemetry HUD — top-left, monospace */}
                {hudRows.length > 0 && (
                  <div
                    style={{
                      position: 'absolute',
                      top: 14,
                      left: 14,
                      fontFamily: 'var(--font-mono)',
                      fontSize: 10.5,
                      color: '#bbf2d0',
                      textShadow: '0 0 4px rgba(0,0,0,.8)',
                      pointerEvents: 'none',
                    }}
                  >
                    {hudRows.map(([k, v]) => (
                      <div key={k} style={{ display: 'grid', gridTemplateColumns: '50px 1fr', gap: 8, lineHeight: 1.45 }}>
                        <span style={{ color: '#7bce97', opacity: 0.7 }}>{k}</span>
                        <span>{v}</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Top-right corner: status pills + FMV+ counters */}
                <div style={{ position: 'absolute', top: 14, right: 14, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {selectedClip.status === 'ready' && (
                      <span
                        style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: 10.5,
                          color: '#ff4040',
                          letterSpacing: '.1em',
                          padding: '2px 6px',
                          background: 'rgba(0,0,0,.5)',
                          border: '1px solid rgba(255,64,64,.4)',
                        }}
                      >
                        ● LIVE
                      </span>
                    )}
                    {trackingProgress && trackingProgress.windows > 0 && (
                      <span
                        style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: 10.5,
                          color: accent,
                          padding: '2px 6px',
                          background: 'rgba(0,0,0,.5)',
                          border: `1px solid ${accent}`,
                        }}
                        title={`Tracking window ${trackingProgress.window}/${trackingProgress.windows}`}
                      >
                        ⊕ TRACK {trackingProgress.window}/{trackingProgress.windows}
                      </span>
                    )}
                    <button className="btn icon sm" style={{ background: 'rgba(0,0,0,.5)', borderColor: 'rgba(255,255,255,.15)', color: '#fff' }} title="Fullscreen">
                      <Maximize2 size={12} />
                    </button>
                  </div>

                  {/* Object-Multiplex badge — appears when SAM3 uses the
                       multiplex inference path. Reads metadata across the
                       latest in-frame detections. */}
                  {(() => {
                    const inFrameDets = detectionsForFrame(currentFrameIdx);
                    const usesMultiplex = inFrameDets.some(({ det }) => det.metadata?.uses_multiplex === true)
                      || detections.slice(0, 5).some((d) => d.metadata?.uses_multiplex === true);
                    if (!usesMultiplex) return null;
                    return (
                      <span
                        style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: 10.5,
                          color: accent,
                          padding: '3px 8px',
                          background: 'rgba(0,0,0,.6)',
                          border: `1px solid ${accent}`,
                          letterSpacing: '.06em',
                        }}
                        title="SAM3 video tracker is running in Object-Multiplex mode"
                      >
                        ⚡ OBJECT-MULTIPLEX · {inFrameDets.length} OBJ
                      </span>
                    );
                  })()}

                  {/* NDJSON streaming counter (FMV+) */}
                  {(ndjsonTotal > 0 || (trackingProgress && trackingProgress.windows > 0)) && (
                    <span
                      style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: 10,
                        color: '#fff',
                        padding: '3px 8px',
                        background: 'rgba(0,0,0,.6)',
                        letterSpacing: '.04em',
                      }}
                      title="NDJSON inference events streamed since clip selection"
                    >
                      NDJSON · <span style={{ color: accent }}>FRAME {currentFrameIdx}</span> · <span style={{ color: '#5ee0a0' }}>+{ndjsonDelta} dets/s · +{ndjsonNewTracksDelta} tracks/s</span> · {ndjsonTotal} total
                    </span>
                  )}
                </div>

                {/* Transcoding overlay */}
                {selectedClip.status !== 'ready' && (
                  <div
                    style={{
                      position: 'absolute',
                      inset: 0,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: 'rgba(0,0,0,0.6)',
                    }}
                  >
                    <span className="tag accent">TRANSCODING…</span>
                  </div>
                )}

                {/* Synthetic telemetry warning */}
                {selectedClip.status === 'ready' && !hasRealTelemetry && frames.length > 0 && (
                  <div style={{ position: 'absolute', bottom: 14, left: 14 }}>
                    <span
                      className="tag unknown"
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'help' }}
                      title="No MISB-0601 KLV, MP4 GPMD, or co-uploaded .srt sidecar was detected in this clip.  Showing a synthetic sine-wave path for demo."
                    >
                      <AlertTriangle size={11} /> SYNTHETIC TELEMETRY
                    </span>
                  </div>
                )}

                {/* Tracking error toast */}
                {trackingError && (
                  <div style={{ position: 'absolute', bottom: 14, right: 14 }}>
                    <span className="tag crit" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      <AlertTriangle size={11} /> {trackingError}
                    </span>
                  </div>
                )}

                {/* PiP minimized synced-map overlay (300x190, bottom-right) */}
                {mapPipVisible && (
                  <div
                    className="fmv-map-pip"
                    style={{
                      position: 'absolute',
                      right: 14,
                      bottom: 14,
                      background: 'var(--bg-0)',
                      border: '1px solid rgba(255,255,255,.22)',
                      boxShadow: '0 12px 32px rgba(0,0,0,.55)',
                      overflow: 'hidden',
                    }}
                  >
                    <MapContainer
                      key={`pip-${selectedId || 'empty'}`}
                      center={mapCenter}
                      zoom={15}
                      style={{ width: '100%', height: '100%' }}
                      attributionControl={false}
                    >
                      <TileLayer url={CARTO_BASEMAP_URL} maxNativeZoom={10} />
                      <FmvMapCursorTracker onCursorChange={setMapCursor} />
                      {platformPath.length > 1 && (
                        <Polyline positions={platformPath} pathOptions={{ color: '#7cf', weight: 1.5 }} />
                      )}
                      {platformPosition && <Marker position={platformPosition} icon={platformMarkerIcon} />}
                      {frameCenter && <Marker position={frameCenter} icon={flyMarkerIcon} />}
                      {currentFrame?.footprint && (
                        <GeoJSON
                          key={`pip-fp-${currentFrame.frame_index}`}
                          data={currentFrame.footprint as any}
                          style={() => ({ color: '#7cf', weight: 1, fillOpacity: 0.08 })}
                        />
                      )}
                    </MapContainer>
                    <div
                      style={{
                        position: 'absolute',
                        top: 0,
                        left: 0,
                        right: 0,
                        height: 22,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        padding: '0 8px',
                        background: 'linear-gradient(to bottom, rgba(0,0,0,.7), rgba(0,0,0,0))',
                        fontFamily: 'var(--font-mono)',
                        fontSize: 9.5,
                        color: '#fff',
                        letterSpacing: '.08em',
                      }}
                    >
                      <MapIcon size={10} style={{ color: 'var(--accent)' }} />
                      <span>SYNCED MAP · MINIMIZED</span>
                      <span style={{ flex: 1 }} />
                      <button
                        type="button"
                        onClick={() => setMapMode('split')}
                        title="Expand to 1/3 split view"
                        className="btn icon xs"
                        style={{
                          background: 'rgba(0,0,0,.5)',
                          borderColor: 'rgba(255,255,255,.2)',
                          color: '#fff',
                          width: 18,
                          height: 18,
                        }}
                      >
                        <Maximize2 size={10} />
                      </button>
                      <button
                        type="button"
                        onClick={() => setMapMode('hidden')}
                        title="Hide map"
                        className="btn icon xs"
                        style={{
                          background: 'rgba(0,0,0,.5)',
                          borderColor: 'rgba(255,255,255,.2)',
                          color: '#fff',
                          width: 18,
                          height: 18,
                        }}
                      >
                        <ChevronRight size={10} />
                      </button>
                    </div>
                    <div
                      style={{
                        position: 'absolute',
                        bottom: 4,
                        left: 6,
                        fontFamily: 'var(--font-mono)',
                        fontSize: 9.5,
                        color: '#bbf2d0',
                        textShadow: '0 0 4px rgba(0,0,0,.8)',
                        pointerEvents: 'none',
                      }}
                    >
                      {currentFrame?.telemetry?.source?.toUpperCase() || 'TELEMETRY'} · #{currentFrameIdx}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>

          {/* SYNCED MAP PANE ---------------------------------------------- */}
          {mapSplitVisible && (
            <div style={{ position: 'relative', background: 'var(--bg-0)', minWidth: 0 }}>
              <div
                style={{
                  position: 'absolute',
                  top: 10,
                  left: 12,
                  zIndex: 500,
                  fontFamily: 'var(--font-mono)',
                  fontSize: 10,
                  color: 'var(--ink-2)',
                  letterSpacing: '.08em',
                  textTransform: 'uppercase',
                }}
              >
                Synced map
              </div>
              <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 500, display: 'flex', gap: 4 }}>
                <button
                  type="button"
                  onClick={() => setMapMode('pip')}
                  title="Minimize to picture-in-picture"
                  className="btn icon xs"
                  style={{ borderRadius: 6 }}
                >
                  <ChevronRight size={11} />
                </button>
                <button
                  type="button"
                  onClick={() => setMapMode('hidden')}
                  title="Hide map"
                  className="btn icon xs"
                  style={{ borderRadius: 6 }}
                >
                  <MapIcon size={11} />
                </button>
              </div>
              <MapContainer
                key={`split-${selectedId || 'empty'}`}
                center={mapCenter}
                zoom={15}
                style={{ width: '100%', height: '100%' }}
                attributionControl={false}
              >
                <TileLayer url={CARTO_BASEMAP_URL} maxNativeZoom={10} />
                <FmvMapCursorTracker onCursorChange={setMapCursor} />
                {platformPath.length > 1 && (
                  <Polyline positions={platformPath} pathOptions={{ color: '#7cf', weight: 1.5 }} />
                )}
                {platformPosition && <Marker position={platformPosition} icon={platformMarkerIcon} />}
                {frameCenter && <Marker position={frameCenter} icon={flyMarkerIcon} />}
                {currentFrame?.footprint && (
                  <GeoJSON
                    key={`fp-${currentFrame.frame_index}`}
                    data={currentFrame.footprint as any}
                    style={() => ({ color: '#7cf', weight: 1, fillOpacity: 0.08 })}
                  />
                )}
              </MapContainer>
              <div
                style={{
                  position: 'absolute',
                  left: 10,
                  bottom: 10,
                  zIndex: 500,
                  fontFamily: 'var(--font-mono)',
                  fontSize: 10,
                  color: 'var(--ink-1)',
                  background: 'rgba(11, 13, 16, 0.7)',
                  padding: '4px 8px',
                  border: '1px solid var(--line)',
                  pointerEvents: 'none',
                }}
                title={
                  currentFrame?.telemetry?.source === 'misb-klv'
                    ? 'Real telemetry parsed from MISB ST 0601 KLV stream'
                    : currentFrame?.telemetry?.source === 'srt'
                      ? 'Real telemetry parsed from co-uploaded .srt sidecar'
                      : currentFrame?.telemetry?.source === 'gpmd'
                        ? 'Real telemetry parsed from MP4 GPMD track'
                        : 'No real telemetry detected'
                }
              >
                {currentFrame?.telemetry?.source?.toUpperCase() || 'TELEMETRY'} · frame {currentFrameIdx}
              </div>
            </div>
          )}
        </div>

        {/* TRANSPORT + SCRUBBER ----------------------------------------- */}
        {selectedClip && (
          <div style={{ background: 'var(--bg-1)', borderTop: '1px solid var(--line)', padding: '10px 14px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
              <button className="btn icon sm" onClick={() => seekTo(currentTime - 1 / fps)} type="button" title="Previous frame">
                <SkipBack size={12} />
              </button>
              <button
                onClick={togglePlay}
                className="btn primary"
                style={{ width: 36, height: 30, padding: 0, justifyContent: 'center', borderRadius: 0 }}
                type="button"
                title="Play / Pause (Space)"
              >
                {playing ? <Pause size={14} /> : <Play size={14} />}
              </button>
              <button className="btn icon sm" onClick={() => seekTo(currentTime + 1 / fps)} type="button" title="Next frame">
                <SkipForward size={12} />
              </button>
              <span className="mono" style={{ fontSize: 11, color: 'var(--ink-0)' }}>
                {fmt(currentTime)}
              </span>
              <span className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                / {fmt(duration || selectedClip.duration_seconds)}
              </span>
              <div style={{ flex: 1 }} />
              <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>
                FRAME {currentFrameIdx}
              </span>
              <div className="seg" style={{ display: 'inline-flex' }} title={hasTelemetry ? 'Synced map' : 'No telemetry — map disabled'}>
                <button
                  type="button"
                  className={mapMode === 'hidden' ? 'on' : ''}
                  onClick={() => setMapMode('hidden')}
                  disabled={!hasTelemetry}
                  title="Hide map"
                >
                  HIDE
                </button>
                <button
                  type="button"
                  className={mapMode === 'pip' ? 'on' : ''}
                  onClick={() => setMapMode('pip')}
                  disabled={!hasTelemetry}
                  title="Minimize map to picture-in-picture overlay"
                >
                  MIN
                </button>
                <button
                  type="button"
                  className={mapMode === 'split' ? 'on' : ''}
                  onClick={() => setMapMode('split')}
                  disabled={!hasTelemetry}
                  title="Expand map to 1/3 split view"
                >
                  EXP
                </button>
              </div>
              <button className="btn xs" type="button" title="Export clip"><Download size={11} /> Clip</button>
            </div>

            {/* Linear scrub slider — primary, keyboard/touch friendly seek */}
            <input
              type="range"
              min={0}
              max={Math.max(0.001, duration || selectedClip.duration_seconds || 1)}
              step={duration ? Math.max(0.01, duration / 1000) : 0.01}
              value={Math.min(duration || selectedClip.duration_seconds || 0, Math.max(0, currentTime))}
              onChange={(e) => seekTo(Number(e.target.value))}
              title="Drag to seek. Keyboard arrows step too."
              aria-label="Playback position"
              style={{
                width: '100%',
                accentColor: accent,
                marginBottom: 8,
              }}
            />

            <div
              id="fmv-timeline-strip"
              onMouseDown={onTimelineDown}
              onMouseMove={(e) => {
                const rect = e.currentTarget.getBoundingClientRect();
                setHoverPct((e.clientX - rect.left) / rect.width);
              }}
              onMouseLeave={() => setHoverPct(null)}
              style={{
                position: 'relative',
                height: 56,
                background: 'var(--bg-0)',
                border: '1px solid var(--line)',
                cursor: 'pointer',
                overflow: 'hidden',
              }}
              title="Drag the bar to scrub. ←/→ step one frame. Space toggles play."
            >
              {/* Processed (buffered) band — YouTube style */}
              <div
                style={{
                  position: 'absolute',
                  top: 0,
                  bottom: 0,
                  left: 0,
                  width: `${Math.round(processedFrac * 100)}%`,
                  background: 'color-mix(in oklab, var(--ink-1) 22%, transparent)',
                }}
              />
              {/* Active processing edge */}
              {processingActive && processedFrac < 1 && (
                <div
                  style={{
                    position: 'absolute',
                    top: 0,
                    bottom: 0,
                    left: `${Math.round(processedFrac * 100)}%`,
                    width: '6%',
                    background: `repeating-linear-gradient(135deg, ${accent}55 0 6px, ${accent}11 6px 12px)`,
                    animation: 'fmv-proc 1.4s linear infinite',
                  }}
                />
              )}
              {/* Processing label */}
              {processingActive && (
                <div
                  className="mono"
                  style={{
                    position: 'absolute',
                    top: 4,
                    left: `calc(${Math.round(processedFrac * 100)}% + 8px)`,
                    fontSize: 9,
                    letterSpacing: '.08em',
                    color: accent,
                    textTransform: 'uppercase',
                    textShadow: '0 0 4px var(--bg-0)',
                    pointerEvents: 'none',
                  }}
                >
                  ◉ Detecting · {Math.round(processedFrac * 100)}%
                </div>
              )}

              {/* Detection density waveform */}
              <svg
                width="100%"
                height="100%"
                preserveAspectRatio="none"
                viewBox="0 0 1000 56"
                style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
              >
                {histogram.max > 0 &&
                  (() => {
                    const n = histogram.counts.length;
                    let path = 'M0,56';
                    for (let i = 0; i < n; i++) {
                      const x = (i / Math.max(1, n - 1)) * 1000;
                      const v = (histogram.counts[i] / histogram.max) * 44;
                      path += ` L${x.toFixed(1)},${(56 - v).toFixed(1)}`;
                    }
                    path += ' L1000,56 Z';
                    return <path d={path} fill={accent} opacity={0.25} />;
                  })()}
              </svg>

              {/* Event diamond markers */}
              {eventMarkers.map((e, i) => {
                const color =
                  e.tone === 'critical' ? 'var(--nato-hostile)' :
                  e.tone === 'high' ? accent : 'var(--nato-unknown)';
                return (
                  <div
                    key={i}
                    style={{
                      position: 'absolute',
                      top: 0,
                      bottom: 0,
                      width: 2,
                      left: `${e.pct * 100}%`,
                      background: color,
                      pointerEvents: 'none',
                    }}
                  >
                    <div
                      style={{
                        position: 'absolute',
                        top: -3,
                        left: -3,
                        width: 8,
                        height: 8,
                        transform: 'rotate(45deg)',
                        background: color,
                      }}
                    />
                  </div>
                );
              })}

              {/* Playhead */}
              <div
                style={{
                  position: 'absolute',
                  top: 0,
                  bottom: 0,
                  left: `${duration ? (currentTime / duration) * 100 : 0}%`,
                  borderLeft: `1.5px solid ${accent}`,
                  pointerEvents: 'none',
                }}
              >
                <div
                  style={{
                    position: 'absolute',
                    top: -4,
                    left: -5,
                    width: 0,
                    height: 0,
                    borderLeft: '5px solid transparent',
                    borderRight: '5px solid transparent',
                    borderTop: `6px solid ${accent}`,
                  }}
                />
              </div>

              {/* Hover indicator */}
              {hoverPct !== null && (
                <div
                  style={{
                    position: 'absolute',
                    top: 0,
                    bottom: 0,
                    left: `${hoverPct * 100}%`,
                    width: 1,
                    background: 'rgba(255,255,255,0.4)',
                    pointerEvents: 'none',
                  }}
                />
              )}
            </div>
          </div>
        )}
      </section>

      {/* === RIGHT COLUMN: Tracks / Detections / Clips tabs (collapsible) === */}
      {rightOpen ? (
        <aside
          className="panel fmv-sidebar"
          style={{ display: 'flex', flexDirection: 'column', minWidth: 0, border: 0, position: 'relative' }}
        >
          <button
            onClick={() => setRightOpen(false)}
            type="button"
            title="Collapse panel"
            className="btn icon xs"
            style={{ position: 'absolute', top: 8, right: 8, zIndex: 2 }}
          >
            <ChevronRight size={11} />
          </button>

          <div className="panel-h" style={{ padding: 0 }}>
            {(
              [
                ['tracks', 'Tracks', trackRows.length] as [SidePanelTab, string, number],
                ['detections', 'Detections', detections.length] as [SidePanelTab, string, number],
                ['clips', 'Clips', clips.length] as [SidePanelTab, string, number],
                ...(selectedDetectionId
                  ? ([['detail', 'Detail', 1] as [SidePanelTab, string, number]] as const)
                  : []),
              ]
            ).map(([k, label, count]) => (
              <button
                key={k}
                onClick={() => setSideTab(k)}
                type="button"
                style={{
                  flex: 1,
                  height: 34,
                  border: 0,
                  background: sideTab === k ? 'var(--bg-2)' : 'transparent',
                  color: sideTab === k ? 'var(--ink-0)' : 'var(--ink-2)',
                  borderRight: '1px solid var(--line)',
                  borderBottom: sideTab === k ? `2px solid ${accent}` : '2px solid transparent',
                  cursor: 'pointer',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 11,
                  letterSpacing: '.08em',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 6,
                  textTransform: 'uppercase',
                }}
              >
                {label} <span style={{ color: sideTab === k ? accent : 'var(--ink-3)' }}>{count}</span>
              </button>
            ))}
          </div>

          <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
            {sideTab === 'tracks' && (
              <>
                <div
                  style={{
                    padding: '10px 14px',
                    borderBottom: '1px solid var(--line)',
                    display: 'flex',
                    gap: 6,
                    alignItems: 'center',
                  }}
                >
                  <div className="seg" style={{ flex: 1 }}>
                    <button
                      type="button"
                      className={trackFilter === 'in-frame' ? 'on' : ''}
                      onClick={() => setTrackFilter('in-frame')}
                    >
                      IN FRAME
                    </button>
                    <button type="button" className={trackFilter === 'all' ? 'on' : ''} onClick={() => setTrackFilter('all')}>
                      ALL
                    </button>
                  </div>
                  <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                    {inFrameCount} / {trackRows.length}
                  </span>
                </div>

                {visibleTrackRows.length === 0 && (
                  <div style={{ fontSize: 11, color: 'var(--ink-2)', textAlign: 'center', padding: 16 }}>
                    {selectedClip
                      ? selectedClip.status === 'ready'
                        ? trackFilter === 'in-frame'
                          ? 'No tracks active at this frame.'
                          : 'No detections yet — SAM3 pipeline may still be running.'
                        : 'Waiting for transcode…'
                      : 'Select a clip in the Clips tab.'}
                  </div>
                )}

                {/* Re-ID cluster panel — DINOv3-LVD cosine-similar tracks */}
                {reidCluster.members.length > 0 && (
                  <div
                    style={{
                      margin: '10px 14px',
                      padding: 10,
                      border: `1px solid color-mix(in oklab, ${accent} 50%, var(--line))`,
                      borderRadius: 6,
                      background: `color-mix(in oklab, ${accent} 8%, var(--bg-2))`,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <span
                        className="mono"
                        style={{
                          fontSize: 9.5,
                          padding: '2px 6px',
                          color: '#5ee0a0',
                          background: 'color-mix(in oklab, #5ee0a0 14%, transparent)',
                          border: '1px solid color-mix(in oklab, #5ee0a0 50%, transparent)',
                        }}
                      >
                        DINOv3-LVD
                      </span>
                      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>
                        cosine ≥ 0.86 · {reidCluster.members.length} peer{reidCluster.members.length === 1 ? '' : 's'}
                      </span>
                    </div>
                    <div style={{ fontSize: 11.5, color: 'var(--ink-1)', marginBottom: 8 }}>
                      Possibly the same object as{' '}
                      {reidCluster.members.slice(0, 3).map((m, i) => (
                        <span key={m.id}>
                          {i > 0 ? ', ' : ''}
                          <b style={{ color: accent }}>{m.track_id ? `TRK-${m.track_id}` : `DET-${m.id}`}</b>
                          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                            {' '}({(m.similarity * 100).toFixed(0)}%)
                          </span>
                        </span>
                      ))}
                    </div>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {reidCluster.members.map((m, i) => (
                        <button
                          key={m.id}
                          type="button"
                          onClick={() => setSelectedDetectionId(m.id)}
                          className="mono"
                          style={{
                            fontSize: 10.5,
                            padding: '2px 8px',
                            borderRadius: 999,
                            background: i === 0 ? accent : 'var(--bg-3)',
                            color: i === 0 ? '#0b0d10' : 'var(--ink-1)',
                            border: 0,
                            cursor: 'pointer',
                            fontWeight: i === 0 ? 600 : 500,
                          }}
                          title={`${m.class} · ${(m.similarity * 100).toFixed(0)}% similarity`}
                        >
                          {m.track_id ? `TRK-${m.track_id}` : `DET-${m.id}`}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {visibleTrackRows.map((t) => (
                  <button
                    key={t.key}
                    type="button"
                    onClick={() => {
                      seekTo(t.first / fps);
                      // Find the highest-confidence detection in this track
                      // and open it in the Detail tab so the operator can edit.
                      const matching = detections
                        .filter((d) =>
                          t.trackId
                            ? String(d.metadata?.track_id) === String(t.trackId)
                            : d.class === t.label || detectionClassLabel(d.class) === t.label,
                        )
                        .sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
                      if (matching[0]) {
                        setSelectedDetectionId(matching[0].id);
                        setSideTab('detail');
                      }
                    }}
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '16px 1fr auto',
                      gap: 10,
                      alignItems: 'center',
                      width: '100%',
                      padding: '10px 14px',
                      borderBottom: '1px solid var(--line)',
                      background: t.inFrame ? `color-mix(in oklab, ${accent} 9%, transparent)` : 'transparent',
                      borderLeft: t.inFrame ? `2px solid ${accent}` : '2px solid transparent',
                      cursor: 'pointer',
                      textAlign: 'left',
                      color: 'var(--ink-0)',
                    }}
                  >
                    <AffGlyph aff={t.aff} size={14} />
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 12, fontWeight: t.inFrame ? 600 : 500 }}>{t.label}</div>
                      <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                        {t.trackId ? `TRK-${t.trackId}` : 'no track'} · {t.frames}f · {Math.round(t.conf * 100)}%
                      </div>
                      {/* Lifecycle sparkline + first/last-seen markers */}
                      <LifecycleSparkline
                        first={t.first}
                        last={t.last}
                        groupKey={t.key}
                        detections={detections}
                        accent={accent}
                      />
                      <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                        <span
                          className="mono"
                          style={{
                            fontSize: 9,
                            padding: '1px 5px',
                            border: '1px solid var(--line-2)',
                            color: '#5ee0a0',
                            borderRadius: 2,
                          }}
                          title={`First detection at frame ${t.first}`}
                        >
                          FIRST #{t.first}
                        </span>
                        <span
                          className="mono"
                          style={{
                            fontSize: 9,
                            padding: '1px 5px',
                            border: '1px solid var(--line-2)',
                            color: accent,
                            borderRadius: 2,
                          }}
                          title={`Last detection at frame ${t.last}`}
                        >
                          LAST #{t.last}
                        </span>
                      </div>
                    </div>
                    <span
                      className="mono"
                      style={{
                        fontSize: 9,
                        color: t.inFrame ? 'var(--ok)' : 'var(--ink-3)',
                        padding: '1px 5px',
                        border: '1px solid',
                        borderColor: t.inFrame ? 'var(--ok)' : 'var(--line-2)',
                      }}
                    >
                      {t.inFrame ? 'IN' : 'OUT'}
                    </span>
                  </button>
                ))}
              </>
            )}

            {sideTab === 'detections' && (() => {
              const list = detections.filter((d) => {
                if (detectionFilter !== 'in-frame') return true;
                const f = (d as any).frame_index;
                if (typeof f !== 'number') return false;
                return Math.abs(f - currentFrameIdx) <= detectionMaxAgeFrames;
              });
              const sorted = [...list].sort((a, b) => {
                const fa = Number((a as any).frame_index ?? 0);
                const fb = Number((b as any).frame_index ?? 0);
                switch (detectionsSort) {
                  case 'time_desc': return fb - fa;
                  case 'conf_desc': return (b.confidence || 0) - (a.confidence || 0);
                  case 'class_asc': return detectionClassLabel(a.class).localeCompare(detectionClassLabel(b.class));
                  case 'time_asc':
                  default: return fa - fb;
                }
              });
              return (
                <>
                  <div
                    style={{
                      padding: '10px 14px',
                      borderBottom: '1px solid var(--line)',
                      display: 'flex',
                      gap: 6,
                      alignItems: 'center',
                      flexWrap: 'wrap',
                    }}
                  >
                    <div className="seg" style={{ flex: '1 0 auto' }}>
                      <button
                        type="button"
                        className={detectionFilter === 'in-frame' ? 'on' : ''}
                        onClick={() => setDetectionFilter('in-frame')}
                      >
                        IN FRAME
                      </button>
                      <button
                        type="button"
                        className={detectionFilter === 'all' ? 'on' : ''}
                        onClick={() => setDetectionFilter('all')}
                      >
                        ALL
                      </button>
                    </div>
                    <select
                      value={detectionsSort}
                      onChange={(e) => setDetectionsSort(e.target.value as DetectionsSort)}
                      className="mono"
                      style={{
                        fontSize: 10,
                        background: 'var(--bg-2)',
                        color: 'var(--ink-0)',
                        border: '1px solid var(--line)',
                        padding: '3px 6px',
                      }}
                    >
                      <option value="time_asc">time ↑</option>
                      <option value="time_desc">time ↓</option>
                      <option value="conf_desc">conf ↓</option>
                      <option value="class_asc">class A→Z</option>
                    </select>
                    <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                      {sorted.length} / {detections.length}
                    </span>
                  </div>

                  {sorted.length === 0 && (
                    <div style={{ fontSize: 11, color: 'var(--ink-2)', textAlign: 'center', padding: 16 }}>
                      {selectedClip
                        ? detections.length === 0
                          ? 'No detections — SAM3 pipeline may still be running.'
                          : 'No detections at this frame.'
                        : 'Select a clip in the Clips tab.'}
                    </div>
                  )}

                  {sorted.map((d) => {
                    const frameIdx = Number((d as any).frame_index ?? 0);
                    const t = frameIdx / fps;
                    const trackId = trackIdOf(d);
                    const selected = selectedDetectionId === d.id;
                    return (
                      <button
                        key={d.id}
                        type="button"
                        onClick={() => {
                          seekTo(t);
                          setSelectedDetectionId(d.id);
                          setSideTab('detail');
                        }}
                        style={{
                          display: 'grid',
                          gridTemplateColumns: '16px 1fr auto',
                          gap: 10,
                          alignItems: 'center',
                          width: '100%',
                          padding: '8px 14px',
                          borderBottom: '1px solid var(--line)',
                          background: selected ? `color-mix(in oklab, ${accent} 14%, transparent)` : 'transparent',
                          borderLeft: selected ? `2px solid ${accent}` : '2px solid transparent',
                          cursor: 'pointer',
                          textAlign: 'left',
                          color: 'var(--ink-0)',
                        }}
                      >
                        <AffGlyph aff={affOf(d)} size={14} />
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontSize: 12, fontWeight: selected ? 600 : 500 }}>
                            {detectionClassLabel(d.class)}
                          </div>
                          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                            f={frameIdx} · t={t.toFixed(2)}s · {trackId ? `TRK-${trackId}` : 'no track'}
                          </div>
                        </div>
                        <span
                          className="mono"
                          style={{
                            fontSize: 10,
                            padding: '1px 5px',
                            border: '1px solid var(--line-2)',
                            color: '#5ee0a0',
                            borderRadius: 2,
                          }}
                        >
                          {Math.round((d.confidence || 0) * 100)}%
                        </span>
                      </button>
                    );
                  })}
                </>
              );
            })()}

            {sideTab === 'clips' && (
              <ClipsTab
                clips={clips}
                selectedId={selectedId}
                setSelectedId={setSelectedId}
              />
            )}
            {sideTab === 'detail' && selectedDetectionId != null && (() => {
              const det = detections.find((d) => d.id === selectedDetectionId);
              if (!det) return (
                <div style={{ padding: 16, color: 'var(--ink-2)', fontSize: 12 }}>
                  Detection {selectedDetectionId} not loaded yet.
                </div>
              );
              return (
                <ObjectDetailsForm
                  key={`fmv-det-${det.id}`}
                  source="fmv"
                  detectionId={det.id}
                  defaultClass={det.class}
                  title={detectionClassLabel(det.class)}
                  initial={{
                    object_class: det.class,
                    designation: det.metadata?.designation,
                    military_classification: det.metadata?.military_classification,
                    threat_level: (det as any).threat_level || det.metadata?.threat_level,
                    affiliation: (det as any).affiliation || det.metadata?.allegiance,
                  }}
                  canDelete={user?.role === 'admin'}
                  onDeleted={() => {
                    setDetections((cur) => cur.filter((d) => d.id !== det.id));
                    setSelectedDetectionId(null);
                    setSideTab('tracks');
                  }}
                  onSaved={() => {
                    if (selectedId != null) fetchDetections(selectedId);
                  }}
                  onViewOnMap={
                    onOpenMap ? () => onOpenMap(det.id) : undefined
                  }
                />
              );
            })()}
          </div>
        </aside>
      ) : (
        <aside
          className="fmv-sidebar"
          onClick={() => setRightOpen(true)}
          title="Show tracks"
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'flex-start',
            background: 'var(--bg-1)',
            borderLeft: '1px solid var(--line)',
            padding: '12px 0',
            gap: 14,
            cursor: 'pointer',
          }}
        >
          <ChevronLeft size={12} style={{ color: 'var(--ink-2)' }} />
          <Film size={14} style={{ color: accent }} />
          <span
            style={{
              writingMode: 'vertical-rl',
              transform: 'rotate(180deg)',
              fontSize: 10.5,
              letterSpacing: '.06em',
              color: 'var(--ink-1)',
              marginTop: 4,
            }}
          >
            Tracks · Upload
          </span>
          <span
            className="mono"
            style={{
              writingMode: 'vertical-rl',
              transform: 'rotate(180deg)',
              fontSize: 10,
              color: 'var(--ink-3)',
            }}
          >
            {inFrameCount} / {trackRows.length}
          </span>
        </aside>
      )}
    </div>
  );
}

/* ----------------------------------------------------------------------
 * Clips tab — clip library for selection. Uploads happen on the admin
 * Upload page (IngestConnect), not here.
 * --------------------------------------------------------------------*/

function ClipsTab({
  clips,
  selectedId,
  setSelectedId,
}: {
  clips: Clip[];
  selectedId: number | null;
  setSelectedId: (id: number) => void;
}) {
  const accent = 'var(--accent)';
  return (
    <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* CLIP LIBRARY */}
      <div>
        <div className="label-mono" style={{ marginBottom: 6 }}>
          Clip library · {clips.length}
        </div>
        {clips.length === 0 && (
          <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', padding: 8 }}>
            No clips yet. Use the admin Upload tab to add one.
          </div>
        )}
        {clips.map((clip) => {
          const selected = clip.id === selectedId;
          const statusTone =
            clip.status === 'ready' ? 'ok' :
            clip.status === 'error' ? 'crit' : 'unknown';
          return (
            <button
              className="clip-row"
              key={clip.id}
              type="button"
              onClick={() => setSelectedId(clip.id)}
              style={{
                display: 'grid',
                gridTemplateColumns: '24px 1fr auto',
                gap: 10,
                width: '100%',
                padding: '8px 0',
                borderTop: '1px solid var(--line)',
                background: selected ? `color-mix(in oklab, ${accent} 9%, transparent)` : 'transparent',
                border: 0,
                borderBottom: '1px solid var(--line)',
                cursor: 'pointer',
                textAlign: 'left',
                color: 'var(--ink-0)',
              }}
            >
              <Film size={13} style={{ color: selected ? accent : 'var(--ink-2)' }} />
              <div style={{ minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: selected ? 600 : 500,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {clip.name}
                </div>
                <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                  {fmt(clip.duration_seconds)} {clip.width && clip.height ? `· ${clip.width}×${clip.height}` : ''}
                </div>
              </div>
              <span className={`tag ${statusTone}`}>{clip.status}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

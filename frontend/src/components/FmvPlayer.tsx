import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { GeoJSON, MapContainer, Marker, Polyline, TileLayer } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import {
  AlertTriangle,
  Film,
  Pause,
  Play,
  SkipBack,
  SkipForward,
  UploadCloud,
} from 'lucide-react';
import { useEventStream } from '../hooks/useEventStream';
import {
  categoryFor,
  detectionClassLabel,
  useDetectionCategories,
} from '../utils/detectionTaxonomy';

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

function fmt(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '00:00';
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function normalizeBbox(det: Detection): { xyxy?: [number, number, number, number]; obb?: number[][] } {
  // The worker stores fmv_detections.bbox as a 4-element pixel array
  // [x, y, w, h] (see backend/worker.py:_xyxy_to_normalized_cxcywh called
  // without width/height). Other ingest paths may use the SAM3-native
  // {bbox_xyxy: [x1, y1, x2, y2]} object form, so support both.
  const raw = det.bbox ?? det.metadata?.bbox ?? null;
  let xyxy: [number, number, number, number] | undefined;
  if (Array.isArray(raw) && raw.length === 4) {
    const [x, y, w, h] = raw.map(Number);
    xyxy = [x, y, x + w, y + h];
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
  const obb = (raw && typeof raw === 'object' ? (raw as any).obb : null) || det.metadata?.obb;
  return {
    xyxy,
    obb: Array.isArray(obb) ? obb.map((pt: any) => pt.map(Number)) : undefined,
  };
}

function trackIdOf(det: Detection): string | null {
  return det.metadata?.track_id || det.bbox?.track_id || null;
}

export default function FmvPlayer() {
  const [clips, setClips] = useState<Clip[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [frames, setFrames] = useState<FrameRow[]>([]);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [hoverPct, setHoverPct] = useState<number | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const dragRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
        fetchDetections(selectedId);
      }
    },
    [selectedId, fetchFrames, fetchDetections],
  );
  useEventStream(selectedId ? `fmv:${selectedId}` : 'fmv:none', onClipChannel);

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

  const detectionsByFrame = useMemo(() => {
    const map = new Map<number, Detection[]>();
    for (const d of detections) {
      const arr = map.get(d.frame_index);
      if (arr) arr.push(d);
      else map.set(d.frame_index, [d]);
    }
    return map;
  }, [detections]);

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
      const dets = detectionsByFrame.get(frameIdx) || [];
      const sx = canvas.width / videoWidth;
      const sy = canvas.height / videoHeight;
      ctx.lineWidth = 2;
      ctx.font = '12px ui-monospace, monospace';
      for (const d of dets) {
        const cat = categoryFor((d.metadata?.branch_id as string) || 'Other', categories);
        const color = cat.color;
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        const { xyxy, obb } = normalizeBbox(d);
        // SAM3 video emits obb in `yolo_obb_normalized_xyxyxyxy` form (0..1).
        // Scale by image dimensions; sx/sy then map image px → canvas px.
        const obbNormalized = (d.metadata?.obb_format || '').includes('normalized');
        const obbScaleX = obbNormalized ? videoWidth * sx : sx;
        const obbScaleY = obbNormalized ? videoHeight * sy : sy;
        if (obb && obb.length >= 3) {
          ctx.beginPath();
          ctx.moveTo(obb[0][0] * obbScaleX, obb[0][1] * obbScaleY);
          for (let i = 1; i < obb.length; i++) ctx.lineTo(obb[i][0] * obbScaleX, obb[i][1] * obbScaleY);
          ctx.closePath();
          ctx.stroke();
          // Label anchored at the top-most OBB vertex so it stays inside the canvas.
          const top = obb.reduce((acc, p) => (p[1] < acc[1] ? p : acc), obb[0]);
          const lx = top[0] * obbScaleX;
          const ly = top[1] * obbScaleY;
          const label = `${detectionClassLabel(d.class)} ${Math.round((d.confidence || 0) * 100)}%`;
          const m = ctx.measureText(label);
          ctx.globalAlpha = 0.85;
          ctx.fillRect(lx, ly - 14, m.width + 6, 14);
          ctx.globalAlpha = 1;
          ctx.fillStyle = '#000';
          ctx.fillText(label, lx + 3, ly - 3);
          ctx.fillStyle = color;
        } else if (xyxy) {
          const [x1, y1, x2, y2] = xyxy;
          ctx.strokeRect(x1 * sx, y1 * sy, (x2 - x1) * sx, (y2 - y1) * sy);
          const label = `${detectionClassLabel(d.class)} ${Math.round((d.confidence || 0) * 100)}%`;
          const m = ctx.measureText(label);
          ctx.globalAlpha = 0.85;
          ctx.fillRect(x1 * sx, y1 * sy - 14, m.width + 6, 14);
          ctx.globalAlpha = 1;
          ctx.fillStyle = '#000';
          ctx.fillText(label, x1 * sx + 3, y1 * sy - 3);
          ctx.fillStyle = color;
        }
      }
      rafRef.current = requestAnimationFrame(draw);
    };

    rafRef.current = requestAnimationFrame(draw);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [detectionsByFrame, categories, fps, videoWidth, videoHeight]);

  // Resize canvas to match the video element's rendered size.
  useEffect(() => {
    const wrapper = wrapperRef.current;
    const canvas = canvasRef.current;
    const v = videoRef.current;
    if (!wrapper || !canvas || !v) return;
    const obs = new ResizeObserver(() => {
      canvas.style.width = `${v.clientWidth}px`;
      canvas.style.height = `${v.clientHeight}px`;
    });
    obs.observe(v);
    obs.observe(wrapper);
    return () => obs.disconnect();
  }, [selectedClip]);

  // -------------------- Upload --------------------

  const handleUpload = useCallback(
    async (file: File, srt?: File | null) => {
      setUploadError(null);
      setUploading(true);
      setUploadProgress(0);
      try {
        const fd = new FormData();
        fd.append('file', file);
        fd.append('name', file.name);
        if (srt) fd.append('srt', srt);
        const res = await axios.post(`${API_URL}/api/fmv/clips`, fd, {
          onUploadProgress: (e) => {
            const pct = Math.round((e.loaded / (e.total || 1)) * 100);
            setUploadProgress(pct);
          },
        });
        await fetchClips();
        if (res.data?.clip?.id) setSelectedId(res.data.clip.id);
      } catch (err: any) {
        setUploadError(err?.response?.data?.detail || err?.message || 'Upload failed');
      } finally {
        setUploading(false);
        setUploadProgress(0);
      }
    },
    [fetchClips],
  );

  const onPickFiles = useCallback(
    (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return;
      const files = Array.from(fileList);
      const video = files.find((f) => /\.(mp4|mov|m4v|ts|mpeg|mpg)$/i.test(f.name));
      const srt = files.find((f) => /\.srt$/i.test(f.name));
      if (!video) {
        setUploadError('Drop an .mp4 / .mov / .ts file. SRT sidecar is optional.');
        return;
      }
      handleUpload(video, srt || null);
    },
    [handleUpload],
  );

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

  const showMap = platformPath.length > 0;

  // -------------------- Render --------------------

  return (
    <div className="sentinel-view" style={{ display: 'grid', gridTemplateColumns: '260px 1fr 360px', gap: 8, height: '100%' }}>
      {/* LEFT — clip library */}
      <aside className="sentinel-panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div className="sentinel-panel-header">
          <Film size={14} /> <span>FMV LIBRARY</span>
          <span className="sentinel-tag info" style={{ marginLeft: 'auto' }}>{clips.length}</span>
        </div>
        <div style={{ padding: 8 }}>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".mp4,.mov,.m4v,.ts,.mpeg,.mpg,.srt"
            style={{ display: 'none' }}
            onChange={(e) => onPickFiles(e.target.files)}
          />
          <div
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              onPickFiles(e.dataTransfer.files);
            }}
            className="sentinel-row"
            style={{
              cursor: 'pointer',
              padding: '14px 10px',
              border: '1px dashed var(--line)',
              textAlign: 'center',
              flexDirection: 'column',
              gap: 4,
            }}
          >
            <UploadCloud size={20} />
            <div style={{ fontSize: 11, fontWeight: 600 }}>Drop FMV clip</div>
            <div style={{ fontSize: 10, color: 'var(--muted)' }}>mp4 / mov / ts (+ optional .srt)</div>
          </div>
          {uploading && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 10, color: 'var(--muted)' }}>UPLOADING {uploadProgress}%</div>
              <div style={{ height: 4, background: 'var(--line)', borderRadius: 2, overflow: 'hidden', marginTop: 4 }}>
                <div style={{ width: `${uploadProgress}%`, height: '100%', background: 'var(--accent)' }} />
              </div>
            </div>
          )}
          {uploadError && (
            <div className="sentinel-tag crit" style={{ marginTop: 8, display: 'block' }}>{uploadError}</div>
          )}
        </div>
        <div className="sentinel-scroll" style={{ flex: 1, padding: '0 8px 8px' }}>
          {clips.length === 0 && !uploading && (
            <div style={{ fontSize: 11, color: 'var(--muted)', textAlign: 'center', padding: 16 }}>
              No clips yet. Drop a file above to begin.
            </div>
          )}
          {clips.map((clip) => {
            const selected = clip.id === selectedId;
            return (
              <button
                key={clip.id}
                type="button"
                className={`sentinel-row ${selected ? 'selected' : ''}`}
                onClick={() => setSelectedId(clip.id)}
                style={{ width: '100%', textAlign: 'left', flexDirection: 'column', alignItems: 'stretch', padding: 8, marginBottom: 4 }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Film size={12} />
                  <span style={{ fontSize: 11, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
                    {clip.name}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 4, marginTop: 4, fontSize: 10, color: 'var(--muted)' }}>
                  <span className={`sentinel-tag ${clip.status === 'ready' ? 'ok' : clip.status === 'error' ? 'crit' : 'warn'}`}>{clip.status}</span>
                  <span>{fmt(clip.duration_seconds)}</span>
                  {clip.width && clip.height && <span>{clip.width}×{clip.height}</span>}
                </div>
              </button>
            );
          })}
        </div>
      </aside>

      {/* CENTER — player */}
      <section style={{ display: 'flex', flexDirection: 'column', minWidth: 0, gap: 8 }}>
        <div
          ref={wrapperRef}
          className="sentinel-panel"
          style={{ position: 'relative', flex: 1, minHeight: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden', background: '#000' }}
        >
          {!selectedClip && (
            <div style={{ color: 'var(--muted)', fontSize: 12, textAlign: 'center', padding: 24 }}>
              <Film size={36} style={{ opacity: 0.5 }} />
              <div style={{ marginTop: 12 }}>Select a clip from the library, or drop a new one.</div>
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
              {selectedClip.status !== 'ready' && (
                <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.6)' }}>
                  <span className="sentinel-tag info">TRANSCODING…</span>
                </div>
              )}
              {selectedClip.status === 'ready' && !hasRealTelemetry && frames.length > 0 && (
                <div style={{ position: 'absolute', top: 8, left: 8 }}>
                  <span
                    className="sentinel-tag warn"
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'help' }}
                    title="No MISB-0601 KLV, MP4 GPMD, or co-uploaded .srt sidecar was detected in this clip. The map is showing a synthetic sine-wave path for demo. Upload an FMV clip with embedded telemetry or a matching .srt to see real coordinates."
                  >
                    <AlertTriangle size={11} /> SYNTHETIC TELEMETRY
                  </span>
                </div>
              )}
            </>
          )}
        </div>

        {/* Timeline + transport */}
        {selectedClip && (
          <div className="sentinel-panel" style={{ padding: 8 }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                fontSize: 9,
                color: 'var(--muted)',
                letterSpacing: '0.05em',
                marginBottom: 3,
                fontFamily: 'ui-monospace, monospace',
              }}
            >
              <span title="Bars show the number of detections in each time bucket — taller = busier frames">
                ◧ DETECTION DENSITY
              </span>
              <span title="Drag the bar to scrub. ←/→ step one frame. Space toggles play.">
                SCRUB ◨
              </span>
            </div>
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
                height: 44,
                background: 'var(--color-sentinel-panel-2, #161a1e)',
                borderRadius: 4,
                cursor: 'pointer',
                overflow: 'hidden',
              }}
            >
              {/* Histogram bars */}
              <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'flex-end' }}>
                {histogram.counts.map((count, idx) => {
                  const h = histogram.max ? (count / histogram.max) * 100 : 0;
                  return (
                    <div
                      key={idx}
                      style={{
                        flex: 1,
                        height: `${h}%`,
                        background: h > 0 ? 'rgba(124,255,180,0.55)' : 'transparent',
                        marginRight: 1,
                      }}
                    />
                  );
                })}
              </div>
              {/* Progress fill */}
              <div
                style={{
                  position: 'absolute',
                  left: 0,
                  top: 0,
                  bottom: 0,
                  width: `${duration ? (currentTime / duration) * 100 : 0}%`,
                  background: 'rgba(78,161,255,0.18)',
                  borderRight: '1px solid #4ea1ff',
                }}
              />
              {/* Hover scrub indicator */}
              {hoverPct !== null && (
                <div
                  style={{
                    position: 'absolute',
                    left: `${hoverPct * 100}%`,
                    top: 0,
                    bottom: 0,
                    width: 1,
                    background: 'rgba(255,255,255,0.4)',
                    pointerEvents: 'none',
                  }}
                />
              )}
            </div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 8, fontSize: 11 }}>
              <button type="button" className="sentinel-btn" onClick={() => seekTo(currentTime - 1 / fps)} title="Previous frame">
                <SkipBack size={12} />
              </button>
              <button type="button" className="sentinel-btn primary" onClick={togglePlay} title="Play/Pause (Space)">
                {playing ? <Pause size={12} /> : <Play size={12} />}
              </button>
              <button type="button" className="sentinel-btn" onClick={() => seekTo(currentTime + 1 / fps)} title="Next frame">
                <SkipForward size={12} />
              </button>
              <span style={{ fontFamily: 'ui-monospace, monospace', color: 'var(--muted)', marginLeft: 8 }}>
                {fmt(currentTime)} / {fmt(duration || selectedClip.duration_seconds)}
              </span>
              <span style={{ fontFamily: 'ui-monospace, monospace', color: 'var(--muted)', marginLeft: 'auto' }}>
                FRAME {currentFrameIdx}
              </span>
            </div>
          </div>
        )}
      </section>

      {/* RIGHT — map + detections */}
      <aside className="sentinel-panel" style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {showMap && (
          <div style={{ height: 280, position: 'relative' }}>
            <div className="sentinel-panel-header" style={{ position: 'absolute', top: 0, left: 0, right: 0, zIndex: 500 }}>
              <span>TELEMETRY MAP</span>
              <span
                className="sentinel-tag info"
                style={{ marginLeft: 'auto' }}
                title={
                  currentFrame?.telemetry?.source === 'misb-klv'
                    ? 'Real telemetry parsed from MISB ST 0601 KLV stream'
                    : currentFrame?.telemetry?.source === 'srt'
                      ? 'Real telemetry parsed from co-uploaded .srt sidecar (DJI/Autel format)'
                      : currentFrame?.telemetry?.source === 'gpmd'
                        ? 'Real telemetry parsed from MP4 GPMD track (GoPro/DJI)'
                        : currentFrame?.telemetry?.source === 'fixture'
                          ? 'No real KLV/SRT/GPMD detected — showing synthetic sine-wave coords for demo'
                          : 'Telemetry source'
                }
              >
                {currentFrame?.telemetry?.source?.toUpperCase() || 'TELEMETRY'}
              </span>
            </div>
            <MapContainer
              key={selectedId || 'empty'}
              center={mapCenter}
              zoom={15}
              style={{ width: '100%', height: '100%' }}
              attributionControl={false}
            >
              <TileLayer url={CARTO_BASEMAP_URL} />
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
            {/* Compact legend — what each glyph on the map represents. */}
            <div
              style={{
                position: 'absolute',
                left: 6,
                bottom: 6,
                zIndex: 500,
                background: 'rgba(16, 19, 22, 0.85)',
                border: '1px solid var(--line)',
                borderRadius: 3,
                padding: '6px 8px',
                fontSize: 10,
                lineHeight: '14px',
                color: 'var(--sentinel-text, #cfd6dc)',
                pointerEvents: 'none',
                fontFamily: 'ui-monospace, monospace',
              }}
              title="Live map overlay — updates every frame from telemetry"
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ display: 'inline-block', width: 18, height: 0, borderTop: '2px solid #7cf' }} />
                <span>Platform track</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ display: 'inline-block', width: 10, height: 10, background: '#fdb44b', border: '1px solid #001' }} />
                <span>Platform (current)</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 5, background: '#7cf', border: '1px solid #001' }} />
                <span>Frame center</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ display: 'inline-block', width: 14, height: 8, border: '1px solid #7cf', background: 'rgba(124,204,255,0.18)' }} />
                <span>Sensor footprint</span>
              </div>
            </div>
          </div>
        )}
        {!showMap && (
          <div className="sentinel-panel-header">
            <span>DETECTIONS</span>
            <span className="sentinel-tag warn" style={{ marginLeft: 'auto' }}>NO TELEMETRY</span>
          </div>
        )}
        <div
          className="sentinel-panel-header"
          title="Each row groups consecutive frames where the same track (or class) was detected. Click to jump to the first frame of that track. The highlighted row contains the current frame."
        >
          <span>DETECTED OBJECTS</span>
          <span className="sentinel-tag info" style={{ marginLeft: 'auto' }}>{detectionGroups.length}</span>
        </div>
        <div className="sentinel-scroll" style={{ flex: 1, padding: '0 8px 8px' }}>
          {detectionGroups.length === 0 && (
            <div style={{ fontSize: 11, color: 'var(--muted)', textAlign: 'center', padding: 12 }}>
              {selectedClip
                ? selectedClip.status === 'ready'
                  ? 'No detections yet — SAM3 pipeline may still be running.'
                  : 'Waiting for transcode…'
                : 'Select a clip to view detections.'}
            </div>
          )}
          {detectionGroups.map((g) => {
            const active = currentFrameIdx >= g.first && currentFrameIdx <= g.last;
            return (
              <button
                key={g.key}
                type="button"
                className={`sentinel-row ${active ? 'selected' : ''}`}
                onClick={() => seekTo(g.first / fps)}
                style={{ width: '100%', textAlign: 'left', padding: 8, marginBottom: 4, display: 'flex', gap: 8, alignItems: 'center' }}
              >
                <span
                  className="sentinel-dot"
                  style={{ background: categoryFor((g as any).branch_id || 'Other', categories).color }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 11, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {detectionClassLabel(g.className)}
                    {g.trackId && <span style={{ color: 'var(--muted)', marginLeft: 6 }}>#{g.trackId}</span>}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--muted)' }}>
                    frames {g.first}–{g.last} · ×{g.count} · max {(g.topConfidence * 100) | 0}%
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </aside>
    </div>
  );
}

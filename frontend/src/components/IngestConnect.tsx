import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  ChevronDown,
  ChevronRight,
  DatabaseZap,
  FileImage,
  ListChecks,
  Search,
  ShieldCheck,
  UploadCloud,
  X,
} from 'lucide-react';
import { useEventStream } from '../hooks/useEventStream';
import { type UploadJob, isUploadActive, uploadMessage, uploadProgress, uploadProgressClass, uploadStage } from '../utils/uploadProgress';
import {
  type Sensor,
  isSam3Prompt,
  parseCustomPrompts,
  pipelineForSensor,
  uploadSensorToTag,
} from '../utils/defenceOntology';
import {
  flattenObjects,
  useOntology,
  type OntologyBranch,
  type OntologyObject,
} from '../utils/useOntology';
import { BRANCH_ICON_BY_KEY, ObjectIcon } from '../utils/branchIcons';
import type { BranchIconKey } from '../utils/defenceOntology';
import { CircleHelp } from 'lucide-react';

const API_URL = import.meta.env.VITE_API_URL || '';

const HIGH_RES_GSD_THRESHOLD = 0.3;

function branchObjectIds(branch: OntologyBranch): string[] {
  return [
    ...(branch.objects || []).map((item) => item.id),
    ...(branch.children || []).flatMap(branchObjectIds),
  ];
}

function isHighRes(obj: OntologyObject): boolean {
  return typeof obj.min_gsd_meters === 'number' && obj.min_gsd_meters <= HIGH_RES_GSD_THRESHOLD;
}

function branchIconComponent(iconKey: string | null | undefined) {
  if (!iconKey) return CircleHelp;
  return BRANCH_ICON_BY_KEY[iconKey as BranchIconKey] ?? CircleHelp;
}

export default function IngestConnect() {
  const [file, setFile] = useState<File | null>(null);
  const [sensorType, setSensorType] = useState('Optical');
  const [selectedDefenceIds, setSelectedDefenceIds] = useState<Set<string>>(new Set());
  const [objectSearch, setObjectSearch] = useState('');
  const [customObjects, setCustomObjects] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState('');
  const [uploadTransferProgress, setUploadTransferProgress] = useState(0);
  const [activeUploadId, setActiveUploadId] = useState<string | null>(null);
  const [uploadJobs, setUploadJobs] = useState<UploadJob[]>([]);
  const [expandedBranches, setExpandedBranches] = useState<Set<string>>(() => new Set());
  const [incompatibleNotice, setIncompatibleNotice] = useState<string | null>(null);

  const sensorTag: Sensor = uploadSensorToTag(sensorType);
  const sensorPipeline = useMemo(() => pipelineForSensor(sensorType), [sensorType]);

  // Live ontology, server-filtered for the active sensor. The hook caches
  // per-sensor and reacts to backend version bumps automatically.
  const { branches: ontologyBranches } = useOntology({ sensor: sensorTag });

  const defenceObjectById = useMemo(() => {
    const map = new Map<string, OntologyObject>();
    flattenObjects(ontologyBranches).forEach((obj) => map.set(obj.id, obj));
    return map;
  }, [ontologyBranches]);

  // Auto-expand the first top-level branch the first time we see a tree (or
  // any time the sensor changes and the previously expanded branches are
  // no longer present).
  useEffect(() => {
    if (!ontologyBranches.length) return;
    setExpandedBranches((current) => {
      if (current.size > 0) return current;
      return new Set([ontologyBranches[0].id]);
    });
  }, [ontologyBranches]);

  const fetchUploadJobs = useCallback(async () => {
    try {
      const response = await axios.get(`${API_URL}/api/ingest/uploads`);
      setUploadJobs(response.data.uploads || []);
    } catch (error) {
      console.error('Error fetching uploads:', error);
    }
  }, []);

  useEffect(() => {
    fetchUploadJobs();
  }, [fetchUploadJobs]);

  useEventStream('imagery', useCallback(() => {
    fetchUploadJobs();
  }, [fetchUploadJobs]));

  useEventStream('ops', useCallback((message: any) => {
    if (String(message?.type || '').startsWith('imagery_') || message?.type === 'upload_received') {
      fetchUploadJobs();
    }
  }, [fetchUploadJobs]));

  const activeJob = uploadJobs.find((job) => job.upload_id === activeUploadId)
    || uploadJobs.find((job) => job.media_type === 'imagery' && isUploadActive(job))
    || uploadJobs.find((job) => job.media_type === 'imagery')
    || null;

  const customPrompts = useMemo(() => parseCustomPrompts(customObjects), [customObjects]);
  // Real prompts to send to SAM 3. Sentinel "__prithvi_*__" / aux markers are
  // dropped here — they live in the JSON only to surface specialist-model
  // outputs (Prithvi burn / flood / crop) in the legend, not to be sent as
  // text prompts to the SAM 3 inference service.
  const selectedPrompts = useMemo(() => {
    const seen = new Set<string>();
    const prompts = [
      ...Array.from(selectedDefenceIds)
        .map((id) => defenceObjectById.get(id)?.prompt)
        .filter((value): value is string => Boolean(value))
        .filter(isSam3Prompt),
      ...customPrompts,
    ];
    return prompts.filter((prompt) => {
      const key = prompt.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [customPrompts, selectedDefenceIds]);

  const searchTerm = objectSearch.trim().toLowerCase();
  // The hook delivers a tree pre-filtered by sensor on the server side
  // (`/api/ontology?sensor=...`). All objects in `defenceObjectById` are
  // already sensor-compatible, so visibility just folds in the search box.
  const visibleDefenceIds = useMemo(() => {
    const ids = new Set<string>();
    defenceObjectById.forEach((item) => {
      if (!searchTerm || `${item.label} ${item.prompt}`.toLowerCase().includes(searchTerm)) {
        ids.add(item.id);
      }
    });
    return ids;
  }, [defenceObjectById, searchTerm]);

  // When the sensor changes, drop any selected ids that are no longer
  // sensor-compatible. We only show a notice for the user-driven case;
  // initial mount (from an empty selection) is silent.
  useEffect(() => {
    if (selectedDefenceIds.size === 0) return;
    if (defenceObjectById.size === 0) return; // tree not loaded yet
    setSelectedDefenceIds((current) => {
      const next = new Set<string>();
      let dropped = 0;
      current.forEach((id) => {
        if (defenceObjectById.has(id)) next.add(id);
        else dropped += 1;
      });
      if (dropped > 0) {
        setIncompatibleNotice(`${dropped} item${dropped === 1 ? '' : 's'} removed — incompatible with ${sensorTag.toUpperCase()}`);
        window.setTimeout(() => setIncompatibleNotice(null), 5000);
      }
      return dropped > 0 ? next : current;
    });
    // intentionally not depending on selectedDefenceIds — we only want to
    // trim when the catalog (tree) shifts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defenceObjectById]);

  // When the user types a search, auto-expand any branch with visible matches.
  useEffect(() => {
    if (!searchTerm) return;
    const matchingBranchIds = new Set<string>();
    const walk = (branch: OntologyBranch) => {
      const ids = branchObjectIds(branch);
      if (ids.some((id) => visibleDefenceIds.has(id))) matchingBranchIds.add(branch.id);
      branch.children?.forEach(walk);
    };
    ontologyBranches.forEach(walk);
    setExpandedBranches((current) => {
      const next = new Set(current);
      matchingBranchIds.forEach((id) => next.add(id));
      return next;
    });
  }, [searchTerm, visibleDefenceIds, ontologyBranches]);

  const toggleBranchExpanded = (id: string) => {
    setExpandedBranches((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleDefenceObject = (id: string) => {
    setSelectedDefenceIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleBranchSelection = (branch: OntologyBranch) => {
    const ids = branchObjectIds(branch).filter((id) => visibleDefenceIds.has(id));
    if (!ids.length) return;
    setSelectedDefenceIds((current) => {
      const next = new Set(current);
      const allSelected = ids.every((id) => next.has(id));
      ids.forEach((id) => {
        if (allSelected) next.delete(id);
        else next.add(id);
      });
      return next;
    });
  };

  const selectVisibleDefence = () => {
    setSelectedDefenceIds((current) => new Set([...Array.from(current), ...Array.from(visibleDefenceIds)]));
  };

  const clearAllPrompts = () => {
    setSelectedDefenceIds(new Set());
    setCustomObjects('');
  };

  useEffect(() => {
    if (!activeJob || !isUploadActive(activeJob)) return;
    const timer = window.setInterval(() => {
      fetchUploadJobs();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [activeJob?.upload_id, activeJob?.status, fetchUploadJobs]);

  useEffect(() => {
    if (!activeJob || activeJob.upload_id !== activeUploadId) return;
    setUploadStatus(`${uploadStage(activeJob)} ${uploadProgress(activeJob)}%`);
  }, [activeJob, activeUploadId]);

  const uploadImage = async () => {
    if (!file || uploading) return;
    setUploading(true);
    setUploadTransferProgress(0);
    setUploadStatus('');
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('sensor_type', sensorType);
      form.append('modality', sensorPipeline.modality);
      form.append('enabled_layers', JSON.stringify(sensorPipeline.enabledLayers));
      form.append('auto_process', 'true');
      if (selectedPrompts.length > 0) {
        form.append('text_prompts', JSON.stringify(selectedPrompts));
      }
      const response = await axios.post(`${API_URL}/api/ingest/upload`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (event) => {
          if (event.total) {
            setUploadTransferProgress(Math.round((event.loaded / event.total) * 100));
          }
        },
      });
      setActiveUploadId(response.data.upload_id || null);
      setUploadStatus(`Queued ${response.data.task_id || response.data.filename}`);
      await fetchUploadJobs();
      setFile(null);
    } catch (error: any) {
      setUploadStatus(error.response?.data?.detail || 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const renderObject = (item: OntologyObject, parentBranch: OntologyBranch) => {
    if (!visibleDefenceIds.has(item.id)) return null;
    const selected = selectedDefenceIds.has(item.id);
    const highRes = isHighRes(item);
    const branchIconKey = (parentBranch.icon_key ?? null) as BranchIconKey | null;
    return (
      <button
        key={item.id}
        type="button"
        onClick={() => toggleDefenceObject(item.id)}
        title={`${item.prompt}${highRes ? ' — needs <=0.3 m GSD imagery' : ''}`}
        className={`flex items-start gap-2 border px-3 py-2 text-left transition ${
          selected
            ? 'border-blue-400/70 bg-blue-500/15 text-blue-100'
            : 'border-slate-800 bg-slate-950/40 text-slate-300 hover:border-slate-600'
        }`}
      >
        <input type="checkbox" checked={selected} readOnly className="mt-0.5 shrink-0" />
        <span
          className="mt-0.5 shrink-0"
          style={{ color: parentBranch.color || undefined }}
          aria-hidden
        >
          <ObjectIcon prompt={item.prompt} branchIconKey={branchIconKey} className="h-4 w-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="block text-xs font-semibold leading-snug">{item.label}</span>
            {highRes && (
              <span className="border border-amber-500/50 bg-amber-500/10 text-amber-300 px-1 py-px font-mono text-[9px] leading-none rounded">
                HIGH-RES
              </span>
            )}
          </span>
          <span className="block truncate font-mono text-[10px] text-slate-500">{item.prompt}</span>
        </span>
      </button>
    );
  };

  const renderBranch = (branch: OntologyBranch, depth = 0) => {
    const ids = branchObjectIds(branch).filter((id) => visibleDefenceIds.has(id));
    if (!ids.length) return null;
    const selectedCount = ids.filter((id) => selectedDefenceIds.has(id)).length;
    const allSelected = selectedCount === ids.length;
    const partial = selectedCount > 0 && !allSelected;
    const expanded = expandedBranches.has(branch.id);
    const wrapperClass = depth === 0
      ? 'border border-slate-800 bg-slate-950/30'
      : 'border-l border-slate-800 ml-2';
    const branchColor = branch.color || '#727a83';
    const BranchIconCmp = branchIconComponent(branch.icon_key);
    return (
      <div key={branch.id} className={wrapperClass}>
        <div
          className={`flex items-center gap-2 px-3 py-2 ${depth === 0 ? 'border-b border-slate-800/70' : ''}`}
        >
          <button
            type="button"
            onClick={() => toggleBranchExpanded(branch.id)}
            className="text-slate-400 hover:text-slate-200 shrink-0"
            aria-label={expanded ? 'Collapse' : 'Expand'}
          >
            {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </button>
          <button
            type="button"
            onClick={() => toggleBranchSelection(branch)}
            className="flex flex-1 items-center gap-2 text-left"
          >
            <input
              type="checkbox"
              checked={allSelected}
              ref={(el) => {
                if (el) el.indeterminate = partial;
              }}
              readOnly
              className="shrink-0"
            />
            <span
              className="shrink-0 grid place-items-center rounded border w-6 h-6"
              style={{ color: branchColor, borderColor: `${branchColor}55`, background: `${branchColor}1a` }}
              aria-hidden
            >
              <BranchIconCmp className="w-3.5 h-3.5" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-xs font-bold uppercase tracking-wider text-slate-200">{branch.label}</span>
              <span className="block font-mono text-[10px] text-slate-500">{selectedCount}/{ids.length} selected</span>
            </span>
            <span
              className="shrink-0 font-mono text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded border"
              style={{ color: branchColor, borderColor: `${branchColor}55` }}
              aria-hidden
            >
              {branch.short || branch.id.slice(0, 3).toUpperCase()}
            </span>
          </button>
        </div>
        {expanded && (
          <div className="space-y-2 px-3 pb-3 pt-2">
            {branch.children?.map((child) => renderBranch(child, depth + 1))}
            {branch.objects && branch.objects.length > 0 && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {branch.objects.map((obj) => renderObject(obj, branch))}
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  const showProgressBar = uploading || (activeJob && isUploadActive(activeJob));
  const transferOrJobProgress = uploading ? uploadTransferProgress : uploadProgress(activeJob);

  return (
    <div className="w-full h-full bg-slate-950 text-slate-200 overflow-auto">
      <div className="max-w-4xl mx-auto p-6 flex flex-col gap-5">
        <div className="flex items-center gap-3 border-b border-slate-800 pb-4">
          <UploadCloud className="w-7 h-7 text-blue-400" />
          <h2 className="text-xl font-bold uppercase tracking-wider">Imagery Upload</h2>
        </div>

        {incompatibleNotice && (
          <div className="border border-amber-500/40 bg-amber-500/10 text-amber-200 text-xs px-3 py-2 rounded">
            {incompatibleNotice}
          </div>
        )}

        <label className="min-h-56 border border-dashed border-slate-700 bg-slate-900/40 hover:bg-slate-900 transition rounded flex flex-col items-center justify-center cursor-pointer">
          <FileImage className="w-12 h-12 text-slate-500 mb-3" />
          <div className="font-mono text-sm text-slate-300">{file ? file.name : 'Select raster'}</div>
          <div className="font-mono text-xs text-slate-500 mt-2">TIF / JP2 / NetCDF / PNG / JPG</div>
          <input
            type="file"
            accept=".tif,.tiff,.jp2,.j2k,.nc,.netcdf,.png,.jpg,.jpeg"
            onChange={(event) => setFile(event.target.files?.[0] || null)}
            className="hidden"
          />
        </label>

        <div className="flex flex-col gap-2">
          <div className="flex flex-col sm:flex-row gap-3">
            <select
              value={sensorType}
              onChange={(event) => setSensorType(event.target.value)}
              className="bg-slate-900 border border-slate-700 rounded px-3 py-3 text-sm sm:w-56"
              title="Sensor type — auto-selects relevant inference layers"
            >
              <option value="Optical">Optical (RGB)</option>
              <option value="Multispectral">Multispectral</option>
              <option value="Hyperspectral">Hyperspectral</option>
              <option value="SAR">SAR</option>
            </select>
            <button
              onClick={uploadImage}
              disabled={!file || uploading}
              className="flex-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed rounded px-4 py-3 text-sm font-bold uppercase tracking-wider flex items-center justify-center gap-2"
            >
              <DatabaseZap className="w-4 h-4" /> Upload &amp; Process
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5 items-center font-mono text-[10px] text-slate-400">
            <span className="text-slate-500 uppercase tracking-wider">Models:</span>
            {sensorPipeline.models.map((model) => (
              <span
                key={model}
                className="border border-blue-500/40 bg-blue-500/10 text-blue-300 px-2 py-0.5 rounded"
              >
                {model}
              </span>
            ))}
          </div>
          {/* VRAM accounting bar from /api/inference/dashboard */}
          <VramBar />

          {/* Chip pipeline visualiser — live status of upload-job chips */}
          <ChipPipelineGrid activeJob={activeJob || undefined} />

          {sensorPipeline.warning && (
            <div className="flex items-start gap-2 border border-yellow-500/40 bg-yellow-500/5 text-yellow-200 text-xs px-3 py-2 rounded">
              <span className="font-bold uppercase tracking-wider text-[10px]">Note:</span>
              <span>{sensorPipeline.warning}</span>
            </div>
          )}
        </div>

        <div className="border border-slate-800 bg-slate-900/70 rounded">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
            <div className="flex items-center gap-3">
              <ShieldCheck className="w-5 h-5 text-blue-400" />
              <div>
                <div className="text-xs font-bold uppercase tracking-wider text-slate-200">Detection Objects</div>
                <div className="font-mono text-[10px] text-slate-500">
                  Pick what SAM3 should look for &middot; filtered for <span className="text-blue-300">{sensorTag.toUpperCase()}</span>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2 font-mono text-[10px]">
              <span className="border border-blue-500/40 bg-blue-500/10 text-blue-300 px-2 py-1 rounded">
                {selectedPrompts.length} prompts
              </span>
              <button
                type="button"
                onClick={selectVisibleDefence}
                title="Select all visible objects"
                className="border border-slate-700 px-2 py-1 rounded text-slate-300 hover:border-blue-400"
              >
                <ListChecks className="w-3 h-3" />
              </button>
              <button
                type="button"
                onClick={clearAllPrompts}
                title="Clear all selected"
                className="border border-slate-700 px-2 py-1 rounded text-slate-300 hover:border-red-400"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          </div>

          <div className="p-4 space-y-3">
            <div className="flex items-center gap-2 border border-slate-800 bg-slate-950 px-3 py-2 rounded">
              <Search className="h-4 w-4 text-slate-500" />
              <input
                value={objectSearch}
                onChange={(event) => setObjectSearch(event.target.value)}
                placeholder="Search objects (e.g. tank, runway, decoy)"
                className="min-w-0 flex-1 bg-transparent text-sm text-slate-200 outline-none placeholder:text-slate-600"
              />
            </div>
            <div className="max-h-96 overflow-auto space-y-2 pr-1">
              {ontologyBranches.map((branch) => renderBranch(branch))}
            </div>
            <label className="block">
              <span className="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-300">Custom Objects</span>
              <textarea
                value={customObjects}
                onChange={(event) => setCustomObjects(event.target.value)}
                placeholder="one object per line, or comma separated"
                className="h-24 w-full resize-none border border-slate-800 bg-slate-950 px-3 py-2 font-mono text-xs text-slate-200 outline-none placeholder:text-slate-600 focus:border-blue-500/70 rounded"
              />
            </label>
            <div className="font-mono text-[10px] text-slate-500">
              {selectedPrompts.length
                ? `${selectedPrompts.length} prompts will override the default profile.`
                : 'No object override selected. Upload will use the default satellite prompt profile.'}
            </div>
          </div>
        </div>

        {showProgressBar && (
          <div className="border border-slate-800 bg-slate-900/80 rounded px-4 py-3 text-sm">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-xs uppercase tracking-wider text-slate-400">
                  {uploading ? 'transferring' : uploadStage(activeJob)}
                </div>
                <div className="mt-1 font-mono text-xs text-slate-300 truncate">
                  {uploading ? `${uploadTransferProgress}% uploaded` : uploadMessage(activeJob)}
                </div>
              </div>
              <div className="font-mono text-xs text-slate-400">
                {transferOrJobProgress}%
              </div>
            </div>
            <div className="mt-3 h-2 w-full bg-slate-800 overflow-hidden rounded">
              <div
                className={`h-full transition-all duration-500 ${uploading ? 'bg-sky-400' : uploadProgressClass(activeJob)}`}
                style={{ width: `${transferOrJobProgress}%` }}
              />
            </div>
            {activeJob?.celery_task_id && (
              <div className="mt-2 text-[10px] font-mono text-slate-500 truncate">task {activeJob.celery_task_id}</div>
            )}
          </div>
        )}

        {uploadStatus && !showProgressBar && (
          <div className="border border-slate-800 bg-slate-900 rounded px-4 py-3 text-sm font-mono text-blue-300">
            {uploadStatus}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/*  Ingest+ enhancements: VRAM bar + chip pipeline visualiser              */
/* ---------------------------------------------------------------------- */

function VramBar() {
  const [data, setData] = useState<{ vram_used_gib?: number | null; vram_total_gib?: number | null; models?: any[] }>({});
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const r = await axios.get(`${API_BASE_URL}/api/inference/dashboard`);
        if (!cancelled) setData(r.data || {});
      } catch {
        if (!cancelled) setData({});
      }
    };
    load();
    const id = window.setInterval(load, 8000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);
  const used = Number(data.vram_used_gib ?? 0);
  const total = Number(data.vram_total_gib ?? 0);
  if (!total) {
    return (
      <div className="font-mono text-[10px] text-slate-500">
        VRAM ·{' '}
        <span className="text-slate-400">
          {used > 0 ? `${used.toFixed(1)} GiB used` : 'sidecar not reporting'}
        </span>
      </div>
    );
  }
  const pct = Math.min(100, Math.round((used / total) * 100));
  const color = pct > 85 ? 'bg-red-500' : pct > 60 ? 'bg-amber-400' : 'bg-blue-500';
  const onlineCount = (data.models || []).filter((m: any) => (m.status || '').toLowerCase() === 'online').length;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between font-mono text-[10px] text-slate-400">
        <span>
          <span className="text-slate-500 uppercase tracking-wider">VRAM</span>{' '}
          {used.toFixed(1)} / {total.toFixed(1)} GiB · {onlineCount}/{(data.models || []).length} loaded
        </span>
        <span className={pct > 85 ? 'text-red-400' : pct > 60 ? 'text-amber-300' : 'text-blue-300'}>{pct}%</span>
      </div>
      <div className="h-1.5 w-full bg-slate-800 rounded overflow-hidden">
        <div className={`h-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function ChipPipelineGrid({ activeJob }: { activeJob: UploadJob | undefined }) {
  // Map the upload-job metadata onto a chip grid. Backend stores
  // processed_chips / planned_chips so we synthesize: done = processed,
  // queued = planned - processed, inferring = the next 6 after processed,
  // error = anything from job.metadata.errors.
  const planned = Number((activeJob?.metadata as any)?.planned_chips ?? (activeJob?.metadata as any)?.total_chips ?? 0);
  const processed = Number((activeJob?.metadata as any)?.processed_chips ?? 0);
  const errored = Number((activeJob?.metadata as any)?.error_chips ?? 0);
  if (!planned || planned <= 0) return null;
  const cols = 16;
  const rows = Math.min(12, Math.max(2, Math.ceil(planned / cols)));
  const total = rows * cols;
  const inferringWindow = Math.min(planned - processed, 8);
  type CellState = 'empty' | 'done' | 'inferring' | 'queued' | 'error';
  const cells: CellState[] = Array.from({ length: total }, (_, i) => {
    if (i >= planned) return 'empty';
    if (i < processed) return 'done';
    if (i < processed + inferringWindow) return 'inferring';
    return 'queued';
  });
  // Mark a deterministic spread of errors at the tail of processed range.
  for (let i = 0; i < errored && i < processed; i++) {
    const idx = processed - 1 - i * 3;
    if (idx >= 0) cells[idx] = 'error';
  }
  const pct = Math.round((processed / planned) * 100);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between font-mono text-[10px] text-slate-400">
        <span className="text-slate-500 uppercase tracking-wider">Chip pipeline</span>
        <span>
          {processed} / {planned} · {pct}%
        </span>
      </div>
      <div
        className="grid border border-slate-800 bg-slate-950 p-1 rounded"
        style={{ gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 2 }}
      >
        {cells.map((st, i) => {
          const bg =
            st === 'done' ? '#3dd68c'
            : st === 'inferring' ? '#4ea1ff'
            : st === 'error' ? '#ff3b30'
            : st === 'empty' ? 'transparent'
            : '#f5b400';
          const op = st === 'queued' ? 0.32 : 0.95;
          return (
            <div
              key={i}
              title={`chip ${i + 1} · ${st}`}
              style={{
                aspectRatio: '1/1',
                background: bg,
                opacity: op,
                animation: st === 'inferring' ? 'chip-pulse 1.1s ease-in-out infinite' : 'none',
              }}
            />
          );
        })}
      </div>
      <div className="flex items-center gap-3 font-mono text-[9.5px] text-slate-500">
        <Legend c="#3dd68c" label="done" />
        <Legend c="#4ea1ff" label="inferring" />
        <Legend c="#f5b400" label="queued" />
        <Legend c="#ff3b30" label="error" />
      </div>
      <style>{`@keyframes chip-pulse { 0%,100% { opacity: 0.55; } 50% { opacity: 1; } }`}</style>
    </div>
  );
}

function Legend({ c, label }: { c: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span style={{ width: 8, height: 8, background: c, display: 'inline-block', borderRadius: 1 }} />
      {label}
    </span>
  );
}

const API_BASE_URL = (import.meta as any).env?.VITE_API_URL || '';

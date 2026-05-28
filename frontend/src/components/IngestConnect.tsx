import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  ChevronDown,
  ChevronRight,
  DatabaseZap,
  FileImage,
  Film,
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
import { promptsForAllBranches, promptsForBranch } from '../utils/promptsForBranch';
import { BRANCH_ICON_BY_KEY, ObjectIcon } from '../utils/branchIcons';
import type { BranchIconKey } from '../utils/defenceOntology';
import { AlertTriangle, CircleHelp } from 'lucide-react';

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

type MediaType = 'imagery' | 'fmv';
/**
 * Vocabulary-scope mode for the imagery upload prompt set.
 *
 *  - `branch`      → derive `text_prompts` from one selected top-level
 *                    branch (default; ~15-25 prompts; precision win).
 *  - `cherry-pick` → operator hand-picks individual objects from the tree
 *                    (the legacy behaviour).
 *  - `all`         → flatten every branch into one prompt list (~131 prompts;
 *                    explicit opt-out, warned in the UI).
 */
type ScopeMode = 'branch' | 'cherry-pick' | 'all';

const FMV_FILE_ACCEPT = '.mp4,.mov,.ts,.mkv,.m4v';
const FMV_SIDECAR_ACCEPT = '.srt,.klv,.csv';
const IMAGERY_FILE_ACCEPT = '.tif,.tiff,.jp2,.j2k,.nc,.netcdf,.png,.jpg,.jpeg';

export default function IngestConnect() {
  const [mediaType, setMediaType] = useState<MediaType>('imagery');
  const [file, setFile] = useState<File | null>(null);
  const [sidecarFile, setSidecarFile] = useState<File | null>(null);
  const [sensorType, setSensorType] = useState('Optical');
  const [fmvModel, setFmvModel] = useState<'sam3' | 'yolo26'>('sam3');
  const [fmvPromptMode, setFmvPromptMode] = useState<'pcs' | 'amg'>('pcs');
  // Phase 8.41: opt-in synthetic Dubai telemetry. Default off — uploads
  // without real KLV/GPMD/SRT now fail with HTTP 422 unless this is ticked.
  const [fmvAllowSynthetic, setFmvAllowSynthetic] = useState(false);
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
  // Vocabulary-scope selector: `branch` is the default (LAE-80C aerial study
  // measured ~15x F1 gain going from open-vocab fan-out → branch-scoped). See
  // docs/decisions/why-branch-scoped-default.md.
  const [scopeMode, setScopeMode] = useState<ScopeMode>('branch');
  const [scopedBranchId, setScopedBranchId] = useState<string | null>(null);

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

  // Default the branch-scope selector to the first top-level branch once the
  // tree arrives, and re-anchor it if a sensor switch removes the previous
  // selection from the visible tree.
  useEffect(() => {
    if (!ontologyBranches.length) return;
    setScopedBranchId((current) => {
      if (current && ontologyBranches.some((b) => b.id === current)) return current;
      return ontologyBranches[0].id;
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
    || uploadJobs.find((job) => job.media_type === mediaType && isUploadActive(job))
    || uploadJobs.find((job) => job.media_type === mediaType)
    || null;

  const customPrompts = useMemo(() => parseCustomPrompts(customObjects), [customObjects]);
  // Cherry-picked prompts from the tree + the operator's custom textarea.
  // Sentinel "__prithvi_*__" / aux markers are dropped here — they live in
  // the JSON only to surface specialist-model outputs (Prithvi burn / flood
  // / crop) in the legend, not to be sent as text prompts to SAM 3.
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

  const scopedBranch = useMemo(
    () => ontologyBranches.find((b) => b.id === scopedBranchId) || null,
    [ontologyBranches, scopedBranchId],
  );

  // Prompts that will actually be sent to /api/ingest/upload, derived from
  // `scopeMode`. In `branch` mode, an optional cherry-picked subset *within*
  // that branch narrows the list further; if the operator has not touched
  // the tree, the full branch slice is used.
  const effectivePrompts = useMemo(() => {
    if (scopeMode === 'cherry-pick') return selectedPrompts;
    if (scopeMode === 'all') return promptsForAllBranches(ontologyBranches);
    // branch
    if (!scopedBranch) return [];
    const branchPrompts = promptsForBranch(scopedBranch, true);
    if (selectedDefenceIds.size === 0) return branchPrompts;
    // Operator narrowed the branch — intersect selections with the branch's
    // own object set.
    const branchObjIds = new Set(branchObjectIds(scopedBranch));
    const restricted = Array.from(selectedDefenceIds)
      .filter((id) => branchObjIds.has(id))
      .map((id) => defenceObjectById.get(id)?.prompt)
      .filter((value): value is string => Boolean(value))
      .filter(isSam3Prompt);
    if (restricted.length === 0) return branchPrompts;
    const seen = new Set<string>();
    return restricted.filter((p) => {
      const k = p.toLowerCase();
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }, [scopeMode, selectedPrompts, ontologyBranches, scopedBranch, selectedDefenceIds, defenceObjectById]);

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
      form.append('auto_process', 'true');
      let endpoint = `${API_URL}/api/ingest/upload`;
      if (mediaType === 'imagery') {
        form.append('sensor_type', sensorType);
        form.append('modality', sensorPipeline.modality);
        form.append('enabled_layers', JSON.stringify(sensorPipeline.enabledLayers));
        // Branch-scoped is the default; record the mission branch even when
        // we also send text_prompts so the worker has provenance and the
        // backend can audit scope use. The backend ignores ontology_branch
        // when explicit text_prompts win (per inference resolve_prompts).
        if (scopeMode === 'branch' && scopedBranchId) {
          form.append('ontology_branch', scopedBranchId);
        }
        if (effectivePrompts.length > 0) {
          form.append('text_prompts', JSON.stringify(effectivePrompts));
        }
      } else {
        // FMV: route through /api/fmv/clips so the sidecar (KLV/SRT) is
        // attached and the clip record is created with HLS transcode +
        // SAM3-video tracking queued in one shot.
        endpoint = `${API_URL}/api/fmv/clips`;
        form.append('model', fmvModel);
        form.append('prompt_mode', fmvPromptMode);
        if (sidecarFile) {
          form.append('srt', sidecarFile);
        }
        if (fmvAllowSynthetic) {
          form.append('allow_synthetic_telemetry', 'true');
        }
      }
      const response = await axios.post(endpoint, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (event) => {
          if (event.total) {
            setUploadTransferProgress(Math.round((event.loaded / event.total) * 100));
          }
        },
      });
      const uploadId = response.data.upload_id
        || response.data.clip?.metadata?.upload_id
        || (response.data.id != null ? String(response.data.id) : null);
      setActiveUploadId(uploadId);
      setUploadStatus(
        mediaType === 'fmv'
          ? `Queued FMV clip ${response.data.name || response.data.clip?.name || response.data.id || ''}`.trim()
          : `Queued ${response.data.task_id || response.data.filename}`,
      );
      await fetchUploadJobs();
      setFile(null);
      setSidecarFile(null);
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
              <div className="ingest-object-grid grid grid-cols-1 gap-2">
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
    <div className="ingest-connect w-full h-full min-h-0 bg-slate-950 text-slate-200 overflow-auto">
      <div className="max-w-4xl mx-auto p-6 flex flex-col gap-5">
        <div className="flex items-center gap-3 border-b border-slate-800 pb-4">
          <UploadCloud className="w-7 h-7 text-blue-400" />
          <h2 className="text-xl font-bold uppercase tracking-wider">
            {mediaType === 'fmv' ? 'FMV Upload' : 'Imagery Upload'}
          </h2>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Media type</span>
          <div className="inline-flex border border-slate-700 rounded overflow-hidden self-start">
            {([
              { key: 'imagery', label: 'Imagery', icon: FileImage },
              { key: 'fmv',     label: 'FMV (Video)', icon: Film },
            ] as const).map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                type="button"
                onClick={() => {
                  if (mediaType === key) return;
                  setMediaType(key);
                  setFile(null);
                  setSidecarFile(null);
                  setUploadStatus('');
                }}
                className={`flex items-center gap-2 px-4 py-2 text-xs font-bold uppercase tracking-wider ${
                  mediaType === key
                    ? 'bg-blue-500/20 text-blue-200'
                    : 'bg-slate-900 text-slate-400 hover:text-slate-200'
                }`}
              >
                <Icon className="w-3.5 h-3.5" /> {label}
              </button>
            ))}
          </div>
        </div>

        {incompatibleNotice && (
          <div className="border border-amber-500/40 bg-amber-500/10 text-amber-200 text-xs px-3 py-2 rounded">
            {incompatibleNotice}
          </div>
        )}

        <label className="min-h-56 border border-dashed border-slate-700 bg-slate-900/40 hover:bg-slate-900 transition rounded flex flex-col items-center justify-center cursor-pointer">
          {mediaType === 'fmv' ? (
            <Film className="w-12 h-12 text-slate-500 mb-3" />
          ) : (
            <FileImage className="w-12 h-12 text-slate-500 mb-3" />
          )}
          <div className="font-mono text-sm text-slate-300">
            {file ? file.name : mediaType === 'fmv' ? 'Select FMV clip' : 'Select raster'}
          </div>
          <div className="font-mono text-xs text-slate-500 mt-2">
            {mediaType === 'fmv' ? 'MP4 / MOV / TS / MKV / M4V' : 'TIF / JP2 / NetCDF / PNG / JPG'}
          </div>
          <input
            type="file"
            accept={mediaType === 'fmv' ? FMV_FILE_ACCEPT : IMAGERY_FILE_ACCEPT}
            onChange={(event) => setFile(event.target.files?.[0] || null)}
            className="hidden"
          />
        </label>

        {mediaType === 'fmv' && (
          <label className="border border-dashed border-slate-800 bg-slate-900/30 hover:bg-slate-900 transition rounded px-4 py-3 cursor-pointer flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                Telemetry sidecar (optional)
              </div>
              <div className="font-mono text-xs text-slate-300 truncate">
                {sidecarFile ? sidecarFile.name : 'Attach .srt / .klv / .csv (MISB-0601 KLV)'}
              </div>
            </div>
            {sidecarFile && (
              <button
                type="button"
                onClick={(event) => {
                  event.preventDefault();
                  setSidecarFile(null);
                }}
                className="border border-slate-700 px-2 py-1 rounded text-slate-300 hover:border-rose-400"
              >
                <X className="w-3 h-3" />
              </button>
            )}
            <input
              type="file"
              accept={FMV_SIDECAR_ACCEPT}
              onChange={(event) => setSidecarFile(event.target.files?.[0] || null)}
              className="hidden"
            />
          </label>
        )}

        {mediaType === 'fmv' && (
          <div className="ingest-two-col grid grid-cols-1 gap-3">
            <div>
              <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1.5">Model</div>
              <select
                value={fmvModel}
                onChange={(event) => {
                  const next = event.target.value as 'sam3' | 'yolo26';
                  setFmvModel(next);
                  // SAM 3.1 only supports PCS; AMG is YOLO 26 only.
                  if (next === 'sam3' && fmvPromptMode === 'amg') setFmvPromptMode('pcs');
                }}
                disabled={uploading}
                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm"
                title="Inference engine. SAM 3.1 uses text-prompted multiplex tracking; YOLO 26 runs YOLOE-26x-seg(-pf) per-frame and supports promptless AMG."
              >
                <option value="sam3">SAM 3.1 (default)</option>
                <option value="yolo26">YOLO 26</option>
              </select>
            </div>
            <div>
              <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1.5">Detection mode</div>
              <div className="inline-flex border border-slate-700 rounded overflow-hidden w-full">
                {fmvModel === 'yolo26' && (
                  <button
                    type="button"
                    onClick={() => setFmvPromptMode('amg')}
                    disabled={uploading}
                    title="Automatic Mask Generation — YOLOE-26x-seg-pf promptless closed-set detection"
                    className={`flex-1 px-3 py-2 text-xs font-bold uppercase tracking-wider ${
                      fmvPromptMode === 'amg'
                        ? 'bg-blue-500/20 text-blue-200'
                        : 'bg-slate-900 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    AMG
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setFmvPromptMode('pcs')}
                  disabled={uploading}
                  title="Promptable Concept Segmentation — track named classes from the admin ontology"
                  className={`flex-1 px-3 py-2 text-xs font-bold uppercase tracking-wider ${
                    fmvPromptMode === 'pcs'
                      ? 'bg-blue-500/20 text-blue-200'
                      : 'bg-slate-900 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  PCS
                </button>
              </div>
            </div>
            {/* Phase 8.41: opt-in synthetic Dubai telemetry for clips without
                KLV / GPMD / SRT. Default OFF so failures fail loud (HTTP 422)
                instead of silently shipping garbage georeference. */}
            <label
              className="flex items-center gap-2 col-span-full text-xs text-slate-300"
              title="When ticked, clips missing real KLV/GPMD/SRT telemetry fall back to the synthetic Dubai sine-wave fixture for offline demos. Leave OFF in production — the upload will fail with a clear error instead."
            >
              <input
                type="checkbox"
                checked={fmvAllowSynthetic}
                onChange={(event) => setFmvAllowSynthetic(event.target.checked)}
                disabled={uploading}
                className="accent-amber-500"
              />
              <span>
                Demo mode: allow synthetic Dubai telemetry if no real KLV/GPMD/SRT present
              </span>
            </label>
          </div>
        )}

        <div className="flex flex-col gap-2">
          <div className="flex flex-col sm:flex-row gap-3">
            {mediaType === 'imagery' ? (
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
            ) : (
              <div className="bg-slate-900 border border-slate-700 rounded px-3 py-3 text-sm sm:w-56 text-slate-300 flex items-center gap-2">
                <Film className="w-4 h-4 text-blue-300" />
                <span>FMV pipeline</span>
              </div>
            )}
            <button
              onClick={uploadImage}
              disabled={!file || uploading || (mediaType === 'imagery' && ontologyBranches.length === 0)}
              title={mediaType === 'imagery' && ontologyBranches.length === 0 ? 'Loading ontology…' : undefined}
              className="flex-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed rounded px-4 py-3 text-sm font-bold uppercase tracking-wider flex items-center justify-center gap-2"
              data-testid="ingest-upload-button"
            >
              <DatabaseZap className="w-4 h-4" /> Upload &amp; Process
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5 items-center font-mono text-[10px] text-slate-400">
            <span className="text-slate-500 uppercase tracking-wider">Models:</span>
            {(mediaType === 'fmv'
              ? (fmvModel === 'sam3' ? ['SAM 3.1 (video)'] : ['YOLOE-26x'])
              : sensorPipeline.models
            ).map((model) => (
              <span
                key={model}
                className="border border-blue-500/40 bg-blue-500/10 text-blue-300 px-2 py-0.5 rounded"
              >
                {model}
              </span>
            ))}
            {mediaType === 'fmv' && (
              <span className="border border-slate-600 bg-slate-800/40 text-slate-300 px-2 py-0.5 rounded uppercase">
                {fmvPromptMode}
              </span>
            )}
            {mediaType === 'imagery' && (
              <span
                data-testid="scope-status-chip"
                title="Active vocabulary scope — branch-scoped is the precision default (~15-25 prompts vs ~131 unscoped)"
                className={`border px-2 py-0.5 rounded uppercase tracking-wider ${
                  scopeMode === 'all'
                    ? 'border-amber-500/50 bg-amber-500/10 text-amber-200'
                    : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
                }`}
              >
                {scopeMode === 'branch' && (
                  <>[Branch: {scopedBranch?.label || '—'}] {effectivePrompts.length} prompts</>
                )}
                {scopeMode === 'cherry-pick' && (
                  <>[Cherry-pick] {effectivePrompts.length} prompts</>
                )}
                {scopeMode === 'all' && (
                  <>[All branches] {effectivePrompts.length} prompts ⚠</>
                )}
              </span>
            )}
          </div>
          {/* VRAM accounting bar from /api/inference/dashboard */}
          <VramBar />

          {/* Chip pipeline visualiser — live status of upload-job chips */}
          <ChipPipelineGrid activeJob={activeJob || undefined} />

          {mediaType === 'imagery' && sensorPipeline.warning && (
            <div className="flex items-start gap-2 border border-yellow-500/40 bg-yellow-500/5 text-yellow-200 text-xs px-3 py-2 rounded">
              <span className="font-bold uppercase tracking-wider text-[10px]">Note:</span>
              <span>{sensorPipeline.warning}</span>
            </div>
          )}
          {mediaType === 'fmv' && (
            <div className="flex items-start gap-2 border border-blue-500/40 bg-blue-500/5 text-blue-200 text-xs px-3 py-2 rounded">
              <span className="font-bold uppercase tracking-wider text-[10px]">FMV:</span>
              <span>
                {fmvModel === 'sam3'
                  ? 'SAM 3.1 video tracking with PCS — concepts come from the ontology defaults.'
                  : fmvPromptMode === 'amg'
                    ? 'YOLO 26 promptless detection (AMG) — runs YOLOE-26x-seg-pf per frame, no prompts needed.'
                    : 'YOLO 26 prompted detection (PCS) — concepts come from the ontology defaults.'}
              </span>
            </div>
          )}
        </div>

        {mediaType === 'imagery' && (
          <ScopeModeSelector
            mode={scopeMode}
            setMode={setScopeMode}
            branches={ontologyBranches}
            scopedBranchId={scopedBranchId}
            setScopedBranchId={setScopedBranchId}
            promptCount={effectivePrompts.length}
          />
        )}

        {mediaType === 'imagery' && (
        <div className="border border-slate-800 bg-slate-900/70 rounded">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
            <div className="flex items-center gap-3">
              <ShieldCheck className="w-5 h-5 text-blue-400" />
              <div>
                <div className="text-xs font-bold uppercase tracking-wider text-slate-200">
                  {scopeMode === 'branch'
                    ? `Refine ${scopedBranch?.label || 'branch'} (optional)`
                    : 'Detection Objects'}
                </div>
                <div className="font-mono text-[10px] text-slate-500">
                  {scopeMode === 'branch'
                    ? <>Branch slice already sent &middot; tick a subset to narrow further</>
                    : <>Pick what SAM3 should look for &middot; filtered for <span className="text-blue-300">{sensorTag.toUpperCase()}</span></>}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2 font-mono text-[10px]">
              <span className="border border-blue-500/40 bg-blue-500/10 text-blue-300 px-2 py-1 rounded">
                {effectivePrompts.length} prompts
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
            <div className="ingest-object-tree space-y-2 pr-1">
              {(scopeMode === 'branch' && scopedBranch
                ? [scopedBranch]
                : ontologyBranches
              ).map((branch) => renderBranch(branch))}
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
              {scopeMode === 'branch' && (
                <>Branch-scoped: {effectivePrompts.length} prompts from <span className="text-blue-300">{scopedBranch?.label || '—'}</span> will be sent.</>
              )}
              {scopeMode === 'cherry-pick' && (
                effectivePrompts.length
                  ? `${effectivePrompts.length} cherry-picked prompts will override the default profile.`
                  : 'No object override selected. Upload will use the default satellite prompt profile.'
              )}
              {scopeMode === 'all' && (
                <>Full vocabulary fan-out: {effectivePrompts.length} prompts will be sent.</>
              )}
            </div>
          </div>
        </div>
        )}

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
/*  Vocabulary-scope selector                                              */
/* ---------------------------------------------------------------------- */

interface ScopeModeSelectorProps {
  mode: ScopeMode;
  setMode: (m: ScopeMode) => void;
  branches: OntologyBranch[];
  scopedBranchId: string | null;
  setScopedBranchId: (id: string) => void;
  promptCount: number;
}

function ScopeModeSelector({
  mode,
  setMode,
  branches,
  scopedBranchId,
  setScopedBranchId,
  promptCount,
}: ScopeModeSelectorProps) {
  const branchCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const b of branches) m.set(b.id, promptsForBranch(b, true).length);
    return m;
  }, [branches]);

  const MODE_BUTTONS: { key: ScopeMode; label: string; title: string }[] = [
    { key: 'branch',      label: 'Mission branch',     title: 'Scope detection to one mission branch. ~15-25 prompts. Default. LAE-80C aerial study measured ~15x F1 gain over open-vocab fan-out.' },
    { key: 'cherry-pick', label: 'Cherry-pick objects', title: 'Hand-pick individual objects across the ontology. Legacy behaviour.' },
    { key: 'all',         label: 'All branches',       title: 'Send the full vocabulary (~131 prompts). High false-positive rate; exploratory passes only.' },
  ];

  return (
    <div
      className="border border-slate-800 bg-slate-900/70 rounded"
      data-testid="scope-mode-selector"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
        <div className="min-w-0">
          <div className="text-xs font-bold uppercase tracking-wider text-slate-200">Vocabulary scope</div>
          <div className="font-mono text-[10px] text-slate-500">
            Branch-scoped is the precision default &middot; <span className="text-emerald-300">{promptCount} prompts</span>
          </div>
        </div>
        <div className="inline-flex border border-slate-700 rounded overflow-hidden">
          {MODE_BUTTONS.map(({ key, label, title }) => (
            <button
              key={key}
              type="button"
              title={title}
              data-testid={`scope-mode-${key}`}
              onClick={() => setMode(key)}
              className={`px-3 py-1.5 text-[11px] font-bold uppercase tracking-wider ${
                mode === key
                  ? 'bg-blue-500/20 text-blue-200'
                  : 'bg-slate-900 text-slate-400 hover:text-slate-200'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="px-4 py-3">
        {mode === 'branch' && (
          branches.length === 0 ? (
            <div className="font-mono text-[11px] text-slate-500">Loading ontology…</div>
          ) : (
            <label className="flex flex-col gap-1.5">
              <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Mission branch</span>
              <select
                data-testid="scope-branch-select"
                value={scopedBranchId || ''}
                onChange={(e) => setScopedBranchId(e.target.value)}
                className="bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm"
              >
                {branches.map((b) => {
                  const count = branchCounts.get(b.id) ?? 0;
                  return (
                    <option key={b.id} value={b.id}>
                      {b.label} ({count} prompt{count === 1 ? '' : 's'})
                    </option>
                  );
                })}
              </select>
            </label>
          )
        )}
        {mode === 'cherry-pick' && (
          <div className="font-mono text-[11px] text-slate-400">
            Pick individual objects in the tree below. The selection becomes the prompt set.
          </div>
        )}
        {mode === 'all' && (
          <div
            data-testid="scope-all-warning"
            className="flex items-start gap-2 border border-amber-500/40 bg-amber-500/10 text-amber-200 text-xs px-3 py-2 rounded"
          >
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            <span>
              Full ontology fan-out (~{promptCount} prompts). Higher false-positive rate per
              LAE-80C — use only for exploratory passes.
            </span>
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

import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Cable, CheckCircle2, DatabaseZap, FileImage, ListChecks, RadioTower, Search, ShieldCheck, UploadCloud, X } from 'lucide-react';
import { useEventStream } from '../hooks/useEventStream';
import { type UploadJob, isUploadActive, uploadMessage, uploadProgress, uploadProgressClass, uploadStage } from '../utils/uploadProgress';
import { DEFENCE_OBJECTS, DEFENCE_ONTOLOGY, type DefenceBranch, parseCustomPrompts } from '../utils/defenceOntology';

const API_URL = import.meta.env.VITE_API_URL || '';
const MAX_IMAGE_PROMPTS = 128;

interface FeedSource {
  id: number;
  name: string;
  feed_type: string;
  protocol: string;
  endpoint: string;
  topic: string;
  parser?: string;
  enabled: boolean;
  status: string;
  last_error?: string;
  created_at: string;
  updated_at: string;
}

const defenceObjectById = new Map(DEFENCE_OBJECTS.map((item) => [item.id, item]));

function branchObjectIds(branch: DefenceBranch): string[] {
  return [
    ...(branch.objects || []).map((item) => item.id),
    ...(branch.children || []).flatMap(branchObjectIds),
  ];
}

export default function IngestConnect() {
  const [file, setFile] = useState<File | null>(null);
  const [sensorType, setSensorType] = useState('Optical');
  const [autoProcess, setAutoProcess] = useState(true);
  const [selectedDefenceIds, setSelectedDefenceIds] = useState<Set<string>>(new Set());
  const [objectSearch, setObjectSearch] = useState('');
  const [customObjects, setCustomObjects] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState('');
  const [uploadTransferProgress, setUploadTransferProgress] = useState(0);
  const [activeUploadId, setActiveUploadId] = useState<string | null>(null);
  const [uploadJobs, setUploadJobs] = useState<UploadJob[]>([]);
  const [feeds, setFeeds] = useState<FeedSource[]>([]);
  const [feedForm, setFeedForm] = useState({
    name: 'AIS Gulf Feed',
    feed_type: 'AIS',
    protocol: 'tcp',
    endpoint: 'tcp://localhost:4002',
    topic: 'feeds',
    parser: 'nmea'
  });
  const [feedStatus, setFeedStatus] = useState('');

  const fetchFeeds = async () => {
    try {
      const response = await axios.get(`${API_URL}/api/feeds`);
      setFeeds(response.data.feeds || []);
    } catch (error) {
      console.error('Error fetching feeds:', error);
    }
  };

  const fetchUploadJobs = useCallback(async () => {
    try {
      const response = await axios.get(`${API_URL}/api/ingest/uploads`);
      setUploadJobs(response.data.uploads || []);
    } catch (error) {
      console.error('Error fetching uploads:', error);
    }
  }, []);

  useEffect(() => {
    fetchFeeds();
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
  const selectedPrompts = useMemo(() => {
    const seen = new Set<string>();
    const prompts = [
      ...Array.from(selectedDefenceIds)
        .map((id) => defenceObjectById.get(id)?.prompt)
        .filter((value): value is string => Boolean(value)),
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
  const visibleDefenceIds = useMemo(() => {
    if (!searchTerm) return new Set(DEFENCE_OBJECTS.map((item) => item.id));
    return new Set(
      DEFENCE_OBJECTS
        .filter((item) => `${item.label} ${item.prompt}`.toLowerCase().includes(searchTerm))
        .map((item) => item.id)
    );
  }, [searchTerm]);

  const toggleDefenceObject = (id: string) => {
    setSelectedDefenceIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleBranch = (branch: DefenceBranch) => {
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
      form.append('auto_process', String(autoProcess));
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
      setUploadStatus(autoProcess ? `Queued ${response.data.task_id}` : `Stored ${response.data.filename}`);
      await fetchUploadJobs();
      setFile(null);
    } catch (error: any) {
      setUploadStatus(error.response?.data?.detail || 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const connectFeed = async () => {
    try {
      const response = await axios.post(`${API_URL}/api/feeds/connect`, {
        ...feedForm,
        enabled: true
      });
      setFeedStatus(`Connected ${response.data.feed.name}`);
      fetchFeeds();
    } catch (error: any) {
      setFeedStatus(error.response?.data?.detail || 'Connection failed');
    }
  };

  const renderObject = (item: { id: string; label: string; prompt: string }) => {
    if (!visibleDefenceIds.has(item.id)) return null;
    const selected = selectedDefenceIds.has(item.id);
    return (
      <button
        key={item.id}
        type="button"
        onClick={() => toggleDefenceObject(item.id)}
        title={item.prompt}
        className={`min-h-10 border px-2 py-2 text-left transition ${
          selected
            ? 'border-blue-400/70 bg-blue-500/15 text-blue-100'
            : 'border-slate-800 bg-slate-950/40 text-slate-300 hover:border-slate-600'
        }`}
      >
        <span className="flex items-start gap-2">
          <input type="checkbox" checked={selected} readOnly className="mt-0.5" />
          <span className="min-w-0">
            <span className="block text-xs font-semibold leading-4">{item.label}</span>
            <span className="block truncate font-mono text-[10px] text-slate-500">{item.prompt}</span>
          </span>
        </span>
      </button>
    );
  };

  const renderBranch = (branch: DefenceBranch, depth = 0) => {
    const ids = branchObjectIds(branch).filter((id) => visibleDefenceIds.has(id));
    if (!ids.length) return null;
    const selectedCount = ids.filter((id) => selectedDefenceIds.has(id)).length;
    return (
      <div key={branch.id} className={depth === 0 ? 'border border-slate-800 bg-slate-950/30' : 'border-l border-slate-800 pl-3'}>
        <button
          type="button"
          onClick={() => toggleBranch(branch)}
          className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-slate-800/40"
        >
          <span className="min-w-0">
            <span className="block text-xs font-bold uppercase tracking-wider text-slate-200">{branch.label}</span>
            <span className="block font-mono text-[10px] text-slate-500">{selectedCount}/{ids.length} selected</span>
          </span>
          <span className={`border px-2 py-1 font-mono text-[10px] ${selectedCount ? 'border-blue-400/50 text-blue-300' : 'border-slate-700 text-slate-500'}`}>
            {selectedCount === ids.length ? 'ALL' : selectedCount ? 'PART' : 'ADD'}
          </span>
        </button>
        <div className="space-y-2 px-3 pb-3">
          {branch.children?.map((child) => renderBranch(child, depth + 1))}
          {branch.objects && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {branch.objects.map(renderObject)}
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="w-full h-full bg-slate-950 text-slate-200 overflow-auto">
      <div className="h-full grid grid-cols-1 xl:grid-cols-[1fr_1fr]">
        <section className="border-r border-slate-800 p-6 flex flex-col gap-5">
          <div className="flex items-center justify-between border-b border-slate-800 pb-4">
            <div>
              <h2 className="text-xl font-bold uppercase tracking-wider flex items-center gap-3">
                <UploadCloud className="w-6 h-6 text-blue-400" /> Imagery Upload
              </h2>
              <div className="text-xs text-slate-500 font-mono mt-1">IMINT / GEOINT COLLECTION</div>
            </div>
            <div className="text-xs font-mono text-blue-300 border border-blue-500/40 px-3 py-1 rounded bg-blue-500/10">
              {uploading ? 'TRANSFERRING' : 'READY'}
            </div>
          </div>

          <label className="min-h-64 border border-dashed border-slate-700 bg-slate-900/40 hover:bg-slate-900 transition rounded flex flex-col items-center justify-center cursor-pointer">
            <FileImage className="w-12 h-12 text-slate-500 mb-4" />
            <div className="font-mono text-sm text-slate-300">{file ? file.name : 'Select raster collection'}</div>
            <div className="font-mono text-xs text-slate-500 mt-2">TIF / JP2 / NetCDF / PNG / JPG</div>
            <input
              type="file"
              accept=".tif,.tiff,.jp2,.j2k,.nc,.netcdf,.png,.jpg,.jpeg"
              onChange={(event) => setFile(event.target.files?.[0] || null)}
              className="hidden"
            />
          </label>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <select
              value={sensorType}
              onChange={(event) => setSensorType(event.target.value)}
              className="bg-slate-900 border border-slate-700 rounded px-3 py-3 text-sm"
            >
              <option>Optical</option>
              <option>Radar</option>
              <option>Thermal</option>
              <option>MASINT</option>
              <option>FMV</option>
            </select>
            <label className="bg-slate-900 border border-slate-700 rounded px-3 py-3 text-sm flex items-center gap-3">
              <input
                type="checkbox"
                checked={autoProcess}
                onChange={(event) => setAutoProcess(event.target.checked)}
              />
              Auto process
            </label>
             <button
               onClick={uploadImage}
               disabled={!file || uploading}
               className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed rounded px-4 py-3 text-sm font-bold uppercase tracking-wider flex items-center justify-center gap-2"
             >
              <DatabaseZap className="w-4 h-4" /> Upload
            </button>
          </div>

          <div className="border border-slate-800 bg-slate-900/70">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
              <div className="flex items-center gap-3">
                <ShieldCheck className="w-5 h-5 text-blue-400" />
                <div>
                  <div className="text-xs font-bold uppercase tracking-wider text-slate-200">Detection Objects</div>
                  <div className="font-mono text-[10px] text-slate-500">Defence ontology + custom SAM3 prompts</div>
                </div>
              </div>
              <div className="flex items-center gap-2 font-mono text-[10px]">
                <span className={`border px-2 py-1 ${selectedPrompts.length > MAX_IMAGE_PROMPTS ? 'border-amber-500/50 bg-amber-500/10 text-amber-300' : 'border-blue-500/40 bg-blue-500/10 text-blue-300'}`}>
                  {selectedPrompts.length} prompts
                </span>
                <button type="button" onClick={selectVisibleDefence} className="border border-slate-700 px-2 py-1 text-slate-300 hover:border-blue-400">
                  <ListChecks className="inline h-3 w-3 mr-1" /> visible
                </button>
                <button type="button" onClick={clearAllPrompts} className="border border-slate-700 px-2 py-1 text-slate-300 hover:border-red-400">
                  <X className="inline h-3 w-3 mr-1" /> clear
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 2xl:grid-cols-[minmax(0,1fr)_300px] gap-4 p-4">
              <div className="min-w-0">
                <div className="mb-3 flex items-center gap-2 border border-slate-800 bg-slate-950 px-3 py-2">
                  <Search className="h-4 w-4 text-slate-500" />
                  <input
                    value={objectSearch}
                    onChange={(event) => setObjectSearch(event.target.value)}
                    placeholder="Search defence objects"
                    className="min-w-0 flex-1 bg-transparent text-sm text-slate-200 outline-none placeholder:text-slate-600"
                  />
                </div>
                <div className="max-h-80 overflow-auto space-y-2 pr-1">
                  {DEFENCE_ONTOLOGY.map((branch) => renderBranch(branch))}
                </div>
              </div>

              <div className="min-w-0 space-y-3">
                <label className="block">
                  <span className="mb-2 block text-xs font-bold uppercase tracking-wider text-slate-300">Custom Objects</span>
                  <textarea
                    value={customObjects}
                    onChange={(event) => setCustomObjects(event.target.value)}
                    placeholder="one object per line, or comma separated"
                    className="h-40 w-full resize-none border border-slate-800 bg-slate-950 px-3 py-2 font-mono text-xs text-slate-200 outline-none placeholder:text-slate-600 focus:border-blue-500/70"
                  />
                </label>
                <div className="border border-slate-800 bg-slate-950/60 px-3 py-2 font-mono text-[10px] text-slate-500">
                  {selectedPrompts.length > MAX_IMAGE_PROMPTS
                    ? `Only the first ${MAX_IMAGE_PROMPTS} prompts will be used by the image inference service.`
                    : selectedPrompts.length
                    ? `Upload will override the default profile with ${selectedPrompts.length} selected text prompts.`
                    : 'No object override selected. Upload will use the default satellite prompt profile.'}
                </div>
              </div>
            </div>
          </div>

          {uploadStatus && (
            <div className="border border-slate-800 bg-slate-900 rounded px-4 py-3 text-sm font-mono text-blue-300">
              {uploadStatus}
            </div>
          )}

          {(uploading || activeJob) && (
            <div className="border border-slate-800 bg-slate-900/80 rounded px-4 py-3 text-sm">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-xs uppercase tracking-wider text-slate-400">
                    {uploading ? 'upload transfer' : uploadStage(activeJob)}
                  </div>
                  <div className="mt-1 font-mono text-xs text-slate-300 truncate">
                    {uploading ? `${uploadTransferProgress}% uploaded` : uploadMessage(activeJob)}
                  </div>
                </div>
                <div className="font-mono text-xs text-slate-400">
                  {uploading ? uploadTransferProgress : uploadProgress(activeJob)}%
                </div>
              </div>
              <div className="mt-3 h-2 w-full bg-slate-800 overflow-hidden rounded">
                <div
                  className={`h-full transition-all duration-500 ${uploading ? 'bg-sky-400' : uploadProgressClass(activeJob)}`}
                  style={{ width: `${uploading ? uploadTransferProgress : uploadProgress(activeJob)}%` }}
                />
              </div>
              {activeJob?.celery_task_id && (
                <div className="mt-2 text-[10px] font-mono text-slate-500 truncate">task {activeJob.celery_task_id}</div>
              )}
            </div>
          )}
        </section>

        <section className="p-6 flex flex-col gap-5">
          <div className="flex items-center justify-between border-b border-slate-800 pb-4">
            <div>
              <h2 className="text-xl font-bold uppercase tracking-wider flex items-center gap-3">
                <RadioTower className="w-6 h-6 text-emerald-400" /> Streaming Inputs
              </h2>
              <div className="text-xs text-slate-500 font-mono mt-1">AIS / ADS-B / RF / VIDEO / WEBHOOKS</div>
            </div>
            <div className="text-xs font-mono text-emerald-300 border border-emerald-500/40 px-3 py-1 rounded bg-emerald-500/10">
              {feeds.filter(feed => feed.enabled).length} ACTIVE
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <input
              value={feedForm.name}
              onChange={(event) => setFeedForm(prev => ({ ...prev, name: event.target.value }))}
              className="bg-slate-900 border border-slate-700 rounded px-3 py-3 text-sm"
            />
            <select
              value={feedForm.feed_type}
              onChange={(event) => setFeedForm(prev => ({ ...prev, feed_type: event.target.value }))}
              className="bg-slate-900 border border-slate-700 rounded px-3 py-3 text-sm"
            >
              <option>AIS</option>
              <option>ADS-B</option>
              <option>RF/SIGINT</option>
              <option>FMV</option>
              <option>OSINT</option>
              <option>Webhook</option>
            </select>
            <select
              value={feedForm.protocol}
              onChange={(event) => setFeedForm(prev => ({ ...prev, protocol: event.target.value }))}
              className="bg-slate-900 border border-slate-700 rounded px-3 py-3 text-sm"
            >
              <option value="tcp">TCP</option>
              <option value="udp">UDP</option>
              <option value="http">HTTP</option>
              <option value="https">HTTPS</option>
              <option value="websocket">WebSocket</option>
              <option value="file">File</option>
              <option value="serial">Serial</option>
            </select>
            <select
              value={feedForm.parser}
              onChange={(event) => setFeedForm(prev => ({ ...prev, parser: event.target.value }))}
              className="bg-slate-900 border border-slate-700 rounded px-3 py-3 text-sm"
            >
              <option value="nmea">NMEA</option>
              <option value="json">JSON</option>
              <option value="csv">CSV</option>
              <option value="klv">MISB KLV</option>
              <option value="raw">Raw</option>
            </select>
            <input
              value={feedForm.endpoint}
              onChange={(event) => setFeedForm(prev => ({ ...prev, endpoint: event.target.value }))}
              className="md:col-span-2 bg-slate-900 border border-slate-700 rounded px-3 py-3 text-sm font-mono"
            />
            <button
              onClick={connectFeed}
              className="md:col-span-2 bg-emerald-600 hover:bg-emerald-500 rounded px-4 py-3 text-sm font-bold uppercase tracking-wider flex items-center justify-center gap-2"
            >
              <Cable className="w-4 h-4" /> Connect Source
            </button>
          </div>

          {feedStatus && (
            <div className="border border-slate-800 bg-slate-900 rounded px-4 py-3 text-sm font-mono text-emerald-300">
              {feedStatus}
            </div>
          )}

          <div className="flex-1 overflow-auto border border-slate-800 rounded bg-slate-900/40">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-500 bg-slate-900 sticky top-0">
                <tr>
                  <th className="px-4 py-3">Source</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Endpoint</th>
                  <th className="px-4 py-3">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {feeds.map(feed => (
                  <tr key={feed.id} className="hover:bg-slate-800/40">
                    <td className="px-4 py-3 font-semibold text-slate-200">{feed.name}</td>
                    <td className="px-4 py-3 text-slate-400">{feed.feed_type}</td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-500">{feed.endpoint}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-xs font-mono ${
                        feed.enabled ? 'border-emerald-500/50 text-emerald-300 bg-emerald-500/10' : 'border-slate-700 text-slate-400 bg-slate-800'
                      }`}>
                        <CheckCircle2 className="w-3 h-3" /> {feed.status}
                      </span>
                    </td>
                  </tr>
                ))}
                {feeds.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-10 text-center text-slate-500 font-mono">NO SOURCES</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

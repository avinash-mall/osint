import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { Cable, CheckCircle2, DatabaseZap, FileImage, RadioTower, UploadCloud } from 'lucide-react';
import { useEventStream } from '../hooks/useEventStream';
import { type UploadJob, isUploadActive, uploadMessage, uploadProgress, uploadProgressClass, uploadStage } from '../utils/uploadProgress';

const API_URL = import.meta.env.VITE_API_URL || '';

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

export default function IngestConnect() {
  const [file, setFile] = useState<File | null>(null);
  const [sensorType, setSensorType] = useState('Optical');
  const [autoProcess, setAutoProcess] = useState(true);
  const [useYolo, setUseYolo] = useState(false);
  const [useLaeDino, setUseLaeDino] = useState(false);
  const [useMmrotate, setUseMmrotate] = useState(false);
  const [useLsknet, setUseLsknet] = useState(false);
  const [useSam2, setUseSam2] = useState(false);
  const [useSam3, setUseSam3] = useState(true);
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
    const providers = [useYolo && 'yolo', useLaeDino && 'lae-dino', useMmrotate && 'mmrotate', useLsknet && 'lsknet', useSam2 && 'sam2', useSam3 && 'sam3'].filter(Boolean) as string[];
    if (providers.length === 0) {
      setUploadStatus('Select at least one inference provider.');
      return;
    }
    setUploading(true);
    setUploadTransferProgress(0);
    setUploadStatus('');
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('sensor_type', sensorType);
      form.append('auto_process', String(autoProcess));
      form.append('inference_providers', providers.join(','));
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

          <div className="border border-slate-700 bg-slate-900 rounded px-4 py-3 flex flex-col gap-2">
            <div className="text-[10px] uppercase tracking-wider text-slate-500 font-mono">
              Inference Providers
            </div>
            <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={useYolo}
                  onChange={(event) => setUseYolo(event.target.checked)}
                />
                <span className="text-slate-200">YOLOv8 OBB</span>
                <span className="text-[10px] text-slate-500 font-mono">geoint-yolov8-obb</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={useLaeDino}
                  onChange={(event) => setUseLaeDino(event.target.checked)}
                />
                <span className="text-slate-200">LAE-DINO</span>
                <span className="text-[10px] text-slate-500 font-mono">open-vocab Swin-T</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={useMmrotate}
                  onChange={(event) => setUseMmrotate(event.target.checked)}
                />
                <span className="text-slate-200">MMRotate</span>
                <span className="text-[10px] text-slate-500 font-mono">DOTA Oriented R-CNN</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={useLsknet}
                  onChange={(event) => setUseLsknet(event.target.checked)}
                />
                <span className="text-slate-200">LSKNet</span>
                <span className="text-[10px] text-slate-500 font-mono">Large Selective Kernel</span>
              </label>
               <label className="flex items-center gap-2 cursor-pointer">
                 <input
                   type="checkbox"
                   checked={useSam2}
                   onChange={(event) => setUseSam2(event.target.checked)}
                 />
                 <span className="text-slate-200">SAM 2</span>
                 <span className="text-[10px] text-slate-500 font-mono">Segment Anything Model 2</span>
               </label>
               <label className="flex items-center gap-2 cursor-pointer">
                 <input
                   type="checkbox"
                   checked={useSam3}
                   onChange={(event) => setUseSam3(event.target.checked)}
                 />
                 <span className="text-slate-200">SAM 3</span>
                 <span className="text-[10px] text-slate-500 font-mono">Segment Anything Model 3</span>
               </label>
            </div>
             {!useYolo && !useLaeDino && !useMmrotate && !useLsknet && !useSam2 && !useSam3 && (
               <div className="text-[10px] font-mono text-rose-400">
                 select at least one provider
               </div>
             )}
             {[useYolo, useLaeDino, useMmrotate, useLsknet, useSam2, useSam3].filter(Boolean).length > 1 && (
               <div className="text-[10px] font-mono text-emerald-400/80">
                 results will be merged; detections confirm when cross-confirmed or high confidence
               </div>
             )}
          </div>

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
               disabled={!file || uploading || (!useYolo && !useLaeDino && !useMmrotate && !useLsknet && !useSam2 && !useSam3)}
               className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed rounded px-4 py-3 text-sm font-bold uppercase tracking-wider flex items-center justify-center gap-2"
             >
              <DatabaseZap className="w-4 h-4" /> Upload
            </button>
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

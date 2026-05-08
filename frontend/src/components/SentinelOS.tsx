import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import axios from 'axios';
import {
  Activity,
  BarChart3,
  Bot,
  BrainCircuit,
  CheckCircle2,
  ClipboardList,
  Database,
  FileText,
  Globe2,
  Hexagon,
  Layers,
  Map as MapIcon,
  Network,
  RadioTower,
  RefreshCw,
  Search,
  ShieldCheck,
  UploadCloud,
  Users,
  Workflow,
} from 'lucide-react';

const API_URL = import.meta.env.VITE_API_URL || '';

type TabKey = 'dashboard' | 'geoint' | 'sigint' | 'humint' | 'osint' | 'timeline' | 'workflows' | 'ai' | 'admin';
type Domain = 'GEOINT' | 'SIGINT' | 'HUMINT' | 'OSINT' | 'WORKFLOW' | 'ADMIN';

interface Health {
  healthy: boolean;
  neo4j: string;
  postgis: string;
  ai?: { configured?: boolean; model?: string; mode?: string };
}

interface AppData {
  health: Health;
  summary: any;
  targets: any[];
  sources: any[];
  observations: any[];
  timeline: any[];
  uploads: any[];
  fmv: any[];
  analytics: any[];
  requirements: any[];
  pedTasks: any[];
  reports: any[];
  proposals: any[];
  models: any[];
  datasets: any[];
  training: any[];
}

const emptyData: AppData = {
  health: { healthy: false, neo4j: 'unknown', postgis: 'unknown', ai: {} },
  summary: {},
  targets: [],
  sources: [],
  observations: [],
  timeline: [],
  uploads: [],
  fmv: [],
  analytics: [],
  requirements: [],
  pedTasks: [],
  reports: [],
  proposals: [],
  models: [],
  datasets: [],
  training: [],
};

const tabs: Array<{ key: TabKey; label: string; short: string; icon: any }> = [
  { key: 'dashboard', label: 'Dashboard', short: 'DASH', icon: Activity },
  { key: 'geoint', label: 'GEOINT', short: 'GEO', icon: MapIcon },
  { key: 'sigint', label: 'SIGINT', short: 'SIG', icon: RadioTower },
  { key: 'humint', label: 'HUMINT', short: 'HUM', icon: Users },
  { key: 'osint', label: 'OSINT', short: 'OSI', icon: Globe2 },
  { key: 'timeline', label: 'Fusion Timeline', short: 'TIME', icon: BarChart3 },
  { key: 'workflows', label: 'Workflows', short: 'FLOW', icon: Workflow },
  { key: 'ai', label: 'AI Analyst', short: 'AI', icon: Bot },
  { key: 'admin', label: 'Admin & Models', short: 'ADM', icon: BrainCircuit },
];

function domainForTab(tab: TabKey): Domain {
  if (tab === 'sigint') return 'SIGINT';
  if (tab === 'humint') return 'HUMINT';
  if (tab === 'osint') return 'OSINT';
  if (tab === 'workflows') return 'WORKFLOW';
  if (tab === 'admin') return 'ADMIN';
  return 'GEOINT';
}

function domainColor(domain?: string) {
  switch ((domain || '').toUpperCase()) {
    case 'GEOINT': return 'text-cyan-200 border-cyan-500/40 bg-cyan-500/10';
    case 'SIGINT': return 'text-emerald-200 border-emerald-500/40 bg-emerald-500/10';
    case 'HUMINT': return 'text-amber-200 border-amber-500/40 bg-amber-500/10';
    case 'OSINT': return 'text-blue-200 border-blue-500/40 bg-blue-500/10';
    case 'WORKFLOW': return 'text-lime-200 border-lime-500/40 bg-lime-500/10';
    default: return 'text-slate-300 border-slate-700 bg-slate-900';
  }
}

function Panel({ title, icon: Icon, children, right }: { title: string; icon: any; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="min-h-0 border border-slate-800 bg-slate-950/75">
      <div className="h-10 border-b border-slate-800 px-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-slate-300">
          <Icon className="w-4 h-4 text-lime-300" /> {title}
        </div>
        {right}
      </div>
      <div className="p-3 min-h-0">{children}</div>
    </section>
  );
}

function Metric({ label, value, tone = 'slate' }: { label: string; value: any; tone?: 'slate' | 'green' | 'blue' | 'amber' | 'red' }) {
  const tones = {
    slate: 'border-slate-800 bg-slate-900/80 text-slate-100',
    green: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-100',
    blue: 'border-blue-500/30 bg-blue-500/10 text-blue-100',
    amber: 'border-amber-500/30 bg-amber-500/10 text-amber-100',
    red: 'border-rose-500/30 bg-rose-500/10 text-rose-100',
  };
  return (
    <div className={`border px-3 py-2 ${tones[tone]}`}>
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value ?? 0}</div>
    </div>
  );
}

function SourceIngestPanel({ domain, compact, onRefresh }: { domain: Domain; compact?: boolean; onRefresh: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState('');
  const [form, setForm] = useState({
    name: `${domain} Source`,
    feed_type: domain === 'SIGINT' ? 'RF/SIGINT' : domain,
    protocol: domain === 'OSINT' ? 'http' : 'tcp',
    endpoint: domain === 'OSINT' ? 'https://example.local/feed' : 'tcp://localhost:4002',
    parser: domain === 'SIGINT' ? 'json' : 'raw',
  });

  const accept = domain === 'GEOINT'
    ? '.tif,.tiff,.jp2,.j2k,.nc,.png,.jpg,.jpeg,.mp4,.mov,.m4v,.ts,.geojson,.json,.kml,.kmz,.gpkg,.zip'
    : domain === 'HUMINT'
      ? '.pdf,.txt,.docx,.wav,.mp3,.m4a,.aac,.ogg,.flac,.amr'
      : domain === 'OSINT'
        ? '.pdf,.txt,.csv,.xlsx,.docx,.json,.geojson'
        : '.json,.csv,.txt,.pcap,.zip';

  const upload = async () => {
    if (!file || busy) return;
    setBusy('upload');
    setStatus('Uploading source...');
    try {
      const body = new FormData();
      body.append('file', file);
      body.append('sensor_type', domain);
      body.append('auto_process', 'true');
      const response = await axios.post(`${API_URL}/api/ingest/upload`, body, { headers: { 'Content-Type': 'multipart/form-data' } });
      setStatus(response.data.message || `Stored ${response.data.filename}`);
      setFile(null);
      onRefresh();
    } catch (error: any) {
      setStatus(error.response?.data?.detail || 'Upload failed.');
    } finally {
      setBusy('');
    }
  };

  const ingestUrl = async () => {
    if (!url.trim() || busy) return;
    setBusy('url');
    setStatus('Queueing URL ingestion...');
    try {
      const response = await axios.post(`${API_URL}/api/ingest/url`, {
        url,
        domain,
        source_type: domain === 'OSINT' ? 'web' : 'url',
        auto_process: true,
      });
      setStatus(response.data.message || 'URL queued.');
      setUrl('');
      onRefresh();
    } catch (error: any) {
      setStatus(error.response?.data?.detail || 'URL ingest failed.');
    } finally {
      setBusy('');
    }
  };

  const connect = async () => {
    if (busy) return;
    setBusy('stream');
    setStatus('Connecting source...');
    try {
      const response = await axios.post(`${API_URL}/api/feeds/connect`, { ...form, topic: 'feeds', enabled: true });
      setStatus(`Connected ${response.data.feed.name}`);
      onRefresh();
    } catch (error: any) {
      setStatus(error.response?.data?.detail || 'Connection failed.');
    } finally {
      setBusy('');
    }
  };

  return (
    <Panel title={`${domain} ingest`} icon={UploadCloud} right={<span className={`text-[10px] px-2 py-1 border ${domainColor(domain)}`}>{busy || 'READY'}</span>}>
      <div className={`grid gap-3 ${compact ? 'grid-cols-1' : 'xl:grid-cols-[1fr_1fr]'}`}>
        <div className="space-y-2">
          <label className="h-24 border border-dashed border-slate-700 bg-slate-900/70 grid place-items-center cursor-pointer">
            <div className="text-center px-3">
              <UploadCloud className="w-6 h-6 mx-auto mb-1 text-slate-500" />
              <div className="text-xs text-slate-200 truncate max-w-80">{file ? file.name : `Upload ${domain} file`}</div>
              <div className="text-[10px] text-slate-500 mt-1">Documents, streams, imagery, audio, or datasets as applicable</div>
            </div>
            <input className="hidden" type="file" accept={accept} onChange={(event) => setFile(event.target.files?.[0] || null)} />
          </label>
          <button onClick={upload} disabled={!file || !!busy} className="h-9 w-full border border-blue-500/50 bg-blue-500/15 text-blue-100 text-xs uppercase tracking-wider disabled:opacity-40">
            Upload / Process
          </button>
        </div>
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <input value={form.name} onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))} className="col-span-2 h-8 bg-slate-900 border border-slate-700 px-2 text-xs" />
            <select value={form.feed_type} onChange={(event) => setForm((prev) => ({ ...prev, feed_type: event.target.value }))} className="h-8 bg-slate-900 border border-slate-700 px-2 text-xs">
              <option>GEOINT</option><option>RF/SIGINT</option><option>AIS</option><option>ADS-B</option><option>HUMINT</option><option>OSINT</option><option>Webhook</option><option>FMV</option>
            </select>
            <select value={form.protocol} onChange={(event) => setForm((prev) => ({ ...prev, protocol: event.target.value }))} className="h-8 bg-slate-900 border border-slate-700 px-2 text-xs">
              <option value="tcp">TCP</option><option value="udp">UDP</option><option value="http">HTTP</option><option value="https">HTTPS</option><option value="websocket">WebSocket</option><option value="file">File</option><option value="serial">Serial</option>
            </select>
            <select value={form.parser} onChange={(event) => setForm((prev) => ({ ...prev, parser: event.target.value }))} className="h-8 bg-slate-900 border border-slate-700 px-2 text-xs">
              <option value="raw">Raw</option><option value="json">JSON</option><option value="csv">CSV</option><option value="nmea">NMEA</option><option value="klv">MISB KLV</option>
            </select>
            <input value={form.endpoint} onChange={(event) => setForm((prev) => ({ ...prev, endpoint: event.target.value }))} className="h-8 bg-slate-900 border border-slate-700 px-2 text-xs font-mono" />
          </div>
          <button onClick={connect} disabled={!!busy} className="h-9 w-full border border-emerald-500/50 bg-emerald-500/15 text-emerald-100 text-xs uppercase tracking-wider disabled:opacity-40">
            Connect Source
          </button>
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https:// or file URL" className="h-8 bg-slate-900 border border-slate-700 px-2 text-xs font-mono" />
            <button onClick={ingestUrl} disabled={!url.trim() || !!busy} className="h-8 px-3 border border-slate-600 text-xs text-slate-200 disabled:opacity-40">Queue URL</button>
          </div>
        </div>
      </div>
      {status && <div className="mt-3 border border-slate-800 bg-slate-900 px-3 py-2 text-xs text-lime-200 font-mono">{status}</div>}
    </Panel>
  );
}

function TimeseriesPanel({ events, observations, domain }: { events: any[]; observations: any[]; domain?: Domain }) {
  const [range, setRange] = useState(70);
  const filteredEvents = domain ? events.filter((event) => event.domain === domain) : events;
  const filteredObservations = domain ? observations.filter((item) => item.domain === domain) : observations;
  const bars = Array.from({ length: 24 }, (_, index) => {
    const seed = filteredEvents.length + filteredObservations.length + index * 7;
    return 14 + (seed % 72);
  });
  return (
    <Panel title={domain ? `${domain} timeseries` : 'Fusion timeline'} icon={BarChart3} right={<span className="text-[10px] text-slate-500">{filteredEvents.length + filteredObservations.length} events</span>}>
      <div className="grid grid-cols-[1fr_auto] gap-3 items-end">
        <div>
          <div className="h-24 border border-slate-800 bg-slate-900/60 px-2 flex items-end gap-1">
            {bars.map((height, index) => (
              <div key={index} className={`flex-1 ${index < range / 4 ? 'bg-lime-400/70' : 'bg-slate-700'}`} style={{ height: `${height}%` }} />
            ))}
          </div>
          <input className="mt-2 w-full" type="range" min="0" max="96" value={range} onChange={(event) => setRange(Number(event.target.value))} />
        </div>
        <div className="w-44 text-xs space-y-2">
          <div className="border border-slate-800 bg-slate-900 p-2">
            <div className="text-slate-500">Selected offset</div>
            <div className="text-slate-200 font-mono">T-{96 - range}h</div>
          </div>
          <div className="border border-slate-800 bg-slate-900 p-2">
            <div className="text-slate-500">Domains</div>
            <div className="text-slate-200">{domain || 'All fused'}</div>
          </div>
        </div>
      </div>
      <div className="mt-3 max-h-48 overflow-auto space-y-1">
        {filteredEvents.slice(0, 8).map((event) => (
          <div key={event.id} className="border border-slate-800 bg-slate-900/70 px-2 py-2 text-xs flex justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-slate-200">{event.title}</div>
              <div className="text-[10px] text-slate-500">{event.event_type}</div>
            </div>
            <span className={`shrink-0 px-2 py-0.5 border text-[10px] ${domainColor(event.domain)}`}>{event.domain}</span>
          </div>
        ))}
        {filteredEvents.length === 0 && <div className="text-xs text-slate-500 border border-slate-800 p-3">No timeline events in this filter.</div>}
      </div>
    </Panel>
  );
}

function EntityInsightPanel({ targets, observations }: { targets: any[]; observations: any[] }) {
  return (
    <Panel title="Entity insight" icon={Network} right={<span className="text-[10px] text-slate-500">{targets.length} targets</span>}>
      <div className="space-y-2">
        {targets.slice(0, 5).map((target) => {
          const props = target.properties || {};
          return (
            <div key={target.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs">
              <div className="flex justify-between gap-2">
                <span className="font-semibold truncate">{props.name || target.id}</span>
                <span className="text-amber-300">{props.priority || 'Unrated'}</span>
              </div>
              <div className="mt-1 text-slate-500 truncate">{props.description || props.type || 'No description'}</div>
            </div>
          );
        })}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <Metric label="Observations" value={observations.length} tone="blue" />
        <Metric label="Confidence avg" value={observations.length ? '0.62' : '0.00'} tone="green" />
      </div>
    </Panel>
  );
}

function AIWorkflowPanel({ data, onRefresh }: { data: AppData; onRefresh: () => void }) {
  const [prompt, setPrompt] = useState('Summarize current mission risk and propose next internal workflow action.');
  const [response, setResponse] = useState<any>(null);
  const [busy, setBusy] = useState('');
  const [status, setStatus] = useState('');

  const analyze = async () => {
    setBusy('analyze');
    try {
      const result = await axios.post(`${API_URL}/api/ai/analyze`, { prompt, domain: 'WORKFLOW' });
      setResponse(result.data.analysis);
      setStatus('AI analysis generated with ontology citations.');
    } catch (error: any) {
      setStatus(error.response?.data?.detail || 'AI analysis unavailable.');
    } finally {
      setBusy('');
    }
  };

  const propose = async (actionType = 'generate_report') => {
    setBusy('propose');
    try {
      const result = await axios.post(`${API_URL}/api/ai/propose-actions`, {
        prompt,
        domain: 'WORKFLOW',
        action_type: actionType,
        target_id: data.targets[0]?.id,
        payload: { title: actionType === 'create_requirement' ? 'AI proposed collection requirement' : 'AI generated intelligence package' },
        risk_level: 'low',
      });
      setStatus(`Proposal #${result.data.proposal.id} queued for approval.`);
      onRefresh();
    } catch (error: any) {
      setStatus(error.response?.data?.detail || 'Proposal failed.');
    } finally {
      setBusy('');
    }
  };

  const approve = async (id: number) => {
    await axios.post(`${API_URL}/api/actions/proposals/${id}/approve`);
    setStatus(`Proposal #${id} approved.`);
    onRefresh();
  };

  const execute = async (id: number) => {
    await axios.post(`${API_URL}/api/actions/proposals/${id}/execute`);
    setStatus(`Proposal #${id} executed internally.`);
    onRefresh();
  };

  return (
    <Panel title="AI workflow" icon={Bot} right={<span className="text-[10px] text-amber-300">Human approval required</span>}>
      <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} className="h-24 w-full bg-slate-900 border border-slate-700 p-3 text-sm outline-none focus:border-lime-500/70" />
      <div className="mt-2 grid grid-cols-3 gap-2">
        <button onClick={analyze} disabled={!!busy} className="h-9 border border-lime-500/50 bg-lime-500/10 text-xs text-lime-100 disabled:opacity-40">Analyze</button>
        <button onClick={() => propose('generate_report')} disabled={!!busy} className="h-9 border border-blue-500/50 bg-blue-500/10 text-xs text-blue-100 disabled:opacity-40">Propose Report</button>
        <button onClick={() => propose('create_requirement')} disabled={!!busy} className="h-9 border border-amber-500/50 bg-amber-500/10 text-xs text-amber-100 disabled:opacity-40">Propose Task</button>
      </div>
      {response && (
        <div className="mt-3 border border-slate-800 bg-slate-900/70 p-3 text-xs">
          <pre className="whitespace-pre-wrap font-sans text-slate-200">{response.summary}</pre>
          <div className="mt-2 text-[10px] text-slate-500">Citations: {(response.citations || []).map((item: any) => item.label).join(', ')}</div>
        </div>
      )}
      <div className="mt-3 space-y-2 max-h-72 overflow-auto">
        {data.proposals.slice(0, 8).map((proposal) => (
          <div key={proposal.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs">
            <div className="flex justify-between gap-2">
              <span className="font-semibold truncate">{proposal.title}</span>
              <span className={`px-2 py-0.5 border ${proposal.status === 'executed' ? 'border-emerald-500/50 text-emerald-300' : 'border-amber-500/50 text-amber-300'}`}>{proposal.status}</span>
            </div>
            <div className="mt-1 text-slate-500 truncate">{proposal.action_type} / {proposal.risk_level}</div>
            <div className="mt-2 flex gap-2">
              <button onClick={() => approve(proposal.id)} disabled={proposal.status !== 'pending_approval'} className="h-7 px-2 border border-blue-500/40 text-blue-200 disabled:opacity-30">Approve</button>
              <button onClick={() => execute(proposal.id)} disabled={proposal.status !== 'approved'} className="h-7 px-2 border border-emerald-500/40 text-emerald-200 disabled:opacity-30">Execute</button>
            </div>
          </div>
        ))}
      </div>
      {status && <div className="mt-3 text-xs font-mono text-lime-200">{status}</div>}
    </Panel>
  );
}

function Dashboard({ data, onRefresh }: { data: AppData; onRefresh: () => void }) {
  const counts = data.summary.counts || {};
  return (
    <div className="h-full min-h-0 grid grid-rows-[auto_minmax(0,1fr)] gap-3 p-3 overflow-hidden">
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2">
        <Metric label="Targets" value={counts.targets || data.targets.length} tone="blue" />
        <Metric label="High Priority" value={counts.high_priority_targets} tone="amber" />
        <Metric label="Sources" value={counts.active_sources || data.sources.length} tone="green" />
        <Metric label="Uploads" value={counts.uploads || data.uploads.length} />
        <Metric label="Observations" value={counts.observations || data.observations.length} tone="blue" />
        <Metric label="Events 24h" value={counts.recent_events || data.timeline.length} />
        <Metric label="AI Actions" value={counts.pending_actions || data.proposals.filter((p) => p.status === 'pending_approval').length} tone="amber" />
        <Metric label="Models" value={data.models.length} tone="green" />
      </div>
      <div className="min-h-0 grid grid-cols-1 xl:grid-cols-[1.2fr_1fr_1fr] gap-3 overflow-hidden">
        <div className="min-h-0 space-y-3 overflow-auto">
          <SourceIngestPanel domain="GEOINT" compact onRefresh={onRefresh} />
          <TimeseriesPanel events={data.timeline} observations={data.observations} />
        </div>
        <div className="min-h-0 space-y-3 overflow-auto">
          <EntityInsightPanel targets={data.targets} observations={data.observations} />
          <Panel title="Active sources" icon={RadioTower} right={<span className="text-[10px] text-slate-500">{data.sources.length}</span>}>
            <div className="space-y-1">
              {data.sources.slice(0, 8).map((source) => (
                <div key={source.id} className="border border-slate-800 bg-slate-900/70 px-2 py-2 text-xs flex justify-between gap-2">
                  <span className="truncate">{source.name}</span>
                  <span className="text-emerald-300">{source.status}</span>
                </div>
              ))}
            </div>
          </Panel>
        </div>
        <div className="min-h-0 overflow-auto">
          <AIWorkflowPanel data={data} onRefresh={onRefresh} />
        </div>
      </div>
    </div>
  );
}

function IntelligenceWorkspace({ domain, data, onRefresh }: { domain: Domain; data: AppData; onRefresh: () => void }) {
  const observations = data.observations.filter((item) => item.domain === domain);
  const sources = data.sources.filter((item) => (item.source_type || item.feed_type || '').toUpperCase().includes(domain) || (item.metadata?.domain === domain));
  const domainAnalytics = domain === 'GEOINT' ? data.analytics : [];
  return (
    <div className="h-full min-h-0 grid grid-cols-1 xl:grid-cols-[420px_minmax(0,1fr)_360px] gap-3 p-3 overflow-hidden">
      <div className="min-h-0 space-y-3 overflow-auto">
        <SourceIngestPanel domain={domain} compact onRefresh={onRefresh} />
        <Panel title={`${domain} sources`} icon={Database} right={<span className="text-[10px] text-slate-500">{sources.length}</span>}>
          <div className="space-y-1">
            {sources.slice(0, 8).map((source) => (
              <div key={source.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs">
                <div className="flex justify-between gap-2">
                  <span className="truncate font-semibold">{source.name}</span>
                  <span className="text-emerald-300">{source.status}</span>
                </div>
                <div className="text-[10px] text-slate-500 truncate">{source.source_type || source.feed_type} / {source.protocol} / {source.parser || 'raw'}</div>
              </div>
            ))}
            {sources.length === 0 && <div className="text-xs text-slate-500 border border-slate-800 p-3">No {domain} sources connected yet.</div>}
          </div>
        </Panel>
      </div>
      <div className="min-h-0 space-y-3 overflow-auto">
        {domain === 'GEOINT' && (
          <Panel title="Geospatial operations" icon={Layers}>
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
              {['change', 'viewshed', 'los', 'routes', 'pol'].map((kind) => (
                <button
                  key={kind}
                  onClick={async () => { await axios.post(`${API_URL}/api/analytics/${kind}`, { radius_m: 5000 }); onRefresh(); }}
                  className="h-9 border border-cyan-500/40 bg-cyan-500/10 text-xs uppercase text-cyan-100"
                >
                  {kind}
                </button>
              ))}
            </div>
            <div className="mt-3 h-72 border border-slate-800 bg-[url('/world_map.svg')] bg-center bg-cover relative overflow-hidden">
              <div className="absolute inset-0 bg-slate-950/55" />
              {data.targets.slice(0, 8).map((target, index) => (
                <div
                  key={target.id}
                  className="absolute h-3 w-3 border border-rose-300 bg-rose-500/70"
                  style={{ left: `${18 + (index * 11) % 70}%`, top: `${22 + (index * 17) % 52}%` }}
                  title={target.properties?.name}
                />
              ))}
              <div className="absolute left-3 bottom-3 text-xs text-slate-300 font-mono">Offline geospatial context / target overlays</div>
            </div>
          </Panel>
        )}
        {domain !== 'GEOINT' && (
          <Panel title={`${domain} analysis workflow`} icon={Search}>
            <div className="grid grid-cols-3 gap-2">
              <button className="h-9 border border-lime-500/40 bg-lime-500/10 text-xs text-lime-100">Extract</button>
              <button className="h-9 border border-blue-500/40 bg-blue-500/10 text-xs text-blue-100">Link</button>
              <button className="h-9 border border-amber-500/40 bg-amber-500/10 text-xs text-amber-100">Summarize</button>
            </div>
            <div className="mt-3 text-xs text-slate-400 leading-relaxed">
              Uploaded records and connected streams are normalized into observations, timeline events, and ontology candidates. AI extraction remains reviewable before links or tasks are executed.
            </div>
          </Panel>
        )}
        <TimeseriesPanel events={data.timeline} observations={data.observations} domain={domain} />
        {domainAnalytics.length > 0 && (
          <Panel title="Analytic jobs" icon={BarChart3}>
            <div className="space-y-1">
              {domainAnalytics.slice(0, 8).map((job) => (
                <div key={job.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs flex justify-between">
                  <span>{job.job_type}</span>
                  <span className="text-emerald-300">{job.status}</span>
                </div>
              ))}
            </div>
          </Panel>
        )}
      </div>
      <div className="min-h-0 space-y-3 overflow-auto">
        <Panel title={`${domain} observations`} icon={Activity} right={<span className="text-[10px] text-slate-500">{observations.length}</span>}>
          <div className="space-y-1 max-h-80 overflow-auto">
            {observations.slice(0, 12).map((observation) => (
              <div key={observation.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs">
                <div className="font-semibold truncate">{observation.title}</div>
                <div className="text-[10px] text-slate-500">{observation.event_type} / {new Date(observation.observed_at).toLocaleString()}</div>
              </div>
            ))}
            {observations.length === 0 && <div className="text-xs text-slate-500 border border-slate-800 p-3">No observations yet.</div>}
          </div>
        </Panel>
        <AIWorkflowPanel data={data} onRefresh={onRefresh} />
      </div>
    </div>
  );
}

function Workflows({ data, onRefresh }: { data: AppData; onRefresh: () => void }) {
  return (
    <div className="h-full min-h-0 grid grid-cols-1 xl:grid-cols-[1fr_1fr_420px] gap-3 p-3 overflow-hidden">
      <Panel title="Collection and PED" icon={ClipboardList}>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="text-xs uppercase tracking-wider text-slate-500 mb-2">Requirements</div>
            <div className="space-y-1 max-h-[70vh] overflow-auto">
              {data.requirements.map((item) => <div key={item.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs">{item.title}<div className="text-slate-500">{item.priority} / {item.status}</div></div>)}
            </div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wider text-slate-500 mb-2">PED tasks</div>
            <div className="space-y-1 max-h-[70vh] overflow-auto">
              {data.pedTasks.map((item) => <div key={item.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs">{item.title}<div className="text-emerald-300">{item.status}</div></div>)}
            </div>
          </div>
        </div>
      </Panel>
      <Panel title="Reports and dissemination" icon={FileText}>
        <div className="space-y-1 max-h-[75vh] overflow-auto">
          {data.reports.map((report) => <div key={report.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs">{report.title}<div className="text-slate-500">{report.report_type} / {report.status}</div></div>)}
          {data.reports.length === 0 && <div className="text-xs text-slate-500 border border-slate-800 p-3">No reports generated yet.</div>}
        </div>
      </Panel>
      <AIWorkflowPanel data={data} onRefresh={onRefresh} />
    </div>
  );
}

function AdminModels({ data, onRefresh }: { data: AppData; onRefresh: () => void }) {
  const [dataset, setDataset] = useState<File | null>(null);
  const [status, setStatus] = useState('');
  const uploadDataset = async () => {
    if (!dataset) return;
    const body = new FormData();
    body.append('file', dataset);
    body.append('name', dataset.name);
    body.append('dataset_type', 'object_detection');
    body.append('domain', 'GEOINT');
    const response = await axios.post(`${API_URL}/api/models/datasets`, body, { headers: { 'Content-Type': 'multipart/form-data' } });
    setStatus(`Dataset stored: ${response.data.dataset.name}`);
    setDataset(null);
    onRefresh();
  };
  const queueTraining = async () => {
    const response = await axios.post(`${API_URL}/api/training/jobs`, { name: `Custom model ${new Date().toLocaleTimeString()}`, epochs: 1, dataset_path: data.datasets[0]?.file_path });
    setStatus(`Training job #${response.data.job.id} queued.`);
    onRefresh();
  };
  return (
    <div className="h-full min-h-0 grid grid-cols-1 xl:grid-cols-[420px_1fr_1fr] gap-3 p-3 overflow-hidden">
      <Panel title="LLM and action policy" icon={ShieldCheck}>
        <div className="space-y-2 text-xs">
          <div className="border border-slate-800 bg-slate-900/70 p-3">
            <div className="text-slate-500">LLM runtime</div>
            <div className="text-slate-100">{data.health.ai?.configured ? data.health.ai?.model : 'Local/offline fallback'}</div>
            <div className="mt-1 text-slate-500">{data.health.ai?.mode || 'read_only_graph_summary'}</div>
          </div>
          <div className="border border-amber-500/30 bg-amber-500/10 p-3 text-amber-100">
            AI may propose actions, but approval is required before mutation or external dispatch.
          </div>
          <SourceIngestPanel domain="ADMIN" compact onRefresh={onRefresh} />
        </div>
      </Panel>
      <Panel title="Datasets and training" icon={BrainCircuit}>
        <label className="h-24 border border-dashed border-slate-700 bg-slate-900/70 grid place-items-center cursor-pointer">
          <div className="text-center">
            <Database className="w-6 h-6 mx-auto mb-1 text-slate-500" />
            <div className="text-xs">{dataset ? dataset.name : 'Upload labeled dataset archive'}</div>
          </div>
          <input className="hidden" type="file" accept=".zip,.tar,.gz,.json,.yaml,.txt" onChange={(event) => setDataset(event.target.files?.[0] || null)} />
        </label>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <button onClick={uploadDataset} disabled={!dataset} className="h-9 border border-blue-500/50 bg-blue-500/10 text-xs text-blue-100 disabled:opacity-40">Store Dataset</button>
          <button onClick={queueTraining} className="h-9 border border-cyan-500/50 bg-cyan-500/10 text-xs text-cyan-100">Queue Training</button>
        </div>
        <div className="mt-3 space-y-1 max-h-72 overflow-auto">
          {data.datasets.map((item) => <div key={item.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs">{item.name}<div className="text-slate-500">{item.dataset_type} / {item.status}</div></div>)}
        </div>
        {status && <div className="mt-3 text-xs font-mono text-lime-200">{status}</div>}
      </Panel>
      <Panel title="Model registry" icon={CheckCircle2}>
        <div className="space-y-2">
          {data.models.map((model) => (
            <div key={model.id} className="border border-slate-800 bg-slate-900/70 p-3 text-xs">
              <div className="flex justify-between gap-2">
                <span className="font-semibold">{model.name}</span>
                <span className={model.promoted ? 'text-lime-300' : 'text-slate-500'}>{model.promoted ? 'promoted' : model.status}</span>
              </div>
              <button onClick={async () => { await axios.post(`${API_URL}/api/models/${model.id}/promote`); onRefresh(); }} className="mt-2 h-7 px-2 border border-lime-500/40 text-lime-200 disabled:opacity-30" disabled={model.promoted}>Promote</button>
            </div>
          ))}
          <div className="text-xs uppercase tracking-wider text-slate-500 pt-2">Training jobs</div>
          {data.training.map((job) => <div key={job.id} className="border border-slate-800 bg-slate-900/70 p-2 text-xs">{job.name}<div className="text-cyan-300">{job.status}</div></div>)}
        </div>
      </Panel>
    </div>
  );
}

export default function SentinelOS() {
  const [activeTab, setActiveTab] = useState<TabKey>(() => {
    const hash = window.location.hash.replace('#', '') as TabKey;
    return tabs.some((tab) => tab.key === hash) ? hash : 'dashboard';
  });
  const [data, setData] = useState<AppData>(emptyData);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [health, summary, targets, sources, observations, timeline, uploads, fmv, analytics, requirements, ped, reports, proposals, models, datasets, training] = await Promise.all([
        axios.get(`${API_URL}/api/health`),
        axios.get(`${API_URL}/api/dashboard/summary`),
        axios.get(`${API_URL}/api/ops/targets`),
        axios.get(`${API_URL}/api/sources`),
        axios.get(`${API_URL}/api/observations`),
        axios.get(`${API_URL}/api/timeline/events`),
        axios.get(`${API_URL}/api/ingest/uploads`),
        axios.get(`${API_URL}/api/fmv/clips`),
        axios.get(`${API_URL}/api/analytics/jobs`),
        axios.get(`${API_URL}/api/collection/requirements`),
        axios.get(`${API_URL}/api/ped/tasks`),
        axios.get(`${API_URL}/api/reports`),
        axios.get(`${API_URL}/api/actions/proposals`),
        axios.get(`${API_URL}/api/models`),
        axios.get(`${API_URL}/api/models/datasets`),
        axios.get(`${API_URL}/api/training/jobs`),
      ]);
      setData({
        health: health.data,
        summary: summary.data,
        targets: targets.data.targets || [],
        sources: sources.data.sources || [],
        observations: observations.data.observations || [],
        timeline: timeline.data.events || [],
        uploads: uploads.data.uploads || [],
        fmv: fmv.data.clips || [],
        analytics: analytics.data.jobs || [],
        requirements: requirements.data.requirements || [],
        pedTasks: ped.data.tasks || [],
        reports: reports.data.reports || [],
        proposals: proposals.data.proposals || [],
        models: models.data.models || [],
        datasets: datasets.data.datasets || [],
        training: training.data.jobs || [],
      });
    } catch (error) {
      console.error('SentinelOS refresh failed', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 15000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const active = useMemo(() => tabs.find((tab) => tab.key === activeTab) || tabs[0], [activeTab]);

  const selectTab = (tab: TabKey) => {
    setActiveTab(tab);
    window.history.replaceState(null, '', `#${tab}`);
  };

  const content = () => {
    if (activeTab === 'dashboard') return <Dashboard data={data} onRefresh={refresh} />;
    if (activeTab === 'timeline') return <div className="h-full p-3"><TimeseriesPanel events={data.timeline} observations={data.observations} /></div>;
    if (activeTab === 'workflows') return <Workflows data={data} onRefresh={refresh} />;
    if (activeTab === 'ai') return <div className="h-full p-3 grid grid-cols-1 xl:grid-cols-[1fr_420px] gap-3"><AIWorkflowPanel data={data} onRefresh={refresh} /><EntityInsightPanel targets={data.targets} observations={data.observations} /></div>;
    if (activeTab === 'admin') return <AdminModels data={data} onRefresh={refresh} />;
    return <IntelligenceWorkspace domain={domainForTab(activeTab)} data={data} onRefresh={refresh} />;
  };

  return (
    <div className="h-screen w-screen overflow-hidden bg-slate-950 text-slate-200 flex font-sans">
      <aside className="w-[76px] shrink-0 border-r border-slate-800 bg-slate-950 flex flex-col items-center py-3">
        <div className="h-11 w-11 border border-lime-500/40 bg-lime-500/10 grid place-items-center mb-4">
          <Hexagon className="w-6 h-6 text-lime-300" fill="currentColor" />
        </div>
        <div className="flex-1 w-full px-2 space-y-1 overflow-auto">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.key}
                onClick={() => selectTab(tab.key)}
                className={`h-12 w-full grid place-items-center border transition ${activeTab === tab.key ? 'border-lime-500/50 bg-lime-500/15 text-lime-200' : 'border-transparent text-slate-500 hover:border-slate-700 hover:text-slate-200'}`}
                title={tab.label}
              >
                <Icon className="w-5 h-5" />
              </button>
            );
          })}
        </div>
      </aside>
      <div className="min-w-0 flex-1 h-full flex flex-col">
        <header className="h-14 shrink-0 border-b border-slate-800 bg-slate-900/95 px-4 flex items-center justify-between">
          <div className="flex items-center gap-4 min-w-0">
            <div>
              <div className="text-[10px] uppercase tracking-[0.34em] text-lime-300">SentinelOS</div>
              <h1 className="text-sm font-semibold uppercase tracking-wider text-slate-100">{active.label}</h1>
            </div>
            <div className="hidden md:flex items-center gap-1">
              {tabs.map((tab) => (
                <button key={tab.key} onClick={() => selectTab(tab.key)} className={`h-8 px-3 border text-[11px] uppercase tracking-wider ${activeTab === tab.key ? 'border-lime-500/50 bg-lime-500/10 text-lime-200' : 'border-slate-800 bg-slate-950 text-slate-500 hover:text-slate-200'}`}>{tab.short}</button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-4 text-xs font-mono">
            <span className={data.health.healthy ? 'text-emerald-300' : 'text-rose-300'}>API {data.health.healthy ? 'ONLINE' : 'DEGRADED'}</span>
            <span className={data.health.neo4j === 'ok' ? 'text-emerald-300' : 'text-rose-300'}>ONTOLOGY {data.health.neo4j === 'ok' ? 'SYNCED' : 'OFFLINE'}</span>
            <span className={data.health.ai?.configured ? 'text-emerald-300' : 'text-amber-300'}>LLM {data.health.ai?.configured ? 'READY' : 'LOCAL'}</span>
            <button onClick={refresh} className="h-8 w-8 border border-slate-700 grid place-items-center text-slate-300" title="Refresh">
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-hidden bg-[radial-gradient(ellipse_at_top_right,_rgba(30,41,59,0.55),_rgba(2,6,23,1)_55%)]">
          {content()}
        </main>
      </div>
    </div>
  );
}

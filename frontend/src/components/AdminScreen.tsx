/**
 * AdminScreen — consolidates the four admin views into a single workspace:
 *   - Ontology  : delegates to the existing OntologyAdmin component (full CRUD)
 *   - Processing: live list of analytics + training jobs (POST/queued/running/done)
 *   - Models    : registered detection models, with one-click promotion
 *   - Alerts    : operator alert feed derived from /api/health + failed ingest tasks
 *
 * Every panel pulls from the real backend.  No mocked data.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import axios from 'axios';
import {
  Activity,
  AlertTriangle,
  Box,
  Cpu,
  GitBranch,
  MoreHorizontal,
  RefreshCw,
  Upload as UploadIcon,
} from 'lucide-react';
import OntologyAdmin from './OntologyAdmin';
import IngestConnect from './IngestConnect';
import AdminAuthTab from './AdminAuthTab';
import { StatusDot } from './atoms';
import HealthDashboardView from './admin/HealthDashboardView';
import ConfOverrideView from './admin/ConfOverrideView';
import PromptProfilesView from './admin/PromptProfilesView';
import TaxonomyVersionView from './admin/TaxonomyVersionView';
import { Filter, HeartPulse, History, Key, Search } from 'lucide-react';
import {
  type UploadJob,
  isUploadActive,
  uploadProgress,
  uploadStage,
} from '../utils/uploadProgress';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

type AdminTab =
  | 'ontology'
  | 'processing'
  | 'models'
  | 'alerts'
  | 'upload'
  | 'auth'
  | 'health'
  | 'confidence'
  | 'prompts'
  | 'versions';

type Counts = {
  processing: number;
  models: number;
  alerts: number;
};

type NavItemDef = {
  key: AdminTab;
  label: string;
  Icon: typeof Activity;
  badgeKey?: keyof Counts;
};

const NAV: NavItemDef[] = [
  { key: 'ontology',   label: 'Ontology',         Icon: GitBranch },
  { key: 'upload',     label: 'Upload',           Icon: UploadIcon },
  { key: 'processing', label: 'Processing',       Icon: Activity, badgeKey: 'processing' },
  { key: 'models',     label: 'AI models',        Icon: Box,      badgeKey: 'models' },
  { key: 'health',     label: 'Health dashboard', Icon: HeartPulse },
  { key: 'confidence', label: 'Conf overrides',   Icon: Filter },
  { key: 'prompts',    label: 'Prompt profiles',  Icon: Search },
  { key: 'versions',   label: 'Version history',  Icon: History },
  { key: 'alerts',     label: 'Health alerts',    Icon: AlertTriangle, badgeKey: 'alerts' },
  { key: 'auth',       label: 'Auth · LDAP',      Icon: Key },
];

type AdminScreenProps = {
  /** Switch to the GEOINT workspace focused on a specific detection. */
  onOpenDetectionOnMap?: (detectionId: number, className?: string) => void;
  /** Switch to the FMV workspace focused on a specific detection. */
  onOpenDetectionInFmv?: (detectionId: number) => void;
};

export default function AdminScreen({
  onOpenDetectionOnMap,
  onOpenDetectionInFmv,
}: AdminScreenProps = {}) {
  const [tab, setTab] = useState<AdminTab>('ontology');
  const [counts, setCounts] = useState<Counts>({ processing: 0, models: 0, alerts: 0 });

  // Listen for Shell's "jump to admin tab" events (e.g. Bell icon ⇒ alerts).
  useEffect(() => {
    const handler = (evt: Event) => {
      const detail = (evt as CustomEvent).detail || {};
      const target = String(detail.tab || '').toLowerCase();
      if (NAV.some((n) => n.key === target)) setTab(target as AdminTab);
    };
    window.addEventListener('sentinel:admin-tab', handler);
    return () => window.removeEventListener('sentinel:admin-tab', handler);
  }, []);

  return (
    <div
      className="admin-shell"
      style={{
        height: '100%',
        display: 'grid',
        gap: 1,
        background: 'var(--line)',
      }}
    >
      <nav
        className="panel admin-nav"
        style={{ border: 0, display: 'flex', flexDirection: 'column' }}
      >
        <div className="panel-h">
          <Cpu size={14} />
          <span className="h-title">Operations</span>
        </div>
        {NAV.map((n) => {
          const { Icon } = n;
          const active = tab === n.key;
          const badge = n.badgeKey ? counts[n.badgeKey] : undefined;
          return (
            <button
              key={n.key}
              type="button"
              onClick={() => setTab(n.key)}
              style={{
                display: 'grid',
                gridTemplateColumns: '24px 1fr auto',
                gap: 8,
                alignItems: 'center',
                padding: '10px 14px',
                border: 0,
                background: active ? 'var(--bg-2)' : 'transparent',
                borderLeft: active ? '2px solid var(--accent)' : '2px solid transparent',
                color: active ? 'var(--ink-0)' : 'var(--ink-1)',
                cursor: 'pointer',
                textAlign: 'left',
                fontSize: 12.5,
              }}
            >
              <Icon size={14} />
              <span>{n.label}</span>
              {badge != null && (
                <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>
                  {badge}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      <section
        className="admin-content"
        style={{
          background: 'var(--bg-0)',
          display: 'flex',
          flexDirection: 'column',
          minWidth: 0,
          minHeight: 0,
          overflow: 'hidden',
        }}
      >
        {tab === 'ontology'   && (
          <OntologyAdmin
            onOpenDetectionOnMap={onOpenDetectionOnMap}
            onOpenDetectionInFmv={onOpenDetectionInFmv}
          />
        )}
        {tab === 'upload'     && <IngestConnect />}
        {tab === 'processing' && <ProcessingView onCount={(n) => setCounts((c) => ({ ...c, processing: n }))} />}
        {tab === 'models'     && <ModelsView onCount={(n) => setCounts((c) => ({ ...c, models: n }))} />}
        {tab === 'alerts'     && <AlertsView onCount={(n) => setCounts((c) => ({ ...c, alerts: n }))} />}
        {tab === 'auth'       && <AdminAuthTab />}
        {tab === 'health'     && <HealthDashboardView />}
        {tab === 'confidence' && <ConfOverrideView />}
        {tab === 'prompts'    && <PromptProfilesView />}
        {tab === 'versions'   && <TaxonomyVersionView />}
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Shared bits                                                        */
/* ------------------------------------------------------------------ */

function ViewHeader({
  title,
  sub,
  actions,
}: {
  title: string;
  sub: string;
  actions?: ReactNode;
}) {
  return (
    <div
      className="view-header"
      style={{
        borderBottom: '1px solid var(--line)',
      }}
    >
      <div className="view-header-copy">
        <div style={{ fontSize: 16, fontWeight: 600, lineHeight: 1.2 }}>{title}</div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', marginTop: 4 }}>
          {sub}
        </div>
      </div>
      <div style={{ flex: 1 }} />
      {actions && <div className="view-header-actions">{actions}</div>}
    </div>
  );
}

function relativeTime(iso: string | undefined | null): string {
  if (!iso) return '—';
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return '—';
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

/* ------------------------------------------------------------------ */
/*  Processing                                                         */
/* ------------------------------------------------------------------ */

type JobRow = {
  id: string | number;
  title: string;
  model: string;
  stage: string;
  status: string;
  created_at?: string | null;
  pct?: number;
  raw_source: 'analytics' | 'training' | 'ingest';
};

function ingestStatus(job: UploadJob): 'running' | 'queued' | 'done' | 'failed' {
  if (job.status === 'failed') return 'failed';
  if (job.status === 'ready') return 'done';
  if (job.status === 'queued') return 'queued';
  return isUploadActive(job) ? 'running' : 'queued';
}

type Filter = 'all' | 'running' | 'queued' | 'done' | 'failed';

function ProcessingView({ onCount }: { onCount: (n: number) => void }) {
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [filter, setFilter] = useState<Filter>('all');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [a, t, u] = await Promise.allSettled([
        axios.get<{ jobs?: any[] }>(`${API_URL}/api/analytics/jobs`),
        axios.get<{ jobs?: any[] }>(`${API_URL}/api/training/jobs`),
        axios.get<{ uploads?: UploadJob[] } | UploadJob[]>(`${API_URL}/api/ingest/uploads`),
      ]);
      const aRows: JobRow[] = a.status === 'fulfilled'
        ? (a.value.data.jobs ?? []).map((j) => ({
            id: j.id,
            title: j.input?.title || j.job_type || `analytics:${j.id}`,
            model: j.job_type || 'analytics',
            stage: j.status || 'queued',
            status: j.status || 'queued',
            created_at: j.created_at,
            pct: j.status === 'completed' || j.status === 'done' ? 1 : j.status === 'running' ? 0.5 : 0,
            raw_source: 'analytics' as const,
          }))
        : [];
      const tRows: JobRow[] = t.status === 'fulfilled'
        ? (t.value.data.jobs ?? []).map((j) => ({
            id: j.id,
            title: j.dataset_name || `training:${j.id}`,
            model: 'training',
            stage: j.status || 'queued',
            status: j.status || 'queued',
            created_at: j.created_at,
            pct: j.status === 'completed' || j.status === 'done' ? 1 : j.status === 'running' ? 0.5 : 0,
            raw_source: 'training' as const,
          }))
        : [];
      const uploadsRaw: UploadJob[] = u.status === 'fulfilled'
        ? (Array.isArray(u.value.data)
            ? u.value.data
            : (u.value.data as { uploads?: UploadJob[] }).uploads ?? [])
        : [];
      const uRows: JobRow[] = uploadsRaw.map((j) => ({
        id: j.upload_id || j.id,
        title: j.filename || `ingest:${j.upload_id || j.id}`,
        model: (j as any).handler || j.media_type || 'ingest',
        stage: uploadStage(j),
        status: ingestStatus(j),
        created_at: j.created_at,
        pct: uploadProgress(j) / 100,
        raw_source: 'ingest' as const,
      }));
      setJobs([...uRows, ...aRows, ...tRows]);
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, 10000);
    return () => window.clearInterval(id);
  }, [load]);

  useEffect(() => {
    onCount(jobs.filter((j) => j.status === 'running' || j.status === 'queued').length);
  }, [jobs, onCount]);

  const visible = useMemo(() => {
    if (filter === 'all') return jobs;
    return jobs.filter((j) => {
      if (filter === 'done') return j.status === 'completed' || j.status === 'done';
      if (filter === 'failed') return j.status === 'failed' || j.status === 'error';
      return j.status === filter;
    });
  }, [jobs, filter]);

  return (
    <>
      <ViewHeader
        title="Processing jobs"
        sub={`${jobs.length} jobs across ingest + analytics + training`}
        actions={
          <>
            <div className="seg">
              {(['all', 'running', 'queued', 'done', 'failed'] as Filter[]).map((f) => (
                <button
                  key={f}
                  className={filter === f ? 'on' : ''}
                  onClick={() => setFilter(f)}
                  type="button"
                >
                  {f.toUpperCase()}
                </button>
              ))}
            </div>
            <button className="btn sm" onClick={load} type="button" title="Refresh">
              <RefreshCw size={12} />
            </button>
          </>
        }
      />
      <div
        className="sentinel-scroll"
        style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 8 }}
      >
        {err && (
          <div className="card" style={{ padding: 14, borderLeft: '3px solid var(--crit)' }}>
            <div style={{ color: 'var(--crit)', fontSize: 12 }}>Failed to load jobs: {err}</div>
          </div>
        )}
        {!err && !loading && visible.length === 0 && (
          <div className="mono" style={{ color: 'var(--ink-2)', padding: 12, fontSize: 11 }}>
            No jobs in this view.
          </div>
        )}
        {visible.map((j) => {
          const color =
            j.status === 'running' ? 'var(--accent)' :
            j.status === 'completed' || j.status === 'done' ? 'var(--ok)' :
            j.status === 'failed' || j.status === 'error' ? 'var(--nato-hostile)' : 'var(--ink-2)';
          const pct = j.pct ?? 0;
          return (
            <div key={`${j.raw_source}-${j.id}`} className="card" style={{ padding: 14 }}>
              <div className="job-row-head">
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>
                      {j.raw_source}#{j.id}
                    </span>
                    <span style={{ fontSize: 13, fontWeight: 500 }}>{j.title}</span>
                  </div>
                  <div
                    className="mono"
                    style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 3 }}
                  >
                    {j.model} · {j.stage} · {relativeTime(j.created_at)}
                  </div>
                </div>
                <div
                  style={{
                    textAlign: 'right',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                  }}
                >
                  <span
                    className="mono"
                    style={{ fontSize: 11, color, letterSpacing: '.08em' }}
                  >
                    {j.status.toUpperCase()}
                  </span>
                  <button className="btn xs ghost icon" type="button">
                    <MoreHorizontal size={11} />
                  </button>
                </div>
              </div>
              <div style={{ marginTop: 10, height: 3, background: 'var(--bg-3)' }}>
                <div style={{ width: `${pct * 100}%`, height: '100%', background: color }} />
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Models                                                             */
/* ------------------------------------------------------------------ */

// model_versions entries that are tuning flags rather than actual model
// identifiers — filtered out of the live model list.
const INFERENCE_FLAG_KEYS = new Set<string>([
  'sam3_weights_source',
  'sam3_mirror_repo_id',
  'sam3_compile_image',
  'sam3_compile_video',
  'sam3_batched_text',
  'sam3_batched_text_chunk_size',
  'sam3_category_threshold',
  'flash_attn_3',
]);

const INFERENCE_MODEL_LABELS: Record<string, { title: string; family: string }> = {
  sam3_image:        { title: 'SAM3 (image)',           family: 'Segmentation' },
  sam3_video:        { title: 'SAM3 (video)',           family: 'Tracking'     },
  dinov3_sat:        { title: 'DINOv3 SAT',             family: 'Embedding'    },
  prithvi_backbone:  { title: 'Prithvi backbone',       family: 'Geospatial'   },
  prithvi_flood:     { title: 'Prithvi flood',          family: 'Geospatial'   },
  prithvi_burn:      { title: 'Prithvi burn scars',     family: 'Geospatial'   },
  terramind:         { title: 'TerraMind',              family: 'Geospatial'   },
  yoloe_pf:          { title: 'YOLOe (prompt-free)',    family: 'Detection'    },
  yoloe_seg:         { title: 'YOLOe (segmentation)',   family: 'Detection'    },
};

type InferenceHealth = {
  model_loaded: boolean;
  current_profile: string | null;
  available_profiles?: string[];
  device?: string;
  gpu_model?: string;
  model_versions?: Record<string, string>;
  load_flags?: Record<string, boolean>;
};

type LiveModel = {
  key: string;
  title: string;
  family: string;
  version: string;
  status: 'LOADED' | 'ENABLED' | 'DISABLED' | 'AVAILABLE';
};

function buildLiveModels(h: InferenceHealth | null): LiveModel[] {
  if (!h?.model_versions) return [];
  const flags = h.load_flags || {};
  const loaded = !!h.model_loaded;
  const profile = (h.current_profile || '').toLowerCase();
  const out: LiveModel[] = [];
  for (const [key, version] of Object.entries(h.model_versions)) {
    if (INFERENCE_FLAG_KEYS.has(key)) continue;
    const meta = INFERENCE_MODEL_LABELS[key] ?? { title: key, family: '—' };

    // sam3 is the gateway model — its load state is driven by the active
    // inference profile rather than a per-key load_flag.
    let status: LiveModel['status'];
    if (key === 'sam3_image' || key === 'sam3_video') {
      status = loaded && (profile === 'fmv' || profile === 'imagery' || profile === 'all')
        ? 'LOADED'
        : 'AVAILABLE';
    } else if (key in flags) {
      status = flags[key] ? 'ENABLED' : 'DISABLED';
    } else {
      status = 'AVAILABLE';
    }

    out.push({ key, title: meta.title, family: meta.family, version, status });
  }
  return out;
}

function ModelsView({ onCount }: { onCount: (n: number) => void }) {
  const [health, setHealth] = useState<InferenceHealth | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await axios.get<InferenceHealth>(`${API_URL}/api/inference/health`);
      setHealth(r.data);
    } catch (e: any) {
      setErr(e?.message ?? String(e));
      setHealth(null);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const models = useMemo(() => buildLiveModels(health), [health]);

  useEffect(() => {
    onCount(models.length);
  }, [models.length, onCount]);

  const profile = health?.current_profile || '—';
  const device = health?.gpu_model || health?.device || '—';

  return (
    <>
      <ViewHeader
        title="AI models"
        sub={`${models.length} bundled · profile=${profile} · device=${device}`}
        actions={
          <button className="btn sm" type="button" onClick={load} title="Refresh">
            <RefreshCw size={12} />
          </button>
        }
      />
      <div className="sentinel-scroll" style={{ padding: 18 }}>
        {err && (
          <div className="card" style={{ padding: 14, borderLeft: '3px solid var(--crit)' }}>
            <div style={{ color: 'var(--crit)', fontSize: 12 }}>Failed to load models: {err}</div>
          </div>
        )}
        {!err && models.length === 0 && (
          <div className="mono" style={{ color: 'var(--ink-2)', padding: 12, fontSize: 11 }}>
            Inference service reported no models.
          </div>
        )}
        {models.length > 0 && (
          <div className="table-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Family</th>
                  <th>Version</th>
                  <th>Key</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {models.map((m) => {
                  const color =
                    m.status === 'LOADED' || m.status === 'ENABLED' ? 'var(--ok)'
                    : m.status === 'DISABLED' ? 'var(--crit)'
                    : 'var(--ink-2)';
                  return (
                    <tr key={m.key}>
                      <td style={{ fontWeight: 500 }}>{m.title}</td>
                      <td className="mono" style={{ color: 'var(--ink-2)', fontSize: 11 }}>{m.family}</td>
                      <td className="mono">{m.version}</td>
                      <td className="mono" style={{ color: 'var(--ink-2)', fontSize: 11 }}>{m.key}</td>
                      <td>
                        <span className="mono" style={{ fontSize: 10.5, color, letterSpacing: '.08em' }}>
                          {m.status}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Alerts                                                             */
/* ------------------------------------------------------------------ */

type AlertRow = {
  id: string;
  severity: 'high' | 'medium' | 'low' | string;
  title: string;
  source: string;
  detail?: string;
  at: string;
};

function AlertsView({ onCount }: { onCount: (n: number) => void }) {
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await axios.get<{ alerts?: AlertRow[] }>(`${API_URL}/api/alerts`);
      setAlerts(r.data.alerts ?? []);
    } catch (e: any) {
      setErr(e?.message ?? String(e));
      setAlerts([]);
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, 15000);
    return () => window.clearInterval(id);
  }, [load]);

  useEffect(() => {
    onCount(alerts.length);
  }, [alerts.length, onCount]);

  return (
    <>
      <ViewHeader
        title="Health alerts"
        sub={`${alerts.length} active · derived from /api/health + ingest failures`}
        actions={
          <button className="btn sm" type="button" onClick={load} title="Refresh">
            <RefreshCw size={12} />
          </button>
        }
      />
      <div
        className="sentinel-scroll"
        style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 8 }}
      >
        {err && (
          <div className="card" style={{ padding: 14, borderLeft: '3px solid var(--crit)' }}>
            <div style={{ color: 'var(--crit)', fontSize: 12 }}>Failed to load alerts: {err}</div>
          </div>
        )}
        {!err && alerts.length === 0 && (
          <div
            className="card"
            style={{ padding: 14, borderLeft: '3px solid var(--ok)', display: 'flex', alignItems: 'center', gap: 10 }}
          >
            <StatusDot tone="ok" pulse />
            <div style={{ fontSize: 13 }}>All systems nominal · no active alerts.</div>
          </div>
        )}
        {alerts.map((a) => {
          const tone =
            a.severity === 'high' ? 'var(--nato-hostile)' :
            a.severity === 'medium' ? 'var(--nato-unknown)' : 'var(--ink-2)';
          return (
            <div
              key={a.id}
              className="card"
              style={{ padding: 14, borderLeft: `3px solid ${tone}` }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <AlertTriangle size={16} style={{ color: tone }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{a.title}</div>
                  <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>
                    {a.source} · {relativeTime(a.at)}
                  </div>
                  {a.detail && (
                    <div
                      className="mono"
                      style={{
                        fontSize: 10.5,
                        color: 'var(--ink-2)',
                        marginTop: 6,
                        whiteSpace: 'pre-wrap',
                      }}
                    >
                      {a.detail}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

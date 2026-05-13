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
  { key: 'upload',     label: 'Upload imagery',   Icon: UploadIcon },
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
      style={{
        height: '100%',
        display: 'grid',
        gridTemplateColumns: '220px 1fr',
        gap: 1,
        background: 'var(--line)',
      }}
    >
      <nav
        className="panel"
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
        style={{
          background: 'var(--bg-0)',
          display: 'flex',
          flexDirection: 'column',
          minWidth: 0,
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
      style={{
        padding: '16px 22px',
        borderBottom: '1px solid var(--line)',
        display: 'flex',
        alignItems: 'flex-end',
        gap: 14,
      }}
    >
      <div>
        <div style={{ fontSize: 16, fontWeight: 600, lineHeight: 1.2 }}>{title}</div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', marginTop: 4 }}>
          {sub}
        </div>
      </div>
      <div style={{ flex: 1 }} />
      {actions}
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
  raw_source: 'analytics' | 'training';
};

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
      const [a, t] = await Promise.allSettled([
        axios.get<{ jobs?: any[] }>(`${API_URL}/api/analytics/jobs`),
        axios.get<{ jobs?: any[] }>(`${API_URL}/api/training/jobs`),
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
      setJobs([...aRows, ...tRows]);
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
        sub={`${jobs.length} jobs across analytics + training`}
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
        className="scroll"
        style={{ flex: 1, padding: 18, display: 'flex', flexDirection: 'column', gap: 8 }}
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
              <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 10 }}>
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

type ModelRow = {
  id: number;
  name: string;
  version: string;
  status: string;
  promoted: boolean;
  metrics?: Record<string, number> | null;
  created_at?: string;
};

function ModelsView({ onCount }: { onCount: (n: number) => void }) {
  const [models, setModels] = useState<ModelRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await axios.get<{ models?: ModelRow[] }>(`${API_URL}/api/models`);
      setModels(r.data.models ?? []);
    } catch (e: any) {
      setErr(e?.message ?? String(e));
      setModels([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    onCount(models.length);
  }, [models.length, onCount]);

  const promote = useCallback(
    async (id: number) => {
      setBusy(id);
      try {
        await axios.post(`${API_URL}/api/models/${id}/promote`);
        await load();
      } catch (e: any) {
        setErr(e?.message ?? String(e));
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  return (
    <>
      <ViewHeader
        title="AI models"
        sub={`${models.length} registered · POST /api/models/{id}/promote`}
        actions={
          <button className="btn sm" type="button" onClick={load} title="Refresh">
            <RefreshCw size={12} />
          </button>
        }
      />
      <div className="scroll" style={{ flex: 1, padding: 18 }}>
        {err && (
          <div className="card" style={{ padding: 14, borderLeft: '3px solid var(--crit)' }}>
            <div style={{ color: 'var(--crit)', fontSize: 12 }}>Failed to load models: {err}</div>
          </div>
        )}
        {!err && models.length === 0 && (
          <div className="mono" style={{ color: 'var(--ink-2)', padding: 12, fontSize: 11 }}>
            No models registered.  Promote one via POST /api/models/&lt;id&gt;/promote.
          </div>
        )}
        {models.length > 0 && (
          <table className="tbl">
            <thead>
              <tr>
                <th>Model</th>
                <th>Version</th>
                <th>Status</th>
                <th>Registered</th>
                <th>Promoted</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.id}>
                  <td style={{ fontWeight: 500 }}>{m.name}</td>
                  <td className="mono">{m.version}</td>
                  <td>
                    <span
                      className="mono"
                      style={{
                        fontSize: 10.5,
                        color: m.status === 'available' ? 'var(--ok)' : 'var(--ink-2)',
                        letterSpacing: '.08em',
                      }}
                    >
                      {(m.status || 'unknown').toUpperCase()}
                    </span>
                  </td>
                  <td className="mono">{relativeTime(m.created_at)}</td>
                  <td>
                    <span
                      className="mono"
                      style={{
                        fontSize: 10.5,
                        color: m.promoted ? 'var(--ok)' : 'var(--ink-2)',
                        letterSpacing: '.08em',
                      }}
                    >
                      {m.promoted ? 'PROMOTED' : 'CANDIDATE'}
                    </span>
                  </td>
                  <td>
                    {m.promoted ? (
                      <button className="btn xs ghost icon" type="button" title="Details">
                        <MoreHorizontal size={11} />
                      </button>
                    ) : (
                      <button
                        className="btn xs"
                        type="button"
                        disabled={busy === m.id}
                        onClick={() => promote(m.id)}
                      >
                        {busy === m.id ? 'Promoting…' : 'Promote'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
        className="scroll"
        style={{ flex: 1, padding: 18, display: 'flex', flexDirection: 'column', gap: 8 }}
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

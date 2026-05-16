/**
 * Admin · Health — system KPIs (GPU/VRAM/profile/mode + active req/CPU/RAM/disk),
 * per-GPU replica chips, and a per-component model table populated by the
 * inference-sam3 sidecar's live /health response (proxied via
 * GET /api/inference/dashboard, polled every 5 s).
 */

import axios from 'axios';
import { Cpu, RefreshCw, Zap } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Panel } from '../atoms';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

type Metrics = {
  requests?: number;
  errors?: number;
  last_request_ts?: number | null;
  p50_ms?: number | null;
  p95_ms?: number | null;
};

type ModelRow = {
  id?: string;
  name?: string;
  version?: string | null;
  sub_versions?: Record<string, string> | null;
  status?: 'online' | 'configured' | 'disabled' | 'offline' | string;
  requests?: number;
  errors?: number;
  last_request_ts?: number | null;
  p50_ms?: number | null;
  p95_ms?: number | null;
  submetrics?: Record<string, Metrics> | null;
};

type Replica = {
  device?: string;
  components?: Record<string, boolean | string[]>;
};

type SystemStats = {
  cpu_pct?: number | null;
  ram_used_gib?: number | null;
  ram_total_gib?: number | null;
  disk_used_gib?: number | null;
  disk_total_gib?: number | null;
  disk_path?: string | null;
};

type Dashboard = {
  gpu?: { model?: string; profile?: string; cuda_version?: string };
  mode?: string;
  inference_error?: string;
  device?: string | null;
  vram_total_gib?: number | null;
  vram_used_gib?: number | null;
  profile_loaded?: string | null;
  available_profiles?: string[];
  pool_size?: number;
  replicas?: Replica[];
  active_requests?: number;
  uptime_s?: number | null;
  system?: SystemStats;
  request_rate_60s?: number | null;
  models?: ModelRow[];
};

// Component slugs displayed as chips in the replicas panel. Order matters —
// keep aligned with backend `_COMPONENT_ROWS` so the chip strip reads
// the same left-to-right on every replica.
const REPLICA_CHIPS: { slug: string; label: string }[] = [
  { slug: 'sam3_image', label: 'sam3-img' },
  { slug: 'sam3_video', label: 'sam3-vid' },
  { slug: 'dinov3_sat', label: 'dinov3' },
  { slug: 'prithvi', label: 'prithvi' },
  { slug: 'terramind', label: 'terramind' },
  { slug: 'dota_obb', label: 'dota-obb' },
  { slug: 'grounding_dino', label: 'gnd-dino' },
  { slug: 'yoloe_pf', label: 'yoloe-pf' },
  { slug: 'yoloe_seg', label: 'yoloe-seg' },
];

export default function HealthDashboardView() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await axios.get<Dashboard>(`${API_URL}/api/inference/dashboard`);
      setData(data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'failed to load dashboard');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, 5000);
    return () => window.clearInterval(id);
  }, [load]);

  const models = data?.models || [];
  const vramUsed = Number(data?.vram_used_gib ?? 0);
  const vramTotal = Number(data?.vram_total_gib ?? 0);
  const vramPct = vramTotal > 0 ? vramUsed / vramTotal : 0;

  const onlineCount = models.filter((m) => m.status === 'online').length;
  const configuredCount = models.filter((m) => m.status === 'configured').length;
  const disabledCount = models.filter((m) => m.status === 'disabled').length;
  const offlineCount = models.filter((m) => m.status === 'offline').length;
  const totalCount = models.length;

  const sys = data?.system || {};
  const ramUsed = Number(sys.ram_used_gib ?? 0);
  const ramTotal = Number(sys.ram_total_gib ?? 0);
  const ramPct = ramTotal > 0 ? ramUsed / ramTotal : 0;
  const diskUsed = Number(sys.disk_used_gib ?? 0);
  const diskTotal = Number(sys.disk_total_gib ?? 0);
  const diskPct = diskTotal > 0 ? diskUsed / diskTotal : 0;
  const cpuPct = sys.cpu_pct == null ? null : Number(sys.cpu_pct);

  return (
    <div className="admin-view" style={{ padding: '20px 24px', overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 18, flex: 1, minHeight: 0 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 14 }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>System health</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', marginTop: 4 }}>
            GET /api/inference/dashboard · live · auto-refresh every 5 s
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn sm" onClick={load} disabled={busy}>
          <RefreshCw size={12} /> {busy ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {error && (
        <div
          style={{
            padding: '8px 12px',
            border: '1px solid var(--nato-hostile)',
            background: 'color-mix(in oklab, var(--nato-hostile) 12%, var(--bg-2))',
            color: 'var(--nato-hostile)',
            fontFamily: 'var(--font-mono)',
            fontSize: 11.5,
          }}
        >
          {error}
        </div>
      )}

      {/* Row 1 — GPU / VRAM / Profile / Mode */}
      <div className="admin-grid-4">
        <KPI
          title="GPU"
          big={data?.gpu?.model || data?.gpu?.profile || '—'}
          sub={`${data?.gpu?.profile || 'cpu'} · CUDA ${data?.gpu?.cuda_version || 'n/a'}`}
          status={data?.gpu?.profile && data.gpu.profile !== 'cpu' ? 'ok' : 'warn'}
        />
        <KPI
          title="VRAM"
          big={vramTotal > 0 ? `${vramUsed.toFixed(1)} / ${vramTotal.toFixed(1)} GiB` : `${vramUsed.toFixed(1)} GiB`}
          sub={
            vramTotal > 0
              ? `${Math.round(vramPct * 100)}% utilized · ${onlineCount}/${totalCount} components loaded`
              : `${onlineCount}/${totalCount} components loaded`
          }
          status={vramPct > 0.85 ? 'crit' : vramPct > 0.7 ? 'warn' : 'ok'}
        />
        <KPI
          title="Profile"
          big={(data?.profile_loaded || 'none').toUpperCase()}
          sub={
            (data?.available_profiles || []).length
              ? `available: ${(data?.available_profiles || []).join(' · ')}`
              : 'no profiles registered'
          }
          status={data?.profile_loaded ? 'ok' : 'warn'}
        />
        <KPI
          title="Mode"
          big={(data?.mode || 'unknown').toUpperCase()}
          sub={
            data?.inference_error
              ? `Sidecar: ${data.inference_error}`
              : data?.device
                ? `device ${data.device} · uptime ${fmtUptime(data?.uptime_s)}`
                : `uptime ${fmtUptime(data?.uptime_s)}`
          }
          status={data?.inference_error ? 'crit' : 'ok'}
        />
      </div>

      {/* Row 2 — throughput / host */}
      <div className="admin-grid-4">
        <KPI
          title="Active req"
          big={String(data?.active_requests ?? 0)}
          sub={`${fmtRate(data?.request_rate_60s)} req/s · uptime ${fmtUptime(data?.uptime_s)}`}
          status={(data?.active_requests ?? 0) > 0 ? 'ok' : 'idle'}
        />
        <KPI
          title="CPU"
          big={cpuPct == null ? '—' : `${cpuPct.toFixed(1)}%`}
          sub="host load"
          status={cpuPct != null && cpuPct > 85 ? 'warn' : 'ok'}
        />
        <KPI
          title="RAM"
          big={ramTotal > 0 ? `${ramUsed.toFixed(1)} / ${ramTotal.toFixed(1)} GiB` : '—'}
          sub={ramTotal > 0 ? `${Math.round(ramPct * 100)}% utilized` : 'unavailable'}
          status={ramPct > 0.9 ? 'warn' : 'ok'}
        />
        <KPI
          title="Disk"
          big={diskTotal > 0 ? `${diskUsed.toFixed(1)} / ${diskTotal.toFixed(1)} GiB` : '—'}
          sub={sys.disk_path ? sys.disk_path : 'unavailable'}
          status={diskPct > 0.9 ? 'warn' : 'ok'}
        />
      </div>

      {/* Replicas — per-GPU bundle map */}
      <Panel
        title={`Replicas (${data?.pool_size ?? 0})`}
        sub={
          data?.profile_loaded
            ? `profile ${data.profile_loaded} active across ${data?.pool_size ?? 0} bundle(s)`
            : 'no profile loaded'
        }
      >
        {(data?.replicas || []).length === 0 ? (
          <div className="mono" style={{ padding: '10px 4px', color: 'var(--ink-3)', fontSize: 11.5 }}>
            No profile loaded · POST /api/inference/load?profile=fmv|imagery to start
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {(data?.replicas || []).map((r, i) => (
              <div key={(r.device || 'r') + '-' + i} style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <div
                  className="mono"
                  style={{
                    fontSize: 11,
                    minWidth: 70,
                    color: 'var(--ink-1)',
                    border: '1px solid var(--line)',
                    padding: '3px 8px',
                    borderRadius: 4,
                    background: 'var(--bg-2)',
                  }}
                >
                  {r.device || `replica ${i}`}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {REPLICA_CHIPS.map((chip) => {
                    const loaded = Boolean((r.components || {})[chip.slug]);
                    return (
                      <span
                        key={chip.slug}
                        className="mono"
                        title={chip.slug + (loaded ? ' · loaded' : ' · not loaded')}
                        style={{
                          fontSize: 10,
                          padding: '3px 7px',
                          borderRadius: 4,
                          letterSpacing: '.06em',
                          color: loaded ? 'var(--bg-0)' : 'var(--ink-3)',
                          background: loaded ? 'var(--ok)' : 'var(--bg-2)',
                          border: `1px solid ${loaded ? 'var(--ok)' : 'var(--line)'}`,
                        }}
                      >
                        {chip.label}
                      </span>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel
        title="Loaded models"
        sub={`${onlineCount} online · ${configuredCount} configured · ${disabledCount} disabled · ${offlineCount} offline (of ${totalCount})`}
        right={
          data?.inference_error ? (
            <span className="mono" style={{ color: 'var(--nato-hostile)', fontSize: 10.5 }}>
              <Zap size={11} style={{ verticalAlign: 'middle' }} /> sidecar down
            </span>
          ) : null
        }
      >
        <div className="health-model-grid">
          {['Model', 'Version', 'Status', 'Req', 'Err', 'p50 ms', 'p95 ms', 'Last used'].map((h) => (
            <div
              key={h}
              className="label-mono"
              style={{ padding: '8px 10px', borderBottom: '1px solid var(--line)' }}
            >
              {h}
            </div>
          ))}
          {models.map((m, i) => {
            const status = (m.status as string) || 'configured';
            const color = statusColor(status);
            return (
              <RowFragment key={(m.id || m.name || i) + '-' + i}>
                <Cell>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Cpu size={11} style={{ color: 'var(--ink-3)' }} />
                    <span style={{ color: 'var(--ink-0)' }}>{m.name || m.id || '—'}</span>
                    {m.id ? <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{`@${m.id}`}</span> : null}
                  </span>
                </Cell>
                <Cell mono>
                  {m.sub_versions && Object.keys(m.sub_versions).length > 0 ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                      {Object.entries(m.sub_versions).map(([k, v]) => (
                        <span key={k} style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>
                          <span style={{ color: 'var(--ink-3)' }}>{k.replace('yoloe_', '')}:</span> {v}
                        </span>
                      ))}
                    </div>
                  ) : (
                    m.version || '—'
                  )}
                </Cell>
                <Cell>
                  <span className="mono" style={{ fontSize: 10.5, color, letterSpacing: '.08em' }}>
                    {status.toUpperCase()}
                  </span>
                </Cell>
                <Cell mono>{m.requests ?? 0}</Cell>
                <Cell mono style={{ color: (m.errors ?? 0) > 0 ? 'var(--nato-hostile)' : undefined }}>
                  {m.errors ?? 0}
                </Cell>
                <Cell mono>{m.p50_ms != null ? Math.round(Number(m.p50_ms)) : '—'}</Cell>
                <Cell mono>{m.p95_ms != null ? Math.round(Number(m.p95_ms)) : '—'}</Cell>
                <Cell mono>{fmtAgo(m.last_request_ts)}</Cell>
              </RowFragment>
            );
          })}
          {models.length === 0 && (
            <div
              style={{
                gridColumn: '1 / -1',
                padding: '12px 10px',
                color: 'var(--ink-3)',
                fontFamily: 'var(--font-mono)',
                fontSize: 11.5,
              }}
            >
              No components registered.
            </div>
          )}
        </div>
      </Panel>
    </div>
  );
}

type KPIStatus = 'ok' | 'warn' | 'crit' | 'idle';

function KPI({ title, big, sub, status }: { title: string; big: string; sub: string; status: KPIStatus }) {
  const c =
    status === 'ok'
      ? 'var(--ok)'
      : status === 'warn'
        ? 'var(--nato-unknown)'
        : status === 'crit'
          ? 'var(--nato-hostile)'
          : 'var(--ink-3)';
  return (
    <Panel>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{ width: 6, height: 6, background: c, borderRadius: 999 }} />
        <span className="label-mono">{title}</span>
      </div>
      <div style={{ fontSize: 22, fontWeight: 600 }}>{big}</div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', marginTop: 6, wordBreak: 'break-word' }}>
        {sub}
      </div>
    </Panel>
  );
}

function RowFragment({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

function Cell({ children, mono, style }: { children: React.ReactNode; mono?: boolean; style?: React.CSSProperties }) {
  return (
    <div
      className={mono ? 'mono' : undefined}
      style={{
        padding: '9px 10px',
        borderBottom: '1px solid var(--line)',
        fontSize: mono ? 11 : 12.5,
        color: 'var(--ink-1)',
        display: 'flex',
        alignItems: 'center',
        ...(style || {}),
      }}
    >
      {children}
    </div>
  );
}

function statusColor(status: string): string {
  switch (status) {
    case 'online':
      return 'var(--ok)';
    case 'configured':
      return 'var(--warn)';
    case 'disabled':
      return 'var(--ink-3)';
    case 'offline':
      return 'var(--nato-hostile)';
    default:
      return 'var(--ink-3)';
  }
}

function fmtUptime(s?: number | null): string {
  if (s == null) return '—';
  const total = Math.max(0, Math.floor(s));
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${total}s`;
}

function fmtRate(r?: number | null): string {
  if (r == null) return '0.00';
  return Number(r).toFixed(2);
}

function fmtAgo(ts?: number | null): string {
  if (!ts) return '—';
  const dt = Math.max(0, Date.now() / 1000 - Number(ts));
  if (dt < 60) return `${Math.round(dt)}s ago`;
  if (dt < 3600) return `${Math.round(dt / 60)}m ago`;
  if (dt < 86400) return `${Math.round(dt / 3600)}h ago`;
  return `${Math.round(dt / 86400)}d ago`;
}

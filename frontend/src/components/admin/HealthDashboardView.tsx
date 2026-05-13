/**
 * Admin · Health — KPIs (GPU/VRAM/Mode) + loaded-models table.
 *
 * Reads /api/inference/dashboard every 5 s. The sidecar populates models +
 * VRAM when reachable; falls back to the env-declared model list so the
 * analyst still sees which heads are wired even when SAM3 is asleep.
 */

import axios from 'axios';
import { Cpu, RefreshCw, Zap } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Panel } from '../atoms';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

type ModelRow = {
  id?: string;
  name?: string;
  version?: string;
  vram?: number;
  vram_gib?: number;
  p50_ms?: number;
  latency_p50_ms?: number;
  status?: string;
};

type Dashboard = {
  gpu?: { model?: string; profile?: string; cuda_version?: string };
  vram_total_gib?: number | null;
  vram_used_gib?: number | null;
  models?: ModelRow[];
  mode?: string;
  device?: string;
  profile_loaded?: string;
  inference_error?: string;
};

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

  const vramUsed = Number(data?.vram_used_gib ?? 0);
  const vramTotal = Number(data?.vram_total_gib ?? 0);
  const onlineCount = (data?.models || []).filter((m) => (m.status || '').toLowerCase() === 'online').length;
  const totalCount = (data?.models || []).length;

  return (
    <div style={{ padding: '20px 24px', overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 18 }}>
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

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14 }}>
        <KPI
          title="GPU"
          big={data?.gpu?.model || data?.gpu?.profile || '—'}
          sub={`${data?.gpu?.profile || 'cpu'} · CUDA ${data?.gpu?.cuda_version || 'n/a'}`}
          status={data?.gpu?.profile && data.gpu.profile !== 'cpu' ? 'ok' : 'warn'}
        />
        <KPI
          title="VRAM"
          big={
            vramTotal > 0
              ? `${vramUsed.toFixed(1)} / ${vramTotal.toFixed(1)} GiB`
              : `${vramUsed.toFixed(1)} GiB`
          }
          sub={
            vramTotal > 0
              ? `${Math.round((vramUsed / vramTotal) * 100)}% utilized · ${onlineCount}/${totalCount} models loaded`
              : `${onlineCount}/${totalCount} models loaded`
          }
          status={vramTotal > 0 && vramUsed / vramTotal > 0.85 ? 'crit' : 'ok'}
        />
        <KPI
          title="Mode"
          big={(data?.mode || 'unknown').toUpperCase()}
          sub={
            data?.inference_error
              ? `Sidecar: ${data.inference_error}`
              : data?.profile_loaded
                ? `profile ${data.profile_loaded}`
                : data?.device
                  ? `device ${data.device}`
                  : 'SAM3 sidecar reachable'
          }
          status={data?.inference_error ? 'crit' : 'ok'}
        />
      </div>

      <Panel
        title="Loaded models"
        sub={`${totalCount} registered · ${onlineCount} online · ${Math.max(0, totalCount - onlineCount)} offline`}
        right={
          data?.inference_error ? (
            <span className="mono" style={{ color: 'var(--nato-hostile)', fontSize: 10.5 }}>
              <Zap size={11} style={{ verticalAlign: 'middle' }} /> sidecar down
            </span>
          ) : null
        }
      >
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 120px 80px 90px 90px 90px', gap: 0 }}>
          {['Model', 'Version', 'VRAM', 'p50 ms', 'Status', ''].map((h) => (
            <div
              key={h}
              className="label-mono"
              style={{ padding: '8px 10px', borderBottom: '1px solid var(--line)' }}
            >
              {h}
            </div>
          ))}
          {(data?.models || []).map((m, i) => {
            const status = (m.status || '').toLowerCase() || 'configured';
            const color =
              status === 'online' ? 'var(--ok)' : status === 'offline' ? 'var(--nato-hostile)' : 'var(--ink-3)';
            const vram = m.vram_gib ?? m.vram;
            const p50 = m.latency_p50_ms ?? m.p50_ms;
            return (
              <RowFragment key={(m.id || m.name || i) + '-' + i}>
                <Cell>{m.name || m.id || '—'}</Cell>
                <Cell mono>{m.version || '—'}</Cell>
                <Cell mono>{vram != null ? `${Number(vram).toFixed(1)} GB` : '—'}</Cell>
                <Cell mono>{p50 != null ? `${Math.round(Number(p50))}` : '—'}</Cell>
                <Cell>
                  <span className="mono" style={{ fontSize: 10.5, color, letterSpacing: '.08em' }}>
                    {status.toUpperCase()}
                  </span>
                </Cell>
                <Cell>
                  <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                    <Cpu size={10} style={{ verticalAlign: 'middle' }} /> {m.id || ''}
                  </span>
                </Cell>
              </RowFragment>
            );
          })}
          {(data?.models || []).length === 0 && (
            <div
              style={{
                gridColumn: '1 / -1',
                padding: '12px 10px',
                color: 'var(--ink-3)',
                fontFamily: 'var(--font-mono)',
                fontSize: 11.5,
              }}
            >
              No models registered.
            </div>
          )}
        </div>
      </Panel>
    </div>
  );
}

function KPI({ title, big, sub, status }: { title: string; big: string; sub: string; status: 'ok' | 'warn' | 'crit' }) {
  const c = status === 'ok' ? 'var(--ok)' : status === 'warn' ? 'var(--nato-unknown)' : 'var(--nato-hostile)';
  return (
    <Panel>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{ width: 6, height: 6, background: c, borderRadius: 999 }} />
        <span className="label-mono">{title}</span>
      </div>
      <div style={{ fontSize: 22, fontWeight: 600 }}>{big}</div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', marginTop: 6 }}>
        {sub}
      </div>
    </Panel>
  );
}

function RowFragment({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

function Cell({ children, mono }: { children: React.ReactNode; mono?: boolean }) {
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
      }}
    >
      {children}
    </div>
  );
}

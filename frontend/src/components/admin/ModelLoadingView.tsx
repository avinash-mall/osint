/**
 * ModelLoadingView — Admin · model loading.
 *
 * UX-AUDIT F27/F28: a dedicated panel for loading inference model weights
 * into VRAM and freeing them again, backed by
 * `/api/inference/{dashboard,load,unload}`.
 *
 * - The destructive global unload is gated behind a `ConfirmDialog` (F27);
 *   the inference container restarts on unload, so in-flight jobs fail.
 * - Flag-gated (`disabled`) models render as `NEEDS SETUP` with the neutral
 *   `setup` tag, not a red fault tag (F28) — a disabled model is a
 *   configuration step, not an error.
 */

import axios from 'axios';
import { useCallback, useEffect, useState } from 'react';
import { Cpu, Key, RefreshCw } from 'lucide-react';
import ViewHeader from './ViewHeader';
import { ConfirmDialog } from '../atoms';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

type ModelRow = {
  id: string;
  name: string;
  version?: string | null;
  status: string;
  requests?: number;
  errors?: number;
};

type Dashboard = {
  models?: ModelRow[];
  vram_used_gib?: number | null;
  vram_total_gib?: number | null;
  profile_loaded?: string | null;
  available_profiles?: string[];
};

// Maps the inference-sam3 dashboard status onto an operator-facing label +
// tag tone. `disabled` is a setup step (`setup` tone), not a fault.
const STATUS_META: Record<string, { label: string; tone: string }> = {
  online:     { label: 'LOADED',      tone: 'ok' },
  configured: { label: 'READY',       tone: 'info' },
  disabled:   { label: 'NEEDS SETUP', tone: 'setup' },
  offline:    { label: 'OFFLINE',     tone: 'crit' },
};

export default function ModelLoadingView() {
  const [data, setData] = useState<Dashboard>({});
  const [err, setErr] = useState<string | null>(null);
  const [profile, setProfile] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmUnload, setConfirmUnload] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await axios.get<Dashboard>(`${API_URL}/api/inference/dashboard`);
      setData(r.data || {});
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const models = data.models ?? [];
  const loadedCount = models.filter((m) => m.status === 'online').length;
  const vramUsed = Number(data.vram_used_gib ?? 0);
  const vramTotal = Number(data.vram_total_gib ?? 0);

  const doLoad = useCallback(async () => {
    if (!profile) return;
    setBusy(true);
    setErr(null);
    try {
      await axios.post(`${API_URL}/api/inference/load`, null, { params: { profile } });
      await load();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }, [profile, load]);

  const doUnload = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      await axios.post(`${API_URL}/api/inference/unload`);
      await load();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? e?.message ?? String(e));
    } finally {
      setBusy(false);
      setConfirmUnload(false);
    }
  }, [load]);

  return (
    <>
      <ViewHeader
        title="Model loading"
        sub={`${loadedCount}/${models.length} loaded · ${
          vramTotal ? `${vramUsed.toFixed(1)}/${vramTotal.toFixed(1)} GiB VRAM` : 'VRAM n/a'
        }`}
        actions={
          <button className="btn sm" type="button" onClick={load} aria-label="Refresh model status">
            <RefreshCw size={12}/>
          </button>
        }
      />
      <div className="scroll" style={{ flex: 1, padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
        {err && (
          <div className="card" role="alert" style={{ padding: 12, borderLeft: '3px solid var(--crit)' }}>
            <div style={{ color: 'var(--crit)', fontSize: 12 }}>{err}</div>
          </div>
        )}

        <div className="card" style={{ padding: 14, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <Cpu size={14} style={{ color: 'var(--accent)' }}/>
          <span style={{ fontSize: 12, fontWeight: 600 }}>Load profile</span>
          <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>
            current · {data.profile_loaded || 'none'}
          </span>
          <span style={{ flex: 1 }}/>
          <select
            className="input"
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
            disabled={busy}
            aria-label="Inference profile"
          >
            <option value="">Select profile…</option>
            {(data.available_profiles ?? []).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <button className="btn sm primary" type="button" onClick={doLoad} disabled={!profile || busy}>
            {busy ? 'Working…' : 'Load'}
          </button>
          <button
            className="btn sm danger" type="button"
            onClick={() => setConfirmUnload(true)} disabled={busy}
          >
            Unload all
          </button>
        </div>

        <table className="tbl">
          <thead>
            <tr>
              <th>Model</th>
              <th>Version</th>
              <th>Status</th>
              <th>Requests</th>
              <th aria-label="setup hint"/>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => {
              const meta = STATUS_META[m.status] || { label: m.status.toUpperCase(), tone: 'info' };
              return (
                <tr key={m.id}>
                  <td style={{ fontWeight: 500 }}>{m.name}</td>
                  <td className="mono">{m.version || '—'}</td>
                  <td>
                    <span className={`tag ${meta.tone}`}>
                      {meta.tone === 'setup' && <Key size={10}/>}
                      {meta.label}
                    </span>
                  </td>
                  <td className="mono">{m.requests ?? 0}</td>
                  <td>
                    {m.status === 'disabled' && (
                      <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                        enable its load flag in deployment .env
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
            {models.length === 0 && !err && (
              <tr>
                <td colSpan={5} className="mono" style={{ color: 'var(--ink-2)', fontSize: 11 }}>
                  Inference service not reporting models.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {confirmUnload && (
        <ConfirmDialog
          title="Unload all inference models?"
          body={(
            <p>
              Evicts every model's weights from VRAM and restarts the inference
              container. Any in-flight inference jobs will fail and must be
              re-run once the service is back up.
            </p>
          )}
          confirmLabel="Unload models"
          destructive
          busy={busy}
          onConfirm={doUnload}
          onClose={() => setConfirmUnload(false)}
        />
      )}
    </>
  );
}

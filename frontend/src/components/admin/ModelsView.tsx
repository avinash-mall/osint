/**
 * ModelsView — Admin · AI models tab.
 *
 * Extracted from the monolithic AdminScreen.tsx. Lists registered models
 * and offers one-click promote.
 */

import axios from 'axios';
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import ViewHeader from './ViewHeader';
import { relativeTime } from './time';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

type ModelRow = {
  id: number;
  name: string;
  version: string;
  status: string;
  promoted: boolean;
  metrics?: Record<string, number> | null;
  created_at?: string;
};

type Props = { onCount: (n: number) => void };

export default function ModelsView({ onCount }: Props) {
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

  useEffect(() => { load(); }, [load]);
  useEffect(() => { onCount(models.length); }, [models.length, onCount]);

  const promote = useCallback(async (id: number) => {
    setBusy(id);
    try {
      await axios.post(`${API_URL}/api/models/${id}/promote`);
      await load();
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    } finally {
      setBusy(null);
    }
  }, [load]);

  return (
    <>
      <ViewHeader
        title="AI models"
        sub={`${models.length} registered · POST /api/models/{id}/promote`}
        actions={
          <button className="btn sm" type="button" onClick={load} aria-label="Refresh models">
            <RefreshCw size={12}/>
          </button>
        }
      />
      <div className="scroll admin-models-table" style={{
        flex: 1, padding: 18,
        containerType: 'inline-size', containerName: 'models-table',
      }}>
        {err && (
          <div className="card" role="alert" style={{ padding: 14, borderLeft: '3px solid var(--crit)' }}>
            <div style={{ color: 'var(--crit)', fontSize: 12 }}>Failed to load models: {err}</div>
          </div>
        )}
        {!err && models.length === 0 && (
          <div className="mono" style={{ color: 'var(--ink-2)', padding: 12, fontSize: 11 }}>
            No models registered. Promote one via POST /api/models/&lt;id&gt;/promote.
          </div>
        )}
        {models.length > 0 && (
          <table className="tbl models-tbl">
            <thead>
              <tr>
                <th>Model</th>
                <th>Version</th>
                <th>Status</th>
                <th>Registered</th>
                <th>Promoted</th>
                <th aria-label="actions"/>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.id}>
                  <td style={{ fontWeight: 500 }}>{m.name}</td>
                  <td className="mono">{m.version}</td>
                  <td>
                    <span className="mono" style={{
                      fontSize: 10.5,
                      color: m.status === 'available' ? 'var(--ok)' : 'var(--ink-2)',
                      letterSpacing: '.08em',
                    }}>
                      {(m.status || 'unknown').toUpperCase()}
                    </span>
                  </td>
                  <td className="mono">{relativeTime(m.created_at)}</td>
                  <td>
                    <span className="mono" style={{
                      fontSize: 10.5,
                      color: m.promoted ? 'var(--ok)' : 'var(--ink-2)',
                      letterSpacing: '.08em',
                    }}>
                      {m.promoted ? 'PROMOTED' : 'CANDIDATE'}
                    </span>
                  </td>
                  <td>
                    {m.promoted ? (
                      <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>—</span>
                    ) : (
                      <button
                        className="btn xs" type="button"
                        disabled={busy === m.id} aria-busy={busy === m.id}
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

/**
 * Admin · Taxonomy version history.
 *
 * Read-only changelog of every ``ontology_version.version_id`` bump. The
 * current version is highlighted with the accent color so analysts know
 * which row their detections are pinned to right now.
 */

import axios from 'axios';
import { History, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Panel } from '../atoms';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

type VersionRow = {
  id: number;
  version_id: number;
  summary?: string;
  changes?: Record<string, unknown>;
  detections_at_cut?: number;
  created_at: string;
  created_by?: string;
};

type Resp = {
  current_version_id: number | null;
  versions: VersionRow[];
};

export default function TaxonomyVersionView() {
  const [data, setData] = useState<Resp | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await axios.get<Resp>(`${API_URL}/api/ontology/version-history`);
      setData(data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'failed to load');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div style={{ padding: '20px 24px', overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 18, flex: 1, minHeight: 0 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 14 }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>Taxonomy version history</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', marginTop: 4 }}>
            Every detection records the taxonomy version active at infer time.
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)' }}>
          Current version <b style={{ color: 'var(--accent)' }}>v{data?.current_version_id ?? '—'}</b>
        </span>
        <button type="button" className="btn sm" onClick={load} disabled={busy}>
          <RefreshCw size={12} /> Reload
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

      <Panel title="Changelog" sub={`${data?.versions.length ?? 0} bumps logged`}>
        {(!data || data.versions.length === 0) && (
          <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
            No version-history rows yet. Each ontology edit appends a row.
          </div>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
          {(data?.versions || []).map((v) => {
            const isCurrent = data?.current_version_id === v.version_id;
            return (
              <div
                key={v.id}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '90px 1fr 140px 140px',
                  gap: 10,
                  padding: '12px 10px',
                  borderBottom: '1px solid var(--line)',
                  background: isCurrent ? 'color-mix(in oklab, var(--accent) 8%, transparent)' : 'transparent',
                  borderLeft: isCurrent ? '3px solid var(--accent)' : '3px solid transparent',
                }}
              >
                <div className="mono" style={{ fontSize: 11.5, color: isCurrent ? 'var(--accent)' : 'var(--ink-1)' }}>
                  <History size={11} style={{ verticalAlign: 'middle' }} /> v{v.version_id}
                </div>
                <div>
                  <div style={{ fontSize: 12.5 }}>{v.summary || '(no summary)'}</div>
                  {v.changes && Object.keys(v.changes).length > 0 && (
                    <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 4 }}>
                      {Object.entries(v.changes)
                        .map(([k, val]) => `${k}=${typeof val === 'object' ? JSON.stringify(val) : val}`)
                        .join(' · ')}
                    </div>
                  )}
                </div>
                <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>
                  {v.detections_at_cut != null ? `${v.detections_at_cut.toLocaleString()} dets` : '—'}
                </div>
                <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>
                  {new Date(v.created_at).toLocaleString()} · {v.created_by || '—'}
                </div>
              </div>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}

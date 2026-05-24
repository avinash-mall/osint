import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { CheckCircle2, Plus, RefreshCw, Trash2 } from 'lucide-react';

const API_URL = import.meta.env.VITE_API_URL || '';

type Kind = 'base' | 'launchpoint' | 'facility';
const KINDS: Kind[] = ['base', 'launchpoint', 'facility'];

interface Threshold {
  id: number;
  kind: Kind;
  window_days: number;
  min_count: number;
  near_radius_m: number;
  current: boolean;
  notes: string | null;
  created_at: string;
  created_by: string | null;
}

const DEFAULTS: Record<Kind, { window_days: number; min_count: number; near_radius_m: number }> = {
  base: { window_days: 30, min_count: 5, near_radius_m: 5000 },
  launchpoint: { window_days: 30, min_count: 5, near_radius_m: 2000 },
  facility: { window_days: 30, min_count: 5, near_radius_m: 1000 },
};

export default function RepeatThresholdsView() {
  const [rows, setRows] = useState<Threshold[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newKind, setNewKind] = useState<Kind>('base');
  const [newWindowDays, setNewWindowDays] = useState(30);
  const [newMinCount, setNewMinCount] = useState(5);
  const [newRadiusM, setNewRadiusM] = useState(5000);
  const [newNotes, setNewNotes] = useState('');

  const reload = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const r = await axios.get(`${API_URL}/api/admin/repeat-thresholds`);
      setRows(r.data.thresholds || []);
    } catch (err: any) {
      setError(err?.message || 'load failed');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { reload().catch(() => {}); }, [reload]);

  // Sync the radius default when the analyst picks a different kind in the
  // create form, so a launchpoint defaults to 2000m, not 5000m.
  useEffect(() => {
    setNewRadiusM(DEFAULTS[newKind].near_radius_m);
  }, [newKind]);

  const create = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      await axios.post(`${API_URL}/api/admin/repeat-thresholds`, {
        kind: newKind,
        window_days: newWindowDays,
        min_count: newMinCount,
        near_radius_m: newRadiusM,
        notes: newNotes || undefined,
        make_current: true,
      });
      setNewNotes('');
      await reload();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'create failed');
    } finally {
      setBusy(false);
    }
  }, [newKind, newWindowDays, newMinCount, newRadiusM, newNotes, reload]);

  const activate = useCallback(async (id: number) => {
    setBusy(true); setError(null);
    try {
      await axios.put(`${API_URL}/api/admin/repeat-thresholds/${id}/activate`);
      await reload();
    } catch (err: any) {
      setError(err?.message || 'activate failed');
    } finally {
      setBusy(false);
    }
  }, [reload]);

  const remove = useCallback(async (id: number) => {
    if (!confirm(`Delete threshold ${id}?`)) return;
    setBusy(true); setError(null);
    try {
      await axios.delete(`${API_URL}/api/admin/repeat-thresholds/${id}`);
      await reload();
    } catch (err: any) {
      setError(err?.message || 'delete failed');
    } finally {
      setBusy(false);
    }
  }, [reload]);

  const grouped = useMemo(() => {
    const out: Record<Kind, Threshold[]> = { base: [], launchpoint: [], facility: [] };
    for (const r of rows) {
      if (out[r.kind]) out[r.kind].push(r);
    }
    return out;
  }, [rows]);

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 340px', gap: 8, padding: 8, height: '100%', overflow: 'hidden' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0 }}>
        <div className="panel" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px' }}>
          <span className="h-title">REPEATED_AT / NEAR thresholds</span>
          <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', marginLeft: 12 }}>
            tick_near_builder + tick_repeat_detector read these per-kind values
          </span>
          <button type="button" onClick={reload} className="sentinel-btn" style={{ marginLeft: 'auto' }} disabled={busy}>
            <RefreshCw size={13} /> Refresh
          </button>
        </div>
        {error && (
          <div className="panel" style={{ padding: '6px 10px', color: 'var(--warn)', fontFamily: 'monospace', fontSize: 11 }}>{error}</div>
        )}
        <div className="panel" style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 8 }}>
          {KINDS.map((k) => (
            <div key={k} style={{ marginBottom: 12 }}>
              <div className="h-title" style={{ padding: '6px 4px', borderBottom: '1px solid var(--line)' }}>
                {k.toUpperCase()} <span style={{ fontSize: 10, color: 'var(--ink-3)', marginLeft: 8 }}>defaults: {DEFAULTS[k].window_days}d / ≥{DEFAULTS[k].min_count} / {DEFAULTS[k].near_radius_m}m</span>
              </div>
              {grouped[k].length === 0 ? (
                <div style={{ padding: 8, color: 'var(--ink-3)', fontFamily: 'monospace', fontSize: 11 }}>
                  no overrides — using env defaults
                </div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: '70px 90px 90px 90px minmax(0, 1fr) 90px 60px', gap: 4, padding: 4, fontSize: 11 }}>
                  <div className="h-title" style={{ fontSize: 10 }}>ID</div>
                  <div className="h-title" style={{ fontSize: 10 }}>Window</div>
                  <div className="h-title" style={{ fontSize: 10 }}>Min count</div>
                  <div className="h-title" style={{ fontSize: 10 }}>Radius (m)</div>
                  <div className="h-title" style={{ fontSize: 10 }}>Notes</div>
                  <div className="h-title" style={{ fontSize: 10 }}>Status</div>
                  <div></div>
                  {grouped[k].map((r) => (
                    <div key={r.id} style={{ display: 'contents' }}>
                      <div style={{ fontFamily: 'monospace' }}>{r.id}</div>
                      <div>{r.window_days}d</div>
                      <div>{r.min_count}</div>
                      <div>{r.near_radius_m}</div>
                      <div style={{ color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.notes || '—'}</div>
                      <div>
                        {r.current ? (
                          <span className="sentinel-tag acc" style={{ fontSize: 10 }}>CURRENT</span>
                        ) : (
                          <button type="button" onClick={() => activate(r.id)} className="sentinel-btn" style={{ padding: '2px 6px', fontSize: 10 }} disabled={busy}>
                            <CheckCircle2 size={10} /> Activate
                          </button>
                        )}
                      </div>
                      <button type="button" onClick={() => remove(r.id)} className="sentinel-btn" style={{ padding: '2px 6px' }} title="Delete" disabled={busy}>
                        <Trash2 size={11} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="panel" style={{ padding: 10, display: 'flex', flexDirection: 'column', gap: 6, alignSelf: 'start' }}>
        <div className="h-title">Add threshold (auto-activates)</div>
        <label style={{ fontSize: 10, color: 'var(--ink-3)' }}>Kind
          <select
            value={newKind}
            onChange={(e) => setNewKind(e.target.value as Kind)}
            style={{ width: '100%', marginTop: 4, background: 'var(--bg-1)', border: '1px solid var(--line)', color: 'var(--ink-1)', padding: '4px 6px', fontSize: 12 }}
          >
            {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 10, color: 'var(--ink-3)' }}>Window (days)
          <input type="number" min={1} max={3650} value={newWindowDays}
            onChange={(e) => setNewWindowDays(Number(e.target.value))}
            style={{ width: '100%', marginTop: 4, background: 'var(--bg-1)', border: '1px solid var(--line)', color: 'var(--ink-1)', padding: '4px 6px', fontSize: 12 }}
          />
        </label>
        <label style={{ fontSize: 10, color: 'var(--ink-3)' }}>Min count
          <input type="number" min={1} max={10000} value={newMinCount}
            onChange={(e) => setNewMinCount(Number(e.target.value))}
            style={{ width: '100%', marginTop: 4, background: 'var(--bg-1)', border: '1px solid var(--line)', color: 'var(--ink-1)', padding: '4px 6px', fontSize: 12 }}
          />
        </label>
        <label style={{ fontSize: 10, color: 'var(--ink-3)' }}>NEAR radius (m)
          <input type="number" min={10} max={50000} value={newRadiusM}
            onChange={(e) => setNewRadiusM(Number(e.target.value))}
            style={{ width: '100%', marginTop: 4, background: 'var(--bg-1)', border: '1px solid var(--line)', color: 'var(--ink-1)', padding: '4px 6px', fontSize: 12 }}
          />
        </label>
        <label style={{ fontSize: 10, color: 'var(--ink-3)' }}>Notes (optional)
          <input value={newNotes} onChange={(e) => setNewNotes(e.target.value)}
            style={{ width: '100%', marginTop: 4, background: 'var(--bg-1)', border: '1px solid var(--line)', color: 'var(--ink-1)', padding: '4px 6px', fontSize: 12 }}
          />
        </label>
        <button type="button" onClick={create} className="sentinel-btn primary" style={{ justifyContent: 'center', marginTop: 4 }} disabled={busy}>
          <Plus size={13} /> Add &amp; activate
        </button>
      </div>
    </div>
  );
}

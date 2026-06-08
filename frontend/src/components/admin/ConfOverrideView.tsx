/**
 * Admin · Confidence overrides — per-class threshold sliders.
 *
 * Reads /api/inference/confidence-overrides (env + DB), lets the admin replace
 * the DB-side overrides, then PUTs them. The active detection policy is
 * invalidated server-side so the next inference call uses the new values
 * without restarting.
 */

import axios from 'axios';
import { Plus, RefreshCw, Save, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Panel } from '../atoms';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

type OverridesPayload = {
  per_class_confidence_overrides: Record<string, number>;
  env_per_class_confidence_overrides?: Record<string, number>;
  global_floor: number | null;
  env_global_floor?: number | null;
  high_confidence_threshold: number | null;
  env_high_confidence_threshold?: number | null;
};

// `base` = the per-class env floor for this class (what inference falls back to
// without a DB override), or the global env floor when no per-class env value
// exists. Shown in the BASE column and used as the "raised above base" pivot.
type Row = { id: string; value: number; from_env: boolean; base: number };

function buildRows(payload: OverridesPayload): Row[] {
  const env = payload.env_per_class_confidence_overrides || {};
  const db = payload.per_class_confidence_overrides || {};
  const globalFloor = Number(payload.env_global_floor ?? 0);
  const baseFor = (k: string) => (env[k] != null ? Number(env[k]) : globalFloor);
  const seen = new Set<string>();
  const rows: Row[] = [];
  for (const [k, v] of Object.entries(db)) {
    rows.push({ id: k, value: Number(v), from_env: false, base: baseFor(k) });
    seen.add(k);
  }
  for (const [k, v] of Object.entries(env)) {
    if (seen.has(k)) continue;
    rows.push({ id: k, value: Number(v), from_env: true, base: baseFor(k) });
  }
  rows.sort((a, b) => a.id.localeCompare(b.id));
  return rows;
}

export default function ConfOverrideView() {
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [globalFloor, setGlobalFloor] = useState<string>('');
  const [envGlobalFloor, setEnvGlobalFloor] = useState<number | null>(null);
  const [highConf, setHighConf] = useState<string>('');
  const [envHighConf, setEnvHighConf] = useState<number | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newClass, setNewClass] = useState('');

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await axios.get<OverridesPayload>(
        `${API_URL}/api/inference/confidence-overrides`,
      );
      setRows(buildRows(data));
      setGlobalFloor(data.global_floor != null ? String(data.global_floor) : '');
      setEnvGlobalFloor(data.env_global_floor ?? null);
      setHighConf(data.high_confidence_threshold != null ? String(data.high_confidence_threshold) : '');
      setEnvHighConf(data.env_high_confidence_threshold ?? null);
      setLoaded(true);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'failed to load');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const updateRow = useCallback((id: string, value: number) => {
    setRows((cur) => cur.map((r) => (r.id === id ? { ...r, value, from_env: false } : r)));
  }, []);

  const removeRow = useCallback((id: string) => {
    setRows((cur) => cur.filter((r) => r.id !== id));
  }, []);

  const addRow = useCallback(() => {
    const id = newClass.trim().toLowerCase();
    if (!id) return;
    setRows((cur) => (cur.some((r) => r.id === id) ? cur : [...cur, { id, value: 0.5, from_env: false, base: Number(envGlobalFloor ?? 0) }]));
    setNewClass('');
  }, [newClass, envGlobalFloor]);

  const save = useCallback(async () => {
    setBusy(true);
    setError(null);
    setSavedAt(null);
    try {
      const overrides = Object.fromEntries(
        rows.filter((r) => !r.from_env).map((r) => [r.id, Math.max(0, Math.min(1, r.value))]),
      );
      const payload: any = {
        per_class_confidence_overrides: overrides,
        global_floor: globalFloor.trim() === '' ? null : Number(globalFloor),
        high_confidence_threshold: highConf.trim() === '' ? null : Number(highConf),
      };
      await axios.put(`${API_URL}/api/inference/confidence-overrides`, payload);
      setSavedAt(new Date().toISOString());
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'save failed');
    } finally {
      setBusy(false);
    }
  }, [rows, globalFloor, highConf, load]);

  return (
    <div className="admin-view" style={{ padding: '20px 24px', overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 18, flex: 1, minHeight: 0 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 14 }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>Confidence overrides</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', marginTop: 4 }}>
            Per-class detection thresholds · DB-stored · invalidates inference policy cache on save
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn sm" onClick={load} disabled={busy}>
          <RefreshCw size={12} /> Reload
        </button>
        <button type="button" className="btn primary" onClick={save} disabled={busy || !loaded}>
          <Save size={12} /> {busy ? 'Saving…' : 'Save overrides'}
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
      {savedAt && (
        <div className="mono" style={{ fontSize: 11, color: 'var(--ok)' }}>
          · saved {new Date(savedAt).toLocaleTimeString()}
        </div>
      )}

      <Panel title="Global thresholds" sub="Apply to every detection unless a per-class override below wins">
        <div className="conf-threshold-grid">
          <label className="label-mono">Global floor</label>
          <input
            type="number"
            step="0.01"
            min="0"
            max="1"
            value={globalFloor}
            onChange={(e) => setGlobalFloor(e.target.value)}
            placeholder={envGlobalFloor != null ? `env: ${envGlobalFloor}` : '0.0'}
            style={inputStyle}
          />
          <label className="label-mono">High-confidence threshold</label>
          <input
            type="number"
            step="0.01"
            min="0"
            max="1"
            value={highConf}
            onChange={(e) => setHighConf(e.target.value)}
            placeholder={envHighConf != null ? `env: ${envHighConf}` : '0.5'}
            style={inputStyle}
          />
        </div>
      </Panel>

      <Panel
        title="Per-class overrides"
        sub={`${rows.filter((r) => !r.from_env).length} DB · ${rows.filter((r) => r.from_env).length} env`}
        right={
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              value={newClass}
              onChange={(e) => setNewClass(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') addRow();
              }}
              placeholder="class label · e.g. destroyer"
              style={{ ...inputStyle, inlineSize: 'min(13.75rem, 100%)' }}
            />
            <button type="button" className="btn xs" onClick={addRow} disabled={!newClass.trim()}>
              <Plus size={11} /> Add
            </button>
          </div>
        }
      >
        <div className="conf-overrides-grid">
          {['CLASS', 'BASE', 'OVERRIDE', 'VALUE', ''].map((h) => (
            <div
              key={h}
              className="label-mono"
              style={{ padding: '8px 10px', borderBottom: '1px solid var(--line)' }}
            >
              {h}
            </div>
          ))}
          {rows.length === 0 && (
            <div
              className="mono"
              style={{ gridColumn: '1 / -1', padding: '12px 10px', color: 'var(--ink-3)', fontSize: 11.5 }}
            >
              No overrides defined. Add one above.
            </div>
          )}
          {rows.map((r) => (
            <Row key={r.id}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                <span style={{ fontWeight: 500 }}>{r.id}</span>
                {r.from_env && (
                  <span
                    className="mono"
                    style={{
                      fontSize: 9.5,
                      padding: '1px 5px',
                      color: 'var(--nato-unknown)',
                      border: '1px solid var(--nato-unknown)',
                      borderRadius: 2,
                    }}
                  >
                    ENV
                  </span>
                )}
              </div>
              <div className="mono" style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>
                {r.base.toFixed(2)}
              </div>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={r.value}
                onChange={(e) => updateRow(r.id, Number(e.target.value))}
                style={{ accentColor: 'var(--accent)', width: '100%' }}
              />
              <div
                className="mono"
                style={{
                  fontSize: 11.5,
                  fontWeight: 600,
                  color: r.value > r.base ? 'var(--accent)' : 'var(--ink-1)',
                }}
              >
                {r.value.toFixed(2)}
              </div>
              <button
                type="button"
                onClick={() => removeRow(r.id)}
                title="Remove override"
                style={{
                  background: 'transparent',
                  border: 0,
                  color: 'var(--ink-3)',
                  cursor: 'pointer',
                  padding: 4,
                }}
              >
                <Trash2 size={12} />
              </button>
            </Row>
          ))}
        </div>
      </Panel>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  background: 'var(--bg-2)',
  border: '1px solid var(--line)',
  color: 'var(--ink-0)',
  padding: '7px 10px',
  fontSize: 12.5,
  fontFamily: 'var(--font-sans)',
};

function Row({ children }: { children: React.ReactNode }) {
  return (
    <>
      {Array.isArray(children) ? children.map((c, i) => (
        <div
          key={i}
          style={{
            padding: '8px 10px',
            borderBottom: '1px solid var(--line)',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          {c}
        </div>
      )) : (
        <div style={{ gridColumn: '1 / -1' }}>{children}</div>
      )}
    </>
  );
}

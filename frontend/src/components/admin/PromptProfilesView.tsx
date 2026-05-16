/**
 * Admin · Prompt profiles per sensor.
 *
 * Versioned snapshots of the prompt list per sensor (optical, multispectral,
 * sar, hsi, fmv). One profile per sensor is "current". Admins can:
 *   - Create a new profile (sensor + version label + prompt list)
 *   - Activate an existing profile (POST /activate)
 *   - Delete a profile
 *
 * The "current" profile feeds the existing ontology.default_prompts() lookup
 * via a small change in ontology.py: when a sensor has a current profile,
 * prefer those prompts over the seeded ontology defaults.
 */

import axios from 'axios';
import { Check, ChevronDown, ChevronRight, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ModalityBadge, Panel } from '../atoms';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

const SENSOR_LIST = ['optical', 'multispectral', 'sar', 'hsi', 'fmv'] as const;
type Sensor = (typeof SENSOR_LIST)[number];
const SENSOR_TO_MODALITY: Record<Sensor, 'rgb' | 'multispectral' | 'sar' | 'hsi' | 'fmv'> = {
  optical: 'rgb',
  multispectral: 'multispectral',
  sar: 'sar',
  hsi: 'hsi',
  fmv: 'fmv',
};

type Profile = {
  id: number;
  sensor: string;
  name: string;
  version: string;
  prompts: string[];
  current: boolean;
  notes?: string;
  created_at: string;
  created_by?: string;
};

export default function PromptProfilesView() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [defaults, setDefaults] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  // Create-new form
  const [newSensor, setNewSensor] = useState<Sensor>('optical');
  const [newVersion, setNewVersion] = useState('');
  const [newName, setNewName] = useState('');
  const [newPromptText, setNewPromptText] = useState('');
  const [newMakeCurrent, setNewMakeCurrent] = useState(true);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await axios.get(`${API_URL}/api/ontology/prompt-profiles`);
      setProfiles(data?.profiles || []);
      setDefaults(data?.ontology_defaults || {});
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'failed to load profiles');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const createProfile = useCallback(async () => {
    if (!newVersion.trim() || !newName.trim()) {
      setError('name and version are required');
      return;
    }
    const prompts = newPromptText
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    setBusy(true);
    setError(null);
    try {
      await axios.post(`${API_URL}/api/ontology/prompt-profiles`, {
        sensor: newSensor,
        name: newName.trim(),
        version: newVersion.trim(),
        prompts,
        make_current: newMakeCurrent,
      });
      setNewName('');
      setNewVersion('');
      setNewPromptText('');
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'create failed');
    } finally {
      setBusy(false);
    }
  }, [newSensor, newVersion, newName, newPromptText, newMakeCurrent, load]);

  const activate = useCallback(
    async (id: number) => {
      setBusy(true);
      setError(null);
      try {
        await axios.put(`${API_URL}/api/ontology/prompt-profiles/${id}/activate`);
        await load();
      } catch (err: any) {
        setError(err?.response?.data?.detail || err?.message || 'activate failed');
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const remove = useCallback(
    async (id: number) => {
      if (!window.confirm('Delete this prompt profile? This cannot be undone.')) return;
      setBusy(true);
      setError(null);
      try {
        await axios.delete(`${API_URL}/api/ontology/prompt-profiles/${id}`);
        await load();
      } catch (err: any) {
        setError(err?.response?.data?.detail || err?.message || 'delete failed');
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const grouped = useMemo(() => {
    const out: Record<string, Profile[]> = {};
    for (const p of profiles) {
      (out[p.sensor] = out[p.sensor] || []).push(p);
    }
    for (const arr of Object.values(out)) {
      arr.sort((a, b) => (b.current ? 1 : 0) - (a.current ? 1 : 0) || b.id - a.id);
    }
    return out;
  }, [profiles]);

  return (
    <div className="admin-view" style={{ padding: '20px 24px', overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 18, flex: 1, minHeight: 0 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 14 }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>Prompt profiles</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', marginTop: 4 }}>
            Versioned per-sensor snapshots of open-vocabulary detection prompts.
          </div>
        </div>
        <div style={{ flex: 1 }} />
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

      <Panel title="New profile" sub="Sensor + version + prompts (comma or newline separated)">
        <div className="prompt-form-grid">
          <label className="label-mono">Sensor</label>
          <select value={newSensor} onChange={(e) => setNewSensor(e.target.value as Sensor)} style={inputStyle}>
            {SENSOR_LIST.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <label className="label-mono">Version</label>
          <input
            value={newVersion}
            onChange={(e) => setNewVersion(e.target.value)}
            placeholder="v3.18"
            style={inputStyle}
          />
          <label className="label-mono">Name</label>
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="East-Med · optical · operational"
            style={inputStyle}
            />
          <label className="label-mono" style={{ alignSelf: 'center' }}>
            Make current
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
            <input
              type="checkbox"
              checked={newMakeCurrent}
              onChange={(e) => setNewMakeCurrent(e.target.checked)}
              style={{ accentColor: 'var(--accent)' }}
            />
            <span style={{ color: 'var(--ink-2)' }}>Replace the sensor's current profile with this one.</span>
          </label>
          <label className="label-mono" style={{ alignSelf: 'start', paddingTop: 8 }}>
            Prompts
          </label>
          <textarea
            rows={4}
            value={newPromptText}
            onChange={(e) => setNewPromptText(e.target.value)}
            placeholder="oil tanker, helipad, burnt building"
            style={{ ...inputStyle, gridColumn: '2 / -1', resize: 'vertical', fontFamily: 'var(--font-sans)' }}
          />
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <button type="button" className="btn primary" onClick={createProfile} disabled={busy}>
            <Plus size={12} /> Create profile
          </button>
          <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', alignSelf: 'center' }}>
            POST /api/ontology/prompt-profiles
          </span>
        </div>
      </Panel>

      <div className="prompt-columns">
        {SENSOR_LIST.map((sensor) => {
          const items = grouped[sensor] || [];
          const seedPrompts = defaults[sensor] || [];
          return (
            <Panel
              key={sensor}
              title={
                <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                  <ModalityBadge m={SENSOR_TO_MODALITY[sensor]} />
                  {sensor.toUpperCase()}
                </span>
              }
              sub={`${items.length} profile${items.length === 1 ? '' : 's'} · ${items.find((p) => p.current)?.version || 'no current set'}`}
            >
              {items.length === 0 && (
                <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                  No custom profile. Default is the seeded ontology prompts ({seedPrompts.length}).
                </div>
              )}
              {items.map((p) => {
                const open = !!expanded[p.id];
                return (
                  <div
                    key={p.id}
                    style={{
                      border: '1px solid var(--line)',
                      background: p.current
                        ? 'color-mix(in oklab, var(--accent) 8%, transparent)'
                        : 'transparent',
                      borderLeft: p.current ? '3px solid var(--accent)' : '3px solid transparent',
                      marginBottom: 8,
                    }}
                  >
                    <div
                      style={{
                        display: 'grid',
                        gridTemplateColumns: 'auto 1fr auto auto',
                        gap: 8,
                        alignItems: 'center',
                        padding: '8px 10px',
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => setExpanded((c) => ({ ...c, [p.id]: !c[p.id] }))}
                        style={{
                          background: 'transparent',
                          border: 0,
                          color: 'var(--ink-2)',
                          cursor: 'pointer',
                        }}
                      >
                        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                      </button>
                      <div>
                        <div style={{ fontSize: 12.5, fontWeight: 500 }}>
                          {p.version} · {p.name}
                        </div>
                        <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
                          {p.prompts.length} prompts · {new Date(p.created_at).toLocaleString()} · {p.created_by || '—'}
                        </div>
                      </div>
                      {!p.current && (
                        <button type="button" className="btn xs" onClick={() => activate(p.id)} disabled={busy}>
                          <Check size={11} /> Activate
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn xs"
                        onClick={() => remove(p.id)}
                        disabled={busy}
                        title="Delete profile"
                        style={{ color: 'var(--nato-hostile)' }}
                      >
                        <Trash2 size={11} />
                      </button>
                    </div>
                    {open && (
                      <div style={{ padding: '0 10px 12px', display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                        {p.prompts.map((pr, i) => (
                          <span
                            key={`${pr}-${i}`}
                            style={{
                              fontSize: 11,
                              padding: '3px 9px',
                              borderRadius: 999,
                              background: 'var(--bg-2)',
                              border: '1px solid var(--line-2)',
                              color: 'var(--ink-1)',
                            }}
                          >
                            {pr}
                          </span>
                        ))}
                        {p.prompts.length === 0 && (
                          <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>
                            (empty)
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
              {seedPrompts.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <div className="label-mono" style={{ marginBottom: 4 }}>
                    Seeded ontology defaults · {seedPrompts.length}
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {seedPrompts.slice(0, 8).map((pr) => (
                      <span
                        key={pr}
                        className="mono"
                        style={{
                          fontSize: 9.5,
                          padding: '2px 6px',
                          background: 'var(--bg-3)',
                          color: 'var(--ink-2)',
                          borderRadius: 999,
                        }}
                      >
                        {pr}
                      </span>
                    ))}
                    {seedPrompts.length > 8 && (
                      <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>
                        +{seedPrompts.length - 8} more
                      </span>
                    )}
                  </div>
                </div>
              )}
            </Panel>
          );
        })}
      </div>
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

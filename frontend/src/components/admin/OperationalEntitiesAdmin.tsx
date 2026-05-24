import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { CheckCircle2, Plus, RefreshCw, Ship, Trash2, Triangle, Truck, Building2, Users, GitMerge, XCircle } from 'lucide-react';

const API_URL = import.meta.env.VITE_API_URL || '';

type EntityKind = 'vessel' | 'aircraft' | 'vehicle' | 'facility' | 'unit' | 'asset';

interface OperationalEntity {
  id: string;
  kind: EntityKind;
  name: string;
  callsign?: string | null;
  hull?: string | null;
  entity_class?: string | null;
  unit_id?: string | null;
  operates_from_base_id?: string | null;
  metadata?: Record<string, any> | null;
  created_at?: string;
}

interface EntityCandidate {
  id: number;
  entity_kind: EntityKind;
  proposed_name: string;
  seed_detection_ids: number[];
  score: number;
  reason?: string;
  status: string;
  proposed_metadata?: any;
}

interface PendingSameAs {
  a: { id: string; labels: string[]; properties: Record<string, any> };
  b: { id: string; labels: string[]; properties: Record<string, any> };
  score: number;
  source: string;
  created_at?: string | null;
}

const MERGEABLE_COLS = ['callsign', 'hull', 'entity_class', 'unit_id', 'operates_from_base_id', 'metadata'] as const;
type MergeCol = typeof MERGEABLE_COLS[number];

const KINDS: { value: EntityKind; label: string; Icon: any }[] = [
  { value: 'vessel', label: 'Vessel', Icon: Ship },
  { value: 'aircraft', label: 'Aircraft', Icon: Triangle },
  { value: 'vehicle', label: 'Vehicle', Icon: Truck },
  { value: 'facility', label: 'Facility', Icon: Building2 },
  { value: 'unit', label: 'Unit', Icon: Users },
];

export default function OperationalEntitiesAdmin() {
  const [entities, setEntities] = useState<OperationalEntity[]>([]);
  const [candidates, setCandidates] = useState<EntityCandidate[]>([]);
  const [pendingSameAs, setPendingSameAs] = useState<PendingSameAs[]>([]);
  const [filterKind, setFilterKind] = useState<EntityKind | ''>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Phase 5.H merge-modal state: the pair currently being merged + per-column picks.
  const [mergeTarget, setMergeTarget] = useState<PendingSameAs | null>(null);
  const [mergePicks, setMergePicks] = useState<Record<MergeCol, 'a' | 'b'>>(() => ({
    callsign: 'b', hull: 'b', entity_class: 'b',
    unit_id: 'b', operates_from_base_id: 'b', metadata: 'b',
  }));

  // Create-form state
  const [newKind, setNewKind] = useState<EntityKind>('vessel');
  const [newName, setNewName] = useState('');
  const [newCallsign, setNewCallsign] = useState('');
  const [newEntityClass, setNewEntityClass] = useState('');

  const reload = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const params: Record<string, any> = { limit: 200 };
      if (filterKind) params.kind = filterKind;
      const [entRes, candRes, sameAsRes] = await Promise.all([
        axios.get(`${API_URL}/api/operational-entities`, { params }),
        axios.get(`${API_URL}/api/operational-entity-candidates`, { params: { status: 'pending', limit: 100 } }),
        axios.get(`${API_URL}/api/operational-entities/pending-same-as`, { params: { limit: 100 } }),
      ]);
      setEntities(entRes.data.entities || []);
      setCandidates(candRes.data.candidates || []);
      setPendingSameAs(sameAsRes.data.pending || []);
    } catch (err: any) {
      setError(err?.message || 'load failed');
    } finally {
      setBusy(false);
    }
  }, [filterKind]);

  useEffect(() => { reload().catch(() => {}); }, [reload]);

  const create = useCallback(async () => {
    if (!newName) return;
    setBusy(true);
    setError(null);
    try {
      await axios.post(`${API_URL}/api/operational-entities`, {
        kind: newKind,
        name: newName,
        callsign: newCallsign || undefined,
        entity_class: newEntityClass || undefined,
      });
      setNewName(''); setNewCallsign(''); setNewEntityClass('');
      await reload();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'create failed');
    } finally {
      setBusy(false);
    }
  }, [newKind, newName, newCallsign, newEntityClass, reload]);

  const remove = useCallback(async (id: string) => {
    if (!confirm(`Delete entity ${id}? This also removes the Neo4j mirror.`)) return;
    setBusy(true);
    try {
      await axios.delete(`${API_URL}/api/operational-entities/${encodeURIComponent(id)}`);
      await reload();
    } catch (err: any) {
      setError(err?.message || 'delete failed');
    } finally {
      setBusy(false);
    }
  }, [reload]);

  const approveCandidate = useCallback(async (cid: number) => {
    setBusy(true);
    try {
      await axios.post(`${API_URL}/api/operational-entity-candidates/${cid}/approve`);
      await reload();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'approve failed');
    } finally {
      setBusy(false);
    }
  }, [reload]);

  const rejectCandidate = useCallback(async (cid: number) => {
    setBusy(true);
    try {
      await axios.post(`${API_URL}/api/operational-entity-candidates/${cid}/reject`);
      await reload();
    } catch (err: any) {
      setError(err?.message || 'reject failed');
    } finally {
      setBusy(false);
    }
  }, [reload]);

  const approveSameAs = useCallback(async (pair: PendingSameAs) => {
    setBusy(true);
    try {
      await axios.post(
        `${API_URL}/api/operational-entities/${encodeURIComponent(pair.a.id)}/same-as/${encodeURIComponent(pair.b.id)}`,
        {},
      );
      await reload();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'same-as failed');
    } finally {
      setBusy(false);
    }
  }, [reload]);

  const rejectSameAs = useCallback(async (pair: PendingSameAs) => {
    setBusy(true);
    try {
      await axios.post(`${API_URL}/api/operational-entities/pending-same-as/reject`, {
        a_id: pair.a.id, b_id: pair.b.id,
      });
      await reload();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'reject failed');
    } finally {
      setBusy(false);
    }
  }, [reload]);

  const openMergeModal = useCallback((pair: PendingSameAs) => {
    setMergeTarget(pair);
    // Default each column to whichever side has a non-empty value
    // (prefer A if A is non-empty and B is empty, otherwise B).
    const picks: Record<MergeCol, 'a' | 'b'> = {} as any;
    for (const col of MERGEABLE_COLS) {
      const aVal = pair.a.properties?.[col];
      const bVal = pair.b.properties?.[col];
      picks[col] = (aVal && !bVal) ? 'a' : 'b';
    }
    setMergePicks(picks);
  }, []);

  const submitMerge = useCallback(async () => {
    if (!mergeTarget) return;
    setBusy(true);
    try {
      await axios.post(
        `${API_URL}/api/operational-entities/${encodeURIComponent(mergeTarget.a.id)}/merge-into/${encodeURIComponent(mergeTarget.b.id)}`,
        { resolutions: mergePicks },
      );
      setMergeTarget(null);
      await reload();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'merge failed');
    } finally {
      setBusy(false);
    }
  }, [mergeTarget, mergePicks, reload]);

  const groupedEntities = useMemo(() => {
    const groups: Record<EntityKind, OperationalEntity[]> = {
      vessel: [], aircraft: [], vehicle: [], facility: [], unit: [], asset: [],
    };
    for (const e of entities) groups[e.kind]?.push(e);
    return groups;
  }, [entities]);

  return (
    <div className="op-entities-admin" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 340px', gap: 8, padding: 8, height: '100%', overflow: 'hidden' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0 }}>
        <div className="panel" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px' }}>
          <span className="h-title">Operational entities · {entities.length}</span>
          <select
            value={filterKind}
            onChange={(e) => setFilterKind(e.target.value as EntityKind | '')}
            style={{ marginLeft: 12, background: 'var(--bg-1)', border: '1px solid var(--line)', color: 'var(--ink-1)', padding: '2px 6px', fontSize: 11 }}
          >
            <option value="">All kinds</option>
            {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
          </select>
          <button type="button" onClick={reload} className="sentinel-btn" style={{ marginLeft: 'auto' }} disabled={busy}>
            <RefreshCw size={13} /> Refresh
          </button>
        </div>
        {error && (
          <div className="panel" style={{ padding: '6px 10px', color: 'var(--warn)', fontFamily: 'monospace', fontSize: 11 }}>{error}</div>
        )}
        <div className="panel" style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 8 }}>
          {KINDS.map((k) => {
            const rows = groupedEntities[k.value] || [];
            if (filterKind && filterKind !== k.value) return null;
            if (rows.length === 0 && filterKind !== k.value) return null;
            const Icon = k.Icon;
            return (
              <div key={k.value} style={{ marginBottom: 12 }}>
                <div className="h-title" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 4px', borderBottom: '1px solid var(--line)' }}>
                  <Icon size={13} /> {k.label} · {rows.length}
                </div>
                {rows.length === 0 ? (
                  <div style={{ padding: 8, color: 'var(--ink-3)', fontFamily: 'monospace', fontSize: 11 }}>none</div>
                ) : (
                  <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 120px 120px 60px', gap: 4, padding: 4 }}>
                    <div className="h-title" style={{ fontSize: 10 }}>Name</div>
                    <div className="h-title" style={{ fontSize: 10 }}>Callsign</div>
                    <div className="h-title" style={{ fontSize: 10 }}>Class</div>
                    <div></div>
                    {rows.map((e) => (
                      <div key={e.id} style={{ display: 'contents' }}>
                        <div style={{ fontSize: 12, color: 'var(--ink-0)' }}>
                          {e.name}
                          <div style={{ fontSize: 10, color: 'var(--ink-3)', fontFamily: 'monospace' }}>{e.id}</div>
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--ink-2)' }}>{e.callsign || '—'}</div>
                        <div style={{ fontSize: 11, color: 'var(--ink-2)' }}>{e.entity_class || '—'}</div>
                        <button
                          type="button"
                          onClick={() => remove(e.id)}
                          className="sentinel-btn"
                          style={{ padding: '2px 6px' }}
                          title="Delete entity"
                          disabled={busy}
                        >
                          <Trash2 size={11} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0 }}>
        <div className="panel" style={{ padding: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div className="h-title">Create entity</div>
          <label style={{ fontSize: 10, color: 'var(--ink-3)' }}>Kind
            <select
              value={newKind}
              onChange={(e) => setNewKind(e.target.value as EntityKind)}
              style={{ width: '100%', marginTop: 4, background: 'var(--bg-1)', border: '1px solid var(--line)', color: 'var(--ink-1)', padding: '4px 6px', fontSize: 12 }}
            >
              {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 10, color: 'var(--ink-3)' }}>Name
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="e.g. Black Pearl"
              style={{ width: '100%', marginTop: 4, background: 'var(--bg-1)', border: '1px solid var(--line)', color: 'var(--ink-1)', padding: '4px 6px', fontSize: 12 }}
            />
          </label>
          <label style={{ fontSize: 10, color: 'var(--ink-3)' }}>Callsign
            <input
              value={newCallsign}
              onChange={(e) => setNewCallsign(e.target.value)}
              style={{ width: '100%', marginTop: 4, background: 'var(--bg-1)', border: '1px solid var(--line)', color: 'var(--ink-1)', padding: '4px 6px', fontSize: 12 }}
            />
          </label>
          <label style={{ fontSize: 10, color: 'var(--ink-3)' }}>Class
            <input
              value={newEntityClass}
              onChange={(e) => setNewEntityClass(e.target.value)}
              placeholder="e.g. container_ship"
              style={{ width: '100%', marginTop: 4, background: 'var(--bg-1)', border: '1px solid var(--line)', color: 'var(--ink-1)', padding: '4px 6px', fontSize: 12 }}
            />
          </label>
          <button
            type="button"
            onClick={create}
            disabled={busy || !newName}
            className="sentinel-btn primary"
            style={{ justifyContent: 'center', marginTop: 4 }}
          >
            <Plus size={13} /> Create
          </button>
        </div>

        <div className="panel" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <div className="panel-h">
            <span className="h-title">Pending candidates · {candidates.length}</span>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: 6 }}>
            {candidates.length === 0 ? (
              <div style={{ padding: 8, color: 'var(--ink-3)', fontFamily: 'monospace', fontSize: 11 }}>none pending</div>
            ) : candidates.map((c) => (
              <div key={c.id} style={{ border: '1px solid var(--line)', padding: 8, marginBottom: 6, background: 'var(--bg-1)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--ink-3)', fontFamily: 'monospace' }}>
                  <span>{c.entity_kind}</span>
                  <span style={{ marginLeft: 'auto' }}>score {Number(c.score).toFixed(2)}</span>
                </div>
                <div style={{ fontSize: 12, color: 'var(--ink-0)', marginTop: 2 }}>{c.proposed_name}</div>
                {c.reason && <div style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 2 }}>{c.reason}</div>}
                <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
                  <button type="button" onClick={() => approveCandidate(c.id)} className="sentinel-btn primary" style={{ flex: 1, justifyContent: 'center', padding: '4px 6px' }} disabled={busy}>
                    <CheckCircle2 size={11} /> Approve
                  </button>
                  <button type="button" onClick={() => rejectCandidate(c.id)} className="sentinel-btn" style={{ flex: 1, justifyContent: 'center', padding: '4px 6px' }} disabled={busy}>
                    <XCircle size={11} /> Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="panel" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <div className="panel-h">
            <span className="h-title">Pending SAME_AS · {pendingSameAs.length}</span>
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: 6 }}>
            {pendingSameAs.length === 0 ? (
              <div style={{ padding: 8, color: 'var(--ink-3)', fontFamily: 'monospace', fontSize: 11 }}>no proposed identities pending</div>
            ) : pendingSameAs.map((pair) => (
              <div key={`${pair.a.id}::${pair.b.id}`} style={{ border: '1px solid var(--line)', padding: 8, marginBottom: 6, background: 'var(--bg-1)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--ink-3)', fontFamily: 'monospace' }}>
                  <span>{pair.source}</span>
                  <span style={{ marginLeft: 'auto' }}>score {Number(pair.score).toFixed(2)}</span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', gap: 6, marginTop: 4 }}>
                  <div style={{ background: 'var(--bg-0)', border: '1px solid var(--line)', padding: 4, minWidth: 0 }}>
                    <div style={{ fontSize: 11, color: 'var(--ink-0)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {pair.a.properties?.name || pair.a.id}
                    </div>
                    <div style={{ fontSize: 9, color: 'var(--ink-3)', fontFamily: 'monospace' }}>{pair.a.id}</div>
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--ink-3)', fontFamily: 'monospace' }}>≈</div>
                  <div style={{ background: 'var(--bg-0)', border: '1px solid var(--line)', padding: 4, minWidth: 0 }}>
                    <div style={{ fontSize: 11, color: 'var(--ink-0)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {pair.b.properties?.name || pair.b.id}
                    </div>
                    <div style={{ fontSize: 9, color: 'var(--ink-3)', fontFamily: 'monospace' }}>{pair.b.id}</div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
                  <button type="button" onClick={() => approveSameAs(pair)} className="sentinel-btn primary" style={{ flex: 1, justifyContent: 'center', padding: '4px 6px' }} disabled={busy}>
                    <CheckCircle2 size={11} /> Approve
                  </button>
                  <button type="button" onClick={() => openMergeModal(pair)} className="sentinel-btn" style={{ flex: 1, justifyContent: 'center', padding: '4px 6px' }} disabled={busy} title="Merge PostGIS rows (Phase 5.H)">
                    <GitMerge size={11} /> Merge
                  </button>
                  <button type="button" onClick={() => rejectSameAs(pair)} className="sentinel-btn" style={{ flex: 1, justifyContent: 'center', padding: '4px 6px' }} disabled={busy}>
                    <XCircle size={11} /> Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {mergeTarget && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
        }} onClick={() => setMergeTarget(null)}>
          <div onClick={(e) => e.stopPropagation()} style={{
            background: 'var(--bg-1)', border: '1px solid var(--line)', padding: 16,
            minWidth: 520, maxWidth: 720, maxHeight: '80vh', overflow: 'auto',
          }}>
            <div className="h-title" style={{ marginBottom: 8 }}>
              Merge {mergeTarget.a.id} → {mergeTarget.b.id}
            </div>
            <div style={{ fontSize: 11, color: 'var(--ink-3)', marginBottom: 12 }}>
              For each column, pick which side's value to keep on the merged
              ({mergeTarget.b.id}) row. The source ({mergeTarget.a.id}) row
              and its Neo4j mirror are deleted on submit.
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr 1fr', gap: 6, alignItems: 'center', fontSize: 11 }}>
              <div className="h-title" style={{ fontSize: 10 }}>Column</div>
              <div className="h-title" style={{ fontSize: 10 }}>A ({mergeTarget.a.id})</div>
              <div className="h-title" style={{ fontSize: 10 }}>B ({mergeTarget.b.id})</div>
              {MERGEABLE_COLS.map((col) => {
                const aVal = mergeTarget.a.properties?.[col];
                const bVal = mergeTarget.b.properties?.[col];
                const fmt = (v: any) => v == null ? '—' : (typeof v === 'object' ? JSON.stringify(v).slice(0, 40) : String(v));
                return (
                  <div key={col} style={{ display: 'contents' }}>
                    <div style={{ color: 'var(--ink-1)', fontFamily: 'monospace' }}>{col}</div>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, padding: 4, background: mergePicks[col] === 'a' ? 'var(--bg-2)' : 'transparent', border: '1px solid var(--line)' }}>
                      <input type="radio" checked={mergePicks[col] === 'a'} onChange={() => setMergePicks((p) => ({ ...p, [col]: 'a' }))} />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{fmt(aVal)}</span>
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, padding: 4, background: mergePicks[col] === 'b' ? 'var(--bg-2)' : 'transparent', border: '1px solid var(--line)' }}>
                      <input type="radio" checked={mergePicks[col] === 'b'} onChange={() => setMergePicks((p) => ({ ...p, [col]: 'b' }))} />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{fmt(bVal)}</span>
                    </label>
                  </div>
                );
              })}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 16, justifyContent: 'flex-end' }}>
              <button type="button" onClick={() => setMergeTarget(null)} className="sentinel-btn" disabled={busy}>Cancel</button>
              <button type="button" onClick={submitMerge} className="sentinel-btn primary" disabled={busy}>
                <GitMerge size={13} /> Merge rows
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

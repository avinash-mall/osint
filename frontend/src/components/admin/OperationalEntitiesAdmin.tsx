import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { CheckCircle2, Plus, RefreshCw, Ship, Trash2, Triangle, Truck, Building2, Users, XCircle } from 'lucide-react';

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
  const [filterKind, setFilterKind] = useState<EntityKind | ''>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      const [entRes, candRes] = await Promise.all([
        axios.get(`${API_URL}/api/operational-entities`, { params }),
        axios.get(`${API_URL}/api/operational-entity-candidates`, { params: { status: 'pending', limit: 100 } }),
      ]);
      setEntities(entRes.data.entities || []);
      setCandidates(candRes.data.candidates || []);
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
      </div>
    </div>
  );
}

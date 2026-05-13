/**
 * ObjectDetailsForm — shared operator-edited metadata for a detection.
 *
 * Used by GaiaMap (right Selection panel), FmvPlayer (right Detail tab) and
 * OntologyAdmin (object detail page). Writes to:
 *   PUT /api/detections/{id}/details         (source = "map")
 *   PUT /api/fmv/detections/{id}/details     (source = "fmv")
 *
 * Fields: designation, object class, military classification, threat level,
 * affiliation, operator confidence (slider), notes. Includes a Delete control
 * and "View on GEOINT / View in FMV" navigation buttons.
 */

import axios from 'axios';
import {
  Crosshair,
  Eye,
  Film,
  Map as MapIcon,
  Save,
  Shield,
  Trash2,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

export type DetailSource = 'map' | 'fmv';

export const THREAT_LEVELS = [
  { id: 'critical', label: 'CRITICAL', color: 'var(--nato-hostile)' },
  { id: 'high', label: 'HIGH', color: 'var(--accent)' },
  { id: 'medium', label: 'MEDIUM', color: 'var(--nato-unknown)' },
  { id: 'low', label: 'LOW', color: 'var(--nato-neutral)' },
  { id: 'none', label: 'NONE', color: 'var(--ink-3)' },
] as const;

export const AFFILIATIONS = [
  { id: 'friend', label: 'FRIEND', color: 'var(--nato-friend)' },
  { id: 'hostile', label: 'HOSTILE', color: 'var(--nato-hostile)' },
  { id: 'neutral', label: 'NEUTRAL', color: 'var(--nato-neutral)' },
  { id: 'unknown', label: 'UNKNOWN', color: 'var(--nato-unknown)' },
] as const;

export type ObjectDetails = {
  designation?: string;
  object_class?: string;
  military_classification?: string;
  threat_level?: string;
  affiliation?: string;
  confidence_override?: number;
  notes?: string;
  updated_at?: string;
  updated_by?: string;
};

type Props = {
  source: DetailSource;
  detectionId: number;
  /** Initial details from the parent's last fetch — saves a round-trip. */
  initial?: ObjectDetails;
  /** Default class fallback when the row hasn't been edited yet. */
  defaultClass?: string;
  /** Optional friendly title displayed in the header. */
  title?: string;
  /** Can the current user delete? Admins always, analysts only operator boxes. */
  canDelete?: boolean;
  onSaved?: (details: ObjectDetails) => void;
  onDeleted?: () => void;
  /** When provided, renders View on GEOINT / View in FMV buttons. */
  onViewOnMap?: () => void;
  onViewInFmv?: () => void;
};

function endpointFor(source: DetailSource, detectionId: number): string {
  return source === 'fmv'
    ? `${API_URL}/api/fmv/detections/${detectionId}/details`
    : `${API_URL}/api/detections/${detectionId}/details`;
}

function deleteEndpointFor(source: DetailSource, detectionId: number): string {
  return source === 'fmv'
    ? `${API_URL}/api/fmv/detections/${detectionId}`
    : `${API_URL}/api/detections/${detectionId}`;
}

export default function ObjectDetailsForm({
  source,
  detectionId,
  initial,
  defaultClass,
  title,
  canDelete = false,
  onSaved,
  onDeleted,
  onViewOnMap,
  onViewInFmv,
}: Props) {
  const [v, setV] = useState<ObjectDetails>({});
  const [busy, setBusy] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Hydrate from server on mount (and any time the detection changes), then
  // overlay the parent-supplied initial so we never blank fields by accident.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await axios.get(endpointFor(source, detectionId));
        if (cancelled) return;
        const remote: ObjectDetails = data?.details || {};
        setV({
          object_class: remote.object_class || initial?.object_class || defaultClass,
          designation: remote.designation || initial?.designation,
          military_classification: remote.military_classification || initial?.military_classification,
          threat_level: remote.threat_level || initial?.threat_level || 'medium',
          affiliation: remote.affiliation || initial?.affiliation || 'unknown',
          confidence_override: remote.confidence_override ?? initial?.confidence_override,
          notes: remote.notes || initial?.notes || '',
          updated_at: remote.updated_at,
          updated_by: remote.updated_by,
        });
      } catch (err: any) {
        if (cancelled) return;
        // 404 just means the row hasn't been edited yet; seed from initial.
        setV({
          object_class: initial?.object_class || defaultClass,
          designation: initial?.designation,
          military_classification: initial?.military_classification,
          threat_level: initial?.threat_level || 'medium',
          affiliation: initial?.affiliation || 'unknown',
          confidence_override: initial?.confidence_override,
          notes: initial?.notes || '',
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source, detectionId, defaultClass, initial?.designation, initial?.threat_level, initial?.affiliation]);

  const set = useCallback(<K extends keyof ObjectDetails>(key: K, value: ObjectDetails[K]) => {
    setV((cur) => ({ ...cur, [key]: value }));
  }, []);

  const save = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await axios.put(endpointFor(source, detectionId), {
        designation: v.designation,
        object_class: v.object_class,
        military_classification: v.military_classification,
        threat_level: v.threat_level,
        affiliation: v.affiliation,
        confidence_override: v.confidence_override,
        notes: v.notes,
      });
      const updated: ObjectDetails = data?.details || {};
      setV((cur) => ({ ...cur, ...updated }));
      setSavedAt(updated.updated_at || new Date().toISOString());
      onSaved?.(updated);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'save failed');
    } finally {
      setBusy(false);
    }
  }, [source, detectionId, v, onSaved]);

  const remove = useCallback(async () => {
    if (!canDelete) return;
    if (!window.confirm('Delete this detection? This cannot be undone.')) return;
    setBusy(true);
    setError(null);
    try {
      await axios.delete(deleteEndpointFor(source, detectionId));
      onDeleted?.();
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'delete failed');
    } finally {
      setBusy(false);
    }
  }, [source, detectionId, canDelete, onDeleted]);

  const inputStyle: React.CSSProperties = {
    width: '100%',
    background: 'var(--bg-2)',
    border: '1px solid var(--line)',
    color: 'var(--ink-0)',
    padding: '7px 10px',
    fontSize: 12,
    fontFamily: 'var(--font-sans)',
    borderRadius: 4,
    outline: 'none',
  };

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        minWidth: 0,
        gap: 12,
        padding: 14,
        background: 'var(--bg-1)',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Crosshair size={14} style={{ color: 'var(--accent)' }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.15 }}>
            {title || v.designation || v.object_class || 'Detection'}
          </div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
            {source === 'fmv' ? 'FMV' : 'GEOINT'}-DET-{detectionId}
            {savedAt && <span style={{ color: 'var(--accent)', marginLeft: 6 }}>· saved</span>}
          </div>
        </div>
        <ThreatBadge level={v.threat_level} />
      </div>

      {/* Cross-screen navigation */}
      {(onViewOnMap || onViewInFmv) && (
        <div style={{ display: 'flex', gap: 6 }}>
          {onViewOnMap && (
            <button type="button" className="btn xs" onClick={onViewOnMap} title="Open on GEOINT map">
              <MapIcon size={11} /> View on map
            </button>
          )}
          {onViewInFmv && (
            <button type="button" className="btn xs" onClick={onViewInFmv} title="Open in FMV player">
              <Film size={11} /> View in FMV
            </button>
          )}
        </div>
      )}

      <ObjField label="Designation" hint="operator-assigned">
        <input
          style={inputStyle}
          value={v.designation || ''}
          placeholder="e.g. Arleigh Burke DDG-51"
          onChange={(e) => set('designation', e.target.value)}
        />
      </ObjField>

      <ObjField label="Object class" hint="ontology key">
        <input
          style={inputStyle}
          value={v.object_class || ''}
          placeholder="destroyer · aircraft · vehicle …"
          onChange={(e) => set('object_class', e.target.value)}
        />
      </ObjField>

      <ObjField label="Military classification" hint="platform / role / variant">
        <input
          style={inputStyle}
          value={v.military_classification || ''}
          placeholder="e.g. Surface combatant · AAW · Project 956"
          onChange={(e) => set('military_classification', e.target.value)}
        />
      </ObjField>

      <ObjField label="Threat level">
        <div className="seg" style={{ display: 'flex' }}>
          {THREAT_LEVELS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={(v.threat_level || 'medium') === t.id ? 'on' : ''}
              onClick={() => set('threat_level', t.id)}
              style={{ flex: 1 }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </ObjField>

      <ObjField label="Affiliation (NATO APP-6)">
        <div className="seg" style={{ display: 'flex' }}>
          {AFFILIATIONS.map((a) => (
            <button
              key={a.id}
              type="button"
              className={(v.affiliation || 'unknown') === a.id ? 'on' : ''}
              onClick={() => set('affiliation', a.id)}
              style={{ flex: 1 }}
            >
              {a.label}
            </button>
          ))}
        </div>
      </ObjField>

      <ObjField label="Operator confidence">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={Math.round(((v.confidence_override ?? 0.5) as number) * 100)}
            onChange={(e) => set('confidence_override', Number(e.target.value) / 100)}
            style={{ flex: 1, accentColor: 'var(--accent)' }}
          />
          <span
            className="mono"
            style={{ fontSize: 11, color: 'var(--ink-1)', width: 36, textAlign: 'right' }}
          >
            {Math.round(((v.confidence_override ?? 0.5) as number) * 100)}%
          </span>
        </div>
      </ObjField>

      <ObjField label="Operator notes">
        <textarea
          rows={3}
          style={{ ...inputStyle, resize: 'vertical', fontFamily: 'var(--font-sans)' }}
          value={v.notes || ''}
          placeholder="Pattern of life, ROE flags, link-graph cues…"
          onChange={(e) => set('notes', e.target.value)}
        />
      </ObjField>

      {error && (
        <div
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
            color: 'var(--nato-hostile)',
            padding: '6px 8px',
            border: '1px solid var(--nato-hostile)',
          }}
          role="alert"
        >
          {error}
        </div>
      )}

      <div
        style={{
          display: 'flex',
          gap: 8,
          paddingTop: 6,
          borderTop: '1px solid var(--line)',
          alignItems: 'center',
        }}
      >
        <button
          type="button"
          className="btn primary"
          onClick={save}
          disabled={busy}
          title="Save operator metadata"
          style={{ flex: 1, justifyContent: 'center' }}
        >
          <Save size={12} /> {busy ? 'Saving…' : 'Save'}
        </button>
        {canDelete && (
          <button
            type="button"
            className="btn xs"
            onClick={remove}
            disabled={busy}
            title="Delete this detection"
            style={{
              background: 'color-mix(in oklab, var(--nato-hostile) 12%, var(--bg-2))',
              borderColor: 'color-mix(in oklab, var(--nato-hostile) 55%, var(--line))',
              color: 'var(--nato-hostile)',
            }}
          >
            <Trash2 size={11} /> Delete
          </button>
        )}
      </div>

      <span
        className="mono"
        style={{ fontSize: 10, color: 'var(--ink-3)', alignSelf: 'center' }}
      >
        {v.updated_at ? (
          <>
            <Eye size={9} style={{ verticalAlign: 'middle' }} />{' '}
            edited by {v.updated_by || 'operator'} · {new Date(v.updated_at).toLocaleString()}
          </>
        ) : (
          <>
            <Shield size={9} style={{ verticalAlign: 'middle' }} /> Auto-classified · edit to override
          </>
        )}
      </span>
    </div>
  );
}

function ObjField({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <span className="label-mono" style={{ fontSize: 10 }}>
        {label}
        {hint && (
          <span style={{ color: 'var(--ink-3)', marginLeft: 6, letterSpacing: 0, textTransform: 'none' }}>
            · {hint}
          </span>
        )}
      </span>
      {children}
    </label>
  );
}

function ThreatBadge({ level }: { level?: string }) {
  const lvl =
    THREAT_LEVELS.find((t) => t.id === (level || 'medium').toLowerCase()) || THREAT_LEVELS[2];
  return (
    <span
      className="mono"
      style={{
        fontSize: 10,
        letterSpacing: '.08em',
        padding: '2px 8px',
        background: `color-mix(in oklab, ${lvl.color} 22%, var(--bg-2))`,
        color: lvl.color,
        border: `1px solid ${lvl.color}`,
      }}
    >
      {lvl.label}
    </span>
  );
}

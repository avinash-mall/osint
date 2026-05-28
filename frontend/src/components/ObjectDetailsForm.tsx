/**
 * ObjectDetailsForm — shared operator-edited metadata for a detection.
 *
 * Mount points:
 *   - GaiaMap        (right Selection panel)
 *   - FmvPlayer      (right Detail tab)
 *   - OntologyAdmin  (object detail page)
 *
 * Persistence:
 *   PUT /api/detections/{id}/details         (source = "map")
 *   PUT /api/fmv/detections/{id}/details     (source = "fmv")
 *
 * Behaviour changes vs previous revision:
 *   1. Debounced auto-save (no explicit Save button) — operators editing many
 *      detections in a row no longer have to click Save 30 times.
 *   2. Mid-edit work survives unmount via sessionStorage keyed by source+id;
 *      restored on next mount unless the server has a newer updated_at.
 *   3. Hydrate effect deps use a stable JSON key for `initial` so partial
 *      updates from the parent always re-hydrate.
 *   4. canDelete defaults to `user.role === 'admin'` via useAuth — callers
 *      no longer have to thread the flag through.
 *   5. THREAT_LEVELS / AFFILIATIONS come from utils/objectMetadata (single
 *      source of truth).
 *   6. Status messages have aria-live; the save indicator has aria-busy.
 *   7. Form establishes a CSS container so the threat/affiliation segmented
 *      controls stack on narrow containers via @container, not media queries.
 */

import axios from 'axios';
import { Crosshair, Eye, Film, Map as MapIcon, Shield, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../hooks/useAuth';
import {
  AFFILIATIONS,
  DEFAULT_AFFILIATION,
  DEFAULT_THREAT,
  THREAT_LEVELS,
  type ObjectDetails,
  threatLevel as lookupThreatLevel,
} from '../utils/objectMetadata';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

export type DetailSource = 'map' | 'fmv';

const AUTOSAVE_DEBOUNCE_MS = 600;
const DRAFT_KEY_PREFIX = 'sentinel.detail-draft.';

type Props = {
  source: DetailSource;
  detectionId: number;
  /** Initial details from the parent's last fetch — saves a round-trip. */
  initial?: ObjectDetails;
  /** Default class fallback when the row hasn't been edited yet. */
  defaultClass?: string;
  /** Optional friendly title displayed in the header. */
  title?: string;
  /** Override canDelete; default is `user.role === 'admin'`. */
  canDelete?: boolean;
  onSaved?: (details: ObjectDetails) => void;
  onDeleted?: () => void;
  /** When provided, renders View on GEOINT / View in FMV buttons. */
  onViewOnMap?: () => void;
  onViewInFmv?: () => void;
};

function endpointFor(source: DetailSource, id: number): string {
  return source === 'fmv'
    ? `${API_URL}/api/fmv/detections/${id}/details`
    : `${API_URL}/api/detections/${id}/details`;
}
function deleteEndpointFor(source: DetailSource, id: number): string {
  return source === 'fmv'
    ? `${API_URL}/api/fmv/detections/${id}`
    : `${API_URL}/api/detections/${id}`;
}
function draftKey(source: DetailSource, id: number): string {
  return `${DRAFT_KEY_PREFIX}${source}-${id}`;
}

type Status =
  | { kind: 'idle' }
  | { kind: 'dirty' }
  | { kind: 'saving' }
  | { kind: 'saved'; at: string }
  | { kind: 'error'; message: string };

export default function ObjectDetailsForm({
  source,
  detectionId,
  initial,
  defaultClass,
  title,
  canDelete: canDeleteOverride,
  onSaved,
  onDeleted,
  onViewOnMap,
  onViewInFmv,
}: Props) {
  const { user } = useAuth();
  const canDelete = canDeleteOverride ?? user?.role === 'admin';

  const [v, setV] = useState<ObjectDetails>({});
  const [status, setStatus] = useState<Status>({ kind: 'idle' });

  // Stable serialisation of `initial` so a partial parent update always re-hydrates.
  const initialKey = useMemo(() => JSON.stringify(initial ?? null), [initial]);

  /* ── Hydrate on mount / detection change ──────────────────────────── */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      // 1. Read local draft first (so a mid-edit unmount doesn't lose work).
      let draft: ObjectDetails | null = null;
      try {
        const raw = sessionStorage.getItem(draftKey(source, detectionId));
        if (raw) draft = JSON.parse(raw) as ObjectDetails;
      } catch { /* ignore */ }

      // 2. Fetch the canonical server row.
      let remote: ObjectDetails = {};
      try {
        const { data } = await axios.get(endpointFor(source, detectionId));
        remote = data?.details || {};
      } catch {
        // 404 = no row yet, treat as empty
      }
      if (cancelled) return;

      // 3. Merge: draft wins if it's newer than the server's updated_at;
      //    otherwise server wins; `initial` from props is the lowest priority floor.
      const merged: ObjectDetails = {
        object_class: remote.object_class || initial?.object_class || defaultClass,
        designation: remote.designation || initial?.designation,
        military_classification: remote.military_classification || initial?.military_classification,
        threat_level: remote.threat_level || initial?.threat_level || DEFAULT_THREAT,
        affiliation: remote.affiliation || initial?.affiliation || DEFAULT_AFFILIATION,
        confidence_override: remote.confidence_override ?? initial?.confidence_override,
        notes: remote.notes || initial?.notes || '',
        updated_at: remote.updated_at,
        updated_by: remote.updated_by,
        // Read-only platform identification (Plan C/D pipeline; UI surfaces in Plan E).
        platform_name: remote.platform_name ?? initial?.platform_name ?? null,
        platform_family: remote.platform_family ?? initial?.platform_family ?? null,
        platform_confidence: remote.platform_confidence ?? initial?.platform_confidence ?? null,
        platform_source: remote.platform_source ?? initial?.platform_source ?? null,
      };
      if (draft) {
        const draftTime = (draft as any)._draftAt as number | undefined;
        const serverTime = remote.updated_at ? Date.parse(remote.updated_at) : 0;
        if (draftTime && draftTime > serverTime) {
          Object.assign(merged, draft);
          setStatus({ kind: 'dirty' });
        }
      }
      setV(merged);
    })();
    return () => { cancelled = true; };
  }, [source, detectionId, defaultClass, initialKey]);

  /* ── Debounced auto-save ──────────────────────────────────────────── */
  const dirtyRef = useRef(false);
  const timerRef = useRef<number | null>(null);

  const persistDraft = useCallback((next: ObjectDetails) => {
    try {
      sessionStorage.setItem(
        draftKey(source, detectionId),
        JSON.stringify({ ...next, _draftAt: Date.now() }),
      );
    } catch { /* quota */ }
  }, [source, detectionId]);

  const clearDraft = useCallback(() => {
    try { sessionStorage.removeItem(draftKey(source, detectionId)); } catch { /* ignore */ }
  }, [source, detectionId]);

  const save = useCallback(async (snapshot: ObjectDetails) => {
    setStatus({ kind: 'saving' });
    try {
      const { data } = await axios.put(endpointFor(source, detectionId), {
        designation: snapshot.designation,
        object_class: snapshot.object_class,
        military_classification: snapshot.military_classification,
        threat_level: snapshot.threat_level,
        affiliation: snapshot.affiliation,
        confidence_override: snapshot.confidence_override,
        notes: snapshot.notes,
      });
      const updated: ObjectDetails = data?.details || {};
      setV((cur) => ({ ...cur, ...updated }));
      clearDraft();
      setStatus({ kind: 'saved', at: updated.updated_at || new Date().toISOString() });
      onSaved?.(updated);
    } catch (err: any) {
      setStatus({ kind: 'error', message: err?.response?.data?.detail || err?.message || 'save failed' });
    }
  }, [source, detectionId, clearDraft, onSaved]);

  const set = useCallback(<K extends keyof ObjectDetails>(key: K, value: ObjectDetails[K]) => {
    setV((cur) => {
      const next = { ...cur, [key]: value };
      dirtyRef.current = true;
      persistDraft(next);
      setStatus({ kind: 'dirty' });
      // Reset debounce
      if (timerRef.current != null) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => save(next), AUTOSAVE_DEBOUNCE_MS);
      return next;
    });
  }, [persistDraft, save]);

  // Flush on unmount: if a save is queued, drop the timer; the draft remains in
  // sessionStorage so the next mount can pick it up.
  useEffect(() => () => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
  }, []);

  // Flush on tab close
  useEffect(() => {
    const handler = () => {
      if (dirtyRef.current && timerRef.current != null) {
        window.clearTimeout(timerRef.current);
        // Best-effort PUT that preserves method semantics during tab close.
        const url = endpointFor(source, detectionId);
        const body = JSON.stringify({
          designation: v.designation,
          object_class: v.object_class,
          military_classification: v.military_classification,
          threat_level: v.threat_level,
          affiliation: v.affiliation,
          confidence_override: v.confidence_override,
          notes: v.notes,
        });
        void fetch(url, {
          method: 'PUT',
          body,
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          keepalive: true,
        });
      }
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [source, detectionId, v]);

  const remove = useCallback(async () => {
    if (!canDelete) return;
    if (!window.confirm('Delete this detection? This cannot be undone.')) return;
    setStatus({ kind: 'saving' });
    try {
      await axios.delete(deleteEndpointFor(source, detectionId));
      clearDraft();
      onDeleted?.();
    } catch (err: any) {
      setStatus({ kind: 'error', message: err?.response?.data?.detail || err?.message || 'delete failed' });
    }
  }, [source, detectionId, canDelete, clearDraft, onDeleted]);

  return (
    <form
      className="object-details-form"
      aria-busy={status.kind === 'saving'}
      onSubmit={(e) => e.preventDefault()}
      style={{
        display: 'flex',
        flexDirection: 'column',
        minWidth: 0,
        gap: 12,
        padding: 14,
        background: 'var(--bg-1)',
        containerType: 'inline-size',
        containerName: 'object-details',
      }}
    >
      {/* Header */}
      <div className="object-details-header">
        <Crosshair size={14} style={{ color: 'var(--accent)' }} aria-hidden />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.15 }}>
            {title || v.designation || v.object_class || 'Detection'}
          </div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
            {source === 'fmv' ? 'FMV' : 'GEOINT'}-DET-{detectionId}
            <SaveIndicator status={status} />
          </div>
        </div>
        <ThreatBadge level={v.threat_level} />
      </div>

      {(onViewOnMap || onViewInFmv) && (
        <div className="object-details-actions">
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

      <Field label="Designation" hint="operator-assigned">
        <Input value={v.designation} onChange={(s) => set('designation', s)} placeholder="e.g. Arleigh Burke DDG-51"/>
      </Field>

      <Field label="Object class" hint="ontology key">
        <Input value={v.object_class} onChange={(s) => set('object_class', s)} placeholder="destroyer · aircraft · vehicle …"/>
      </Field>

      <Field label="Military classification" hint="platform / role / variant">
        <Input value={v.military_classification} onChange={(s) => set('military_classification', s)}
          placeholder="e.g. Surface combatant · AAW · Project 956"/>
      </Field>

      <Field label="Threat level">
        <SegRow
          name={`threat-${source}-${detectionId}`}
          value={(v.threat_level as string) || DEFAULT_THREAT}
          options={THREAT_LEVELS.map((t) => ({ id: t.id, label: t.label }))}
          onChange={(id) => set('threat_level', id)}
        />
      </Field>

      <Field label="Affiliation (NATO APP-6)">
        <SegRow
          name={`aff-${source}-${detectionId}`}
          value={(v.affiliation as string) || DEFAULT_AFFILIATION}
          options={AFFILIATIONS.map((a) => ({ id: a.id, label: a.label }))}
          onChange={(id) => set('affiliation', id)}
        />
      </Field>

      <Field label="Operator confidence">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <input
            type="range" min={0} max={100} step={1}
            value={Math.round(((v.confidence_override ?? 0.5) as number) * 100)}
            onChange={(e) => set('confidence_override', Number(e.target.value) / 100)}
            style={{ flex: 1, accentColor: 'var(--accent)' }}
            aria-label="Operator confidence override"
          />
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-1)', width: 36, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
            {Math.round(((v.confidence_override ?? 0.5) as number) * 100)}%
          </span>
        </div>
      </Field>

      <Field label="Operator notes">
        <textarea
          rows={3}
          value={v.notes || ''}
          placeholder="Pattern of life, ROE flags, link-graph cues…"
          onChange={(e) => set('notes', e.target.value)}
          style={{
            width: '100%',
            background: 'var(--bg-2)',
            border: '1px solid var(--line)',
            color: 'var(--ink-0)',
            padding: '7px 10px',
            fontSize: 12,
            fontFamily: 'var(--font-sans)',
            borderRadius: 4,
            outline: 'none',
            resize: 'vertical',
          }}
        />
      </Field>

      {v.platform_name ? (
        <div
          className="object-details-platform"
          data-tour="object-details-platform"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 6,
            padding: '8px 10px',
            background: 'var(--bg-2)',
            border: '1px solid var(--line)',
            borderRadius: 4,
          }}
        >
          <span className="label-mono" style={{ fontSize: 10 }}>
            Platform identification
            <span style={{ color: 'var(--ink-3)', marginLeft: 6, letterSpacing: 0, textTransform: 'none' }}>
              · read-only · approve via Identification panel
            </span>
          </span>
          <PlatformRow label="Platform" value={v.platform_name} />
          {v.platform_family ? <PlatformRow label="Family" value={v.platform_family} /> : null}
          {v.platform_confidence != null ? (
            <PlatformRow
              label="Confidence"
              value={`${((v.platform_confidence as number) * 100).toFixed(1)}%`}
              mono
            />
          ) : null}
          {v.platform_source ? (
            <PlatformRow
              label="Source"
              value={
                v.platform_source === 'auto' ? 'Auto-identified'
                : v.platform_source === 'analyst' ? 'Analyst-approved'
                : v.platform_source === 'manual' ? 'Manually set'
                : v.platform_source
              }
            />
          ) : null}
        </div>
      ) : null}

      {status.kind === 'error' && (
        <div role="alert" aria-live="assertive" className="object-details-error">
          {status.message}
        </div>
      )}

      <div className="object-details-footer">
        {canDelete ? (
          <button type="button" className="btn xs object-details-delete" onClick={remove} disabled={status.kind === 'saving'}>
            <Trash2 size={11}/> Delete detection
          </button>
        ) : <span />}
        <span className="mono object-details-meta" aria-live="polite">
          {v.updated_at ? (
            <><Eye size={9} style={{ verticalAlign: 'middle' }} aria-hidden/> edited by {v.updated_by || 'operator'} · {new Date(v.updated_at).toLocaleString()}</>
          ) : (
            <><Shield size={9} style={{ verticalAlign: 'middle' }} aria-hidden/> auto-classified · edit to override</>
          )}
        </span>
      </div>
    </form>
  );
}

/* ── Subcomponents ──────────────────────────────────────────────────── */

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <span className="label-mono" style={{ fontSize: 10 }}>
        {label}
        {hint && <span style={{ color: 'var(--ink-3)', marginLeft: 6, letterSpacing: 0, textTransform: 'none' }}>· {hint}</span>}
      </span>
      {children}
    </label>
  );
}

function Input({ value, onChange, placeholder }: { value?: string; onChange: (s: string) => void; placeholder?: string }) {
  return (
    <input
      style={{
        width: '100%',
        background: 'var(--bg-2)',
        border: '1px solid var(--line)',
        color: 'var(--ink-0)',
        padding: '7px 10px',
        fontSize: 12,
        fontFamily: 'var(--font-sans)',
        borderRadius: 4,
        outline: 'none',
      }}
      value={value || ''}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function SegRow({ name, value, options, onChange }: {
  name: string;
  value: string;
  options: { id: string; label: string }[];
  onChange: (id: string) => void;
}) {
  return (
    <div className="seg object-details-seg" role="radiogroup" aria-label={name}>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          role="radio"
          aria-checked={value === o.id}
          className={value === o.id ? 'on' : ''}
          onClick={() => onChange(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function PlatformRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, fontSize: 12, lineHeight: 1.3 }}>
      <span
        className="label-mono"
        style={{ fontSize: 10, width: 76, flex: '0 0 76px', color: 'var(--ink-2)' }}
      >
        {label}
      </span>
      <span
        className={mono ? 'mono' : undefined}
        style={{
          color: 'var(--ink-0)',
          fontVariantNumeric: mono ? 'tabular-nums' : undefined,
          minWidth: 0,
          wordBreak: 'break-word',
        }}
      >
        {value}
      </span>
    </div>
  );
}

function SaveIndicator({ status }: { status: Status }) {
  if (status.kind === 'idle') return null;
  if (status.kind === 'dirty') {
    return <span style={{ color: 'var(--ink-3)', marginLeft: 6 }}>· unsaved</span>;
  }
  if (status.kind === 'saving') {
    return <span style={{ color: 'var(--accent)', marginLeft: 6 }}>· saving…</span>;
  }
  if (status.kind === 'saved') {
    return <span style={{ color: 'var(--ok)', marginLeft: 6 }}>· saved</span>;
  }
  return null;
}

function ThreatBadge({ level }: { level?: string }) {
  const lvl = lookupThreatLevel(level);
  return (
    <span className="mono threat-badge" style={{
      fontSize: 10, letterSpacing: '.08em', padding: '2px 8px',
      background: `color-mix(in oklab, ${lvl.color} 22%, var(--bg-2))`,
      color: lvl.color, border: `1px solid ${lvl.color}`,
    }}>{lvl.label}</span>
  );
}

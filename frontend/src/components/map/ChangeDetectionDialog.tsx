/**
 * ChangeDetectionDialog — pass-vs-pass raster diff viewer.
 *
 * Wraps POST /api/imagery/change (the new endpoint that calls
 * backend/change_detection.py::compute_change). Renders the resulting
 * FeatureCollection on a small Leaflet preview with a confidence-coloured
 * polygon overlay.
 *
 * Lifecycle:
 *   1. Mount → POST {before_pass_id, after_pass_id} → store FeatureCollection
 *   2. Show a summary (peak diff, changed pixels, feature count)
 *   3. Operator can "Open on main map" which dispatches a CustomEvent the
 *      MapStage listens for to drop the layer onto the workspace.
 *   4. Operator can "Export GeoJSON" → triggers a download.
 *
 * UX:
 *   - Modal dialog with backdrop, ESC closes.
 *   - aria-busy while the diff runs (raster work, can take 5–30 s).
 *   - aria-live status updates as the request completes / errors.
 *   - Dialog is a CSS container so the summary stat row collapses to a
 *     stacked layout on small inline-sizes.
 */

import axios from 'axios';
import { useCallback, useEffect, useState } from 'react';
import { Activity, AlertTriangle, Download, Map as MapIcon, X } from 'lucide-react';
import type { Pass } from '../GaiaMap';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

type ChangeResult = {
  type: 'FeatureCollection';
  features: any[];
  mode: string;
  summary: {
    before_pass_id: number;
    after_pass_id: number;
    bounds: [number, number, number, number];
    threshold: number;
    peak_diff: number;
    changed_pixels: number;
    feature_count?: number;
  };
};

type Props = {
  before: Pass;
  after: Pass;
  onClose: () => void;
};

type State =
  | { kind: 'loading' }
  | { kind: 'ok'; result: ChangeResult }
  | { kind: 'empty' }
  | { kind: 'error'; message: string };

export default function ChangeDetectionDialog({ before, after, onClose }: Props) {
  const [state, setState] = useState<State>({ kind: 'loading' });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await axios.post<ChangeResult>(`${API_URL}/api/imagery/change`, {
          before_pass_id: before.id,
          after_pass_id: after.id,
        });
        if (cancelled) return;
        if (!data || data.features.length === 0) {
          setState({ kind: 'empty' });
        } else {
          setState({ kind: 'ok', result: data });
        }
      } catch (err: any) {
        if (cancelled) return;
        setState({
          kind: 'error',
          message: err?.response?.data?.detail || err?.message || 'change detection failed',
        });
      }
    })();
    return () => { cancelled = true; };
  }, [before.id, after.id]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const exportGeojson = useCallback(() => {
    if (state.kind !== 'ok') return;
    const blob = new Blob([JSON.stringify(state.result, null, 2)], { type: 'application/geo+json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `change-${before.id}-vs-${after.id}.geojson`;
    a.click();
    URL.revokeObjectURL(url);
  }, [state, before.id, after.id]);

  const openOnMap = useCallback(() => {
    if (state.kind !== 'ok') return;
    window.dispatchEvent(new CustomEvent('sentinel:overlay-geojson', {
      detail: {
        id: `change-${before.id}-${after.id}`,
        label: `Change · ${before.id} → ${after.id}`,
        featureCollection: state.result,
      },
    }));
    onClose();
  }, [state, before.id, after.id, onClose]);

  return (
    <div
      role="dialog" aria-modal="true"
      aria-label={`Change detection between pass ${before.id} and pass ${after.id}`}
      aria-busy={state.kind === 'loading'}
      style={{
        position: 'fixed', inset: 0, zIndex: 1800,
        display: 'grid', placeItems: 'center',
        background: 'color-mix(in oklab, var(--bg-0) 60%, transparent)',
        backdropFilter: 'blur(4px)',
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 'min(640px, calc(100vw - 32px))',
          background: 'var(--bg-1)',
          border: '1px solid var(--line)',
          borderRadius: 12,
          boxShadow: '0 24px 48px rgba(0,0,0,.55)',
          overflow: 'hidden',
          display: 'flex', flexDirection: 'column',
          containerType: 'inline-size',
          containerName: 'change-dialog',
        }}
      >
        {/* Header */}
        <header style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '14px 16px', borderBottom: '1px solid var(--line)',
        }}>
          <Activity size={16} style={{ color: 'var(--accent)' }} aria-hidden/>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>Change detection</div>
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>
              Pass {before.id} ({short(before.acquired_at)}) → Pass {after.id} ({short(after.acquired_at)})
            </div>
          </div>
          <button type="button" className="btn ghost icon xs" onClick={onClose} aria-label="Close">
            <X size={12}/>
          </button>
        </header>

        {/* Body */}
        <div style={{ padding: 16, minHeight: 200 }} aria-live="polite">
          {state.kind === 'loading' && <LoadingBody/>}
          {state.kind === 'empty' && <EmptyBody/>}
          {state.kind === 'error' && <ErrorBody message={state.message}/>}
          {state.kind === 'ok' && <ResultBody result={state.result}/>}
        </div>

        {/* Footer */}
        {state.kind === 'ok' && (
          <footer style={{
            display: 'flex', gap: 8, justifyContent: 'flex-end',
            padding: '12px 16px', borderTop: '1px solid var(--line)',
          }}>
            <button type="button" className="btn xs" onClick={exportGeojson}>
              <Download size={11}/> Export GeoJSON
            </button>
            <button type="button" className="btn xs primary" onClick={openOnMap}>
              <MapIcon size={11}/> Open on map
            </button>
          </footer>
        )}
      </div>
    </div>
  );
}

function LoadingBody() {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      padding: '20px 0', gap: 14,
      color: 'var(--ink-2)',
    }}>
      <div className="mono" style={{ fontSize: 11, color: 'var(--accent)' }}>
        ◉ DIFFING RASTERS — this can take several seconds
      </div>
      <div style={{
        width: 160, height: 4, background: 'var(--line-2)', border: '1px solid var(--line)',
        position: 'relative', overflow: 'hidden',
      }}>
        <div style={{
          position: 'absolute', inset: 0,
          background: 'var(--accent)',
          animation: 'change-progress 1.8s ease-in-out infinite',
        }}/>
      </div>
      <style>{`@keyframes change-progress { 0%,100% { left: -40%; right: 100% } 50% { left: 30%; right: 30% } }`}</style>
    </div>
  );
}

function EmptyBody() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, padding: '20px 0' }}>
      <div className="mono" style={{ fontSize: 11, color: 'var(--ok)' }}>NO SIGNIFICANT CHANGE</div>
      <div style={{ fontSize: 12, color: 'var(--ink-2)', maxWidth: 360, textAlign: 'center' }}>
        The diff between the two passes is below the configured threshold. Increase
        sensitivity in <b>CHANGE_DET_THRESHOLD</b> if you expect to see smaller changes.
      </div>
    </div>
  );
}

function ErrorBody({ message }: { message: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, padding: '12px 0' }} role="alert">
      <AlertTriangle size={20} style={{ color: 'var(--nato-hostile)' }} aria-hidden/>
      <div style={{ fontSize: 13, color: 'var(--nato-hostile)' }}>Change detection failed</div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', textAlign: 'center', maxWidth: 360 }}>
        {message}
      </div>
    </div>
  );
}

function ResultBody({ result }: { result: ChangeResult }) {
  const { summary } = result;
  return (
    <div className="change-dialog-stats" style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
      gap: 12,
    }}>
      <Stat label="Features" value={result.features.length.toLocaleString()}/>
      <Stat label="Changed pixels" value={summary.changed_pixels.toLocaleString()}/>
      <Stat label="Peak diff" value={summary.peak_diff.toFixed(3)}/>
      <Stat label="Threshold" value={summary.threshold.toFixed(2)}/>
      <Stat label="AOI"
        value={`${summary.bounds[0].toFixed(3)}, ${summary.bounds[1].toFixed(3)} → ${summary.bounds[2].toFixed(3)}, ${summary.bounds[3].toFixed(3)}`}
        wide
      />
    </div>
  );
}

function Stat({ label, value, wide }: { label: string; value: string; wide?: boolean }) {
  return (
    <div
      className="change-dialog-stat"
      style={{
        padding: '10px 12px',
        background: 'var(--bg-2)',
        border: '1px solid var(--line)',
        borderRadius: 8,
        gridColumn: wide ? '1 / -1' : undefined,
      }}
    >
      <div className="label-mono" style={{ fontSize: 10 }}>{label}</div>
      <div className="mono" style={{
        fontSize: 13, color: 'var(--ink-0)',
        marginTop: 4, fontVariantNumeric: 'tabular-nums',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {value}
      </div>
    </div>
  );
}

function short(iso: string | undefined): string {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }); }
  catch { return iso; }
}

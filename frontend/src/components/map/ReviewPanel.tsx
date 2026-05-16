/**
 * Map+ Review tab — accept/flag/reject the selected detection, plus a list
 * of pending review candidates pulled from /api/detections/queue.
 */

import axios from 'axios';
import { CheckCircle2, Flag, RefreshCw, XCircle } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ModalityBadge, Panel } from '../atoms';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

type QueueRow = {
  id: number;
  class: string;
  confidence?: number;
  metadata?: any;
  lat?: number;
  lon?: number;
  pass_name?: string | null;
  acquisition_time?: string | null;
};

const STATUSES = ['pending', 'accepted', 'flagged', 'rejected'] as const;

export default function ReviewPanel({
  selectedDetection,
  onReviewed,
  onJump,
}: {
  selectedDetection: any | null;
  onReviewed?: (status: string) => void;
  onJump?: (detectionId: number) => void;
}) {
  const [status, setStatus] = useState<(typeof STATUSES)[number]>('pending');
  const [rows, setRows] = useState<QueueRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await axios.get(`${API_URL}/api/detections/queue`, {
        params: { status, limit: 25 },
      });
      setRows(data?.detections || []);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'failed to load queue');
    } finally {
      setBusy(false);
    }
  }, [status]);

  useEffect(() => {
    load();
  }, [load]);

  const detectionId = useMemo(
    () => Number(selectedDetection?.properties?.id || 0) || null,
    [selectedDetection],
  );

  const setReview = useCallback(
    async (newStatus: 'accepted' | 'flagged' | 'rejected') => {
      if (!detectionId) return;
      setActing(true);
      setError(null);
      try {
        await axios.patch(`${API_URL}/api/detections/${detectionId}/review`, { status: newStatus });
        onReviewed?.(newStatus);
        await load();
      } catch (err: any) {
        setError(err?.response?.data?.detail || err?.message || 'review failed');
      } finally {
        setActing(false);
      }
    },
    [detectionId, onReviewed, load],
  );

  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Panel
        title="Review this detection"
        sub={detectionId ? `DET-${detectionId}` : 'select a detection on the map to begin'}
      >
        <div className="review-actions" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6 }}>
          <button
            type="button"
            className="btn sm"
            disabled={!detectionId || acting}
            onClick={() => setReview('accepted')}
            style={{
              background: 'color-mix(in oklab, var(--ok) 18%, var(--bg-2))',
              color: 'var(--ok)',
              border: '1px solid var(--ok)',
              justifyContent: 'center',
              fontWeight: 600,
            }}
          >
            <CheckCircle2 size={12} /> Accept
          </button>
          <button
            type="button"
            className="btn sm"
            disabled={!detectionId || acting}
            onClick={() => setReview('flagged')}
            style={{
              background: 'color-mix(in oklab, var(--nato-unknown) 18%, var(--bg-2))',
              color: 'var(--nato-unknown)',
              border: '1px solid var(--nato-unknown)',
              justifyContent: 'center',
              fontWeight: 600,
            }}
          >
            <Flag size={12} /> Flag
          </button>
          <button
            type="button"
            className="btn sm"
            disabled={!detectionId || acting}
            onClick={() => setReview('rejected')}
            style={{
              background: 'color-mix(in oklab, var(--nato-hostile) 18%, var(--bg-2))',
              color: 'var(--nato-hostile)',
              border: '1px solid var(--nato-hostile)',
              justifyContent: 'center',
              fontWeight: 600,
            }}
          >
            <XCircle size={12} /> Reject
          </button>
        </div>
      </Panel>

      <Panel
        title="Queue"
        sub={`${rows.length} · ${status.toUpperCase()}`}
        right={
          <button type="button" className="btn xs" onClick={load} disabled={busy} title="Reload">
            <RefreshCw size={11} />
          </button>
        }
      >
        <div className="seg" style={{ display: 'flex', marginBottom: 8 }}>
          {STATUSES.map((s) => (
            <button
              key={s}
              type="button"
              className={status === s ? 'on' : ''}
              onClick={() => setStatus(s)}
              style={{ flex: 1 }}
            >
              {s.toUpperCase()}
            </button>
          ))}
        </div>
        {error && (
          <div
            className="mono"
            style={{
              fontSize: 10.5,
              color: 'var(--nato-hostile)',
              padding: '6px 8px',
              border: '1px solid var(--nato-hostile)',
              marginBottom: 8,
            }}
          >
            {error}
          </div>
        )}
        {rows.length === 0 && !busy && (
          <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
            Empty.
          </div>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {rows.map((r) => {
            const modality = (r.metadata?.modality || 'rgb') as any;
            return (
              <button
                className="review-queue-row"
                type="button"
                key={r.id}
                onClick={() => onJump?.(r.id)}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr auto auto',
                  gap: 8,
                  alignItems: 'center',
                  padding: '8px 10px',
                  border: '1px solid var(--line)',
                  background: 'var(--bg-2)',
                  borderRadius: 6,
                  cursor: 'pointer',
                  textAlign: 'left',
                  color: 'var(--ink-0)',
                }}
              >
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: 'block', fontSize: 12, fontWeight: 500 }}>
                    {r.class} <span className="mono" style={{ color: 'var(--ink-3)', fontSize: 10 }}>DET-{r.id}</span>
                  </span>
                  <span
                    className="mono"
                    style={{ display: 'block', fontSize: 9.5, color: 'var(--ink-3)' }}
                  >
                    {r.pass_name || ''}
                    {r.acquisition_time
                      ? ` · ${new Date(r.acquisition_time).toLocaleString()}`
                      : ''}
                  </span>
                </span>
                <ModalityBadge m={modality} size="xs" />
                <span
                  className="mono"
                  style={{ fontSize: 10, color: 'var(--ink-2)', minWidth: 32, textAlign: 'right' }}
                >
                  {Math.round(Number(r.confidence || 0) * 100)}%
                </span>
              </button>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}

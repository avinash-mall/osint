/**
 * Map+ Similar tab — cosine similarity over DINOv3-SAT embeddings.
 *
 * Clicking a tile selects that detection (which the parent maps to fitBounds
 * + selectedDetection). When the anchor has no embedding stored, we surface
 * the reason rather than an empty grid.
 */

import axios from 'axios';
import { RefreshCw, Sparkles } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { EmbeddingBadge, ModalityBadge, Panel } from '../atoms';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

type SimilarRow = {
  id: number;
  class: string;
  confidence?: number;
  similarity: number;
  metadata?: any;
  lat?: number;
  lon?: number;
};

export default function SimilarPanel({
  selectedDetection,
  onSelect,
}: {
  selectedDetection: any | null;
  onSelect?: (id: number) => void;
}) {
  const detectionId = Number(selectedDetection?.properties?.id || 0) || null;
  const [results, setResults] = useState<SimilarRow[]>([]);
  const [reason, setReason] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!detectionId) return;
    setBusy(true);
    setError(null);
    setReason(null);
    try {
      const { data } = await axios.get(`${API_URL}/api/detections/${detectionId}/similar`, {
        params: { k: 12 },
      });
      setResults(data?.results || []);
      setReason(data?.reason || null);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'failed to load');
    } finally {
      setBusy(false);
    }
  }, [detectionId]);

  useEffect(() => {
    load();
  }, [load]);

  if (!detectionId) {
    return (
      <div style={{ padding: 14 }}>
        <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
          Select a detection to find cosine-similar peers.
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Panel
        title={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <Sparkles size={13} /> Similar detections
          </span>
        }
        sub={`anchor DET-${detectionId} · k=12 · DINOv3 cosine`}
        right={
          <button type="button" className="btn xs" onClick={load} disabled={busy}>
            <RefreshCw size={11} />
          </button>
        }
      >
        {error && (
          <div
            className="mono"
            style={{
              fontSize: 10.5,
              color: 'var(--nato-hostile)',
              padding: '6px 8px',
              border: '1px solid var(--nato-hostile)',
            }}
          >
            {error}
          </div>
        )}
        {reason && (
          <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
            {reason}
          </div>
        )}
        {!reason && results.length === 0 && !busy && (
          <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
            No close peers.
          </div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {results.map((r) => {
            const modality = (r.metadata?.modality || 'rgb') as any;
            const embedding = (r.metadata?.embedding_head || 'sat') as any;
            return (
              <button
                key={r.id}
                type="button"
                onClick={() => onSelect?.(r.id)}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'flex-start',
                  gap: 4,
                  padding: 10,
                  border: '1px solid var(--line)',
                  background: 'var(--bg-2)',
                  color: 'var(--ink-0)',
                  cursor: 'pointer',
                  textAlign: 'left',
                  borderRadius: 6,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%' }}>
                  <span style={{ fontSize: 12, fontWeight: 500, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {r.class}
                  </span>
                  <span className="mono" style={{ fontSize: 10.5, color: 'var(--accent)' }}>
                    {(r.similarity * 100).toFixed(0)}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <ModalityBadge m={modality} size="xs" />
                  <EmbeddingBadge kind={embedding} />
                </div>
                <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>
                  DET-{r.id} · {Math.round(Number(r.confidence || 0) * 100)}% conf
                </div>
              </button>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}

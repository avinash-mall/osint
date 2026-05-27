/**
 * IdentificationPanel — top-k reference-platform candidates with approve/reject.
 *
 * Mounted by SelectionPanel's Details tab. Fetches the candidate queue for the
 * selected detection from /api/detections/{id}/identification-candidates and
 * lets the analyst approve / reject each candidate (POST to
 * /api/identification-candidates/{candidate_id}/{approve|reject}) or re-run the
 * matcher via POST /api/detections/{id}/identify.
 *
 * Architectural sibling: ReviewPanel.tsx — mirrors its load-on-mount pattern,
 * disabled-during-action pattern, and red error-chip styling. Visual scope class
 * `.identification-panel-*` follows the project convention used by
 * `.object-details-*` (see ObjectDetailsForm.tsx). axios is globally configured
 * with `withCredentials = true` in useAuth.ts, so per-request credentials are
 * not needed.
 *
 * Chip thumbnails are served by GET /api/reference-chips/{chip_id}/image
 * (inline disposition; path-traversal guarded).
 */

import axios from 'axios';
import { Check, RefreshCw, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Panel } from '../atoms';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

interface IdentificationCandidate {
  id: string;
  detection_id: number;
  platform_id: string;
  platform_name: string;
  platform_family: string;
  score: number;
  rank: number;
  matched_chip_ids: string[];
  status: 'pending' | 'approved' | 'rejected' | 'auto_applied';
  applied_at?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  created_at: string;
}

interface IdentificationCandidatesList {
  detection_id: number;
  candidates: IdentificationCandidate[];
  count: number;
}

interface Props {
  detectionId: number;
  /** Called after approve/reject/re-identify so the parent can refresh object_details. */
  onChanged?: () => void;
}

const MAX_THUMBS = 3;

export default function IdentificationPanel({ detectionId, onChanged }: Props) {
  const [candidates, setCandidates] = useState<IdentificationCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyCandidate, setBusyCandidate] = useState<string | null>(null);
  const [reidentifyBusy, setReidentifyBusy] = useState(false);

  async function load() {
    setError(null);
    try {
      const resp = await axios.get<IdentificationCandidatesList>(
        `${API_URL}/api/detections/${detectionId}/identification-candidates`,
      );
      setCandidates(resp.data?.candidates || []);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err?.message ?? 'load failed');
      setCandidates(null);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detectionId]);

  async function handleApprove(candidateId: string) {
    setBusyCandidate(candidateId);
    setError(null);
    try {
      await axios.post(
        `${API_URL}/api/identification-candidates/${candidateId}/approve`,
      );
      await load();
      onChanged?.();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err?.message ?? 'approve failed');
    } finally {
      setBusyCandidate(null);
    }
  }

  async function handleReject(candidateId: string) {
    setBusyCandidate(candidateId);
    setError(null);
    try {
      await axios.post(
        `${API_URL}/api/identification-candidates/${candidateId}/reject`,
      );
      await load();
      onChanged?.();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err?.message ?? 'reject failed');
    } finally {
      setBusyCandidate(null);
    }
  }

  async function handleReidentify() {
    setReidentifyBusy(true);
    setError(null);
    try {
      await axios.post(`${API_URL}/api/detections/${detectionId}/identify`, {
        view_domain: 'overhead',
        top_k: 3,
      });
      await load();
      onChanged?.();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? err?.message ?? 're-identify failed');
    } finally {
      setReidentifyBusy(false);
    }
  }

  const hasCandidates = !!(candidates && candidates.length > 0);
  const sorted = useMemo(() => {
    if (!candidates) return [];
    return [...candidates].sort((a, b) => a.rank - b.rank);
  }, [candidates]);

  return (
    <div
      className="identification-panel"
      data-tour="identification-panel"
      style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 12 }}
    >
      <Panel
        title="Platform identification"
        sub={`DET-${detectionId}`}
        right={
          <button
            type="button"
            className="btn xs"
            onClick={() => void handleReidentify()}
            disabled={reidentifyBusy || busyCandidate !== null}
            title="Re-run reference matcher"
            aria-busy={reidentifyBusy}
            data-tour="identification-panel-reidentify"
          >
            <RefreshCw size={11} /> {reidentifyBusy ? 'Working…' : 'Re-identify'}
          </button>
        }
      >
        {error && (
          <div
            className="mono identification-panel-error"
            role="alert"
            aria-live="assertive"
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

        {candidates === null && !error && (
          <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
            Loading…
          </div>
        )}

        {candidates !== null && !hasCandidates && (
          <div
            className="identification-panel-empty"
            style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
          >
            <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
              No identification candidates.
            </div>
            <button
              type="button"
              className="btn xs"
              onClick={() => void handleReidentify()}
              disabled={reidentifyBusy}
              style={{ alignSelf: 'flex-start' }}
            >
              <RefreshCw size={11} /> Run identify
            </button>
          </div>
        )}

        {hasCandidates && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {sorted.map((c) => (
              <CandidateCard
                key={c.id}
                candidate={c}
                busy={busyCandidate === c.id}
                anyBusy={busyCandidate !== null || reidentifyBusy}
                onApprove={() => void handleApprove(c.id)}
                onReject={() => void handleReject(c.id)}
              />
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}

/* ─── Subcomponents ──────────────────────────────────────────────────── */

function CandidateCard({
  candidate,
  busy,
  anyBusy,
  onApprove,
  onReject,
}: {
  candidate: IdentificationCandidate;
  busy: boolean;
  anyBusy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const pct = `${(candidate.score * 100).toFixed(1)}%`;
  const thumbs = (candidate.matched_chip_ids || []).slice(0, MAX_THUMBS);
  const canAct = candidate.status === 'pending' || candidate.status === 'auto_applied';
  const auto = candidate.status === 'auto_applied';
  const borderColor = auto
    ? 'color-mix(in oklab, var(--accent) 55%, var(--line))'
    : 'var(--line)';

  return (
    <div
      className="identification-panel-card"
      data-status={candidate.status}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        padding: '10px 12px',
        background: 'var(--bg-2)',
        border: `1px solid ${borderColor}`,
        borderRadius: 4,
      }}
    >
      <div
        className="identification-panel-card-head"
        style={{
          display: 'grid',
          gridTemplateColumns: 'auto 1fr auto',
          gap: 8,
          alignItems: 'baseline',
        }}
      >
        <span
          className="mono"
          style={{
            fontSize: 11,
            color: 'var(--ink-2)',
            fontVariantNumeric: 'tabular-nums',
            minWidth: 22,
          }}
          title={`Rank ${candidate.rank}`}
        >
          #{candidate.rank}
        </span>
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              fontSize: 12.5,
              fontWeight: 600,
              color: 'var(--ink-0)',
              lineHeight: 1.2,
              wordBreak: 'break-word',
            }}
          >
            {candidate.platform_name}
          </div>
          {candidate.platform_family && (
            <div
              className="mono"
              style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 2 }}
            >
              {candidate.platform_family}
            </div>
          )}
        </div>
        <span
          className="mono"
          style={{
            fontSize: 11,
            color: 'var(--ink-1)',
            fontVariantNumeric: 'tabular-nums',
            textAlign: 'right',
          }}
          title="Match score"
        >
          {pct}
        </span>
      </div>

      {thumbs.length > 0 && (
        <div
          className="identification-panel-thumbs"
          style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}
        >
          {thumbs.map((cid) => (
            <img
              key={cid}
              src={`${API_URL}/api/reference-chips/${cid}/image`}
              alt={`Reference chip ${cid}`}
              loading="lazy"
              onError={(e) => {
                const img = e.currentTarget;
                if (img.dataset.fallback !== '1') {
                  img.dataset.fallback = '1';
                  img.src =
                    "data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'%3E%3Crect width='1' height='1' fill='%23222' /%3E%3C/svg%3E";
                  img.title = 'chip image unavailable';
                  img.style.opacity = '0.4';
                }
              }}
              style={{
                inlineSize: 56,
                blockSize: 56,
                objectFit: 'cover',
                background: 'var(--bg-1)',
                border: '1px solid var(--line)',
                borderRadius: 3,
                display: 'block',
              }}
            />
          ))}
        </div>
      )}

      <div
        className="identification-panel-card-foot"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flexWrap: 'wrap',
        }}
      >
        <StatusTag status={candidate.status} />
        <span style={{ flex: 1 }} />
        {canAct && (
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              type="button"
              className="btn xs"
              onClick={onApprove}
              disabled={anyBusy}
              aria-busy={busy}
              style={{
                background: 'color-mix(in oklab, var(--ok) 18%, var(--bg-2))',
                color: 'var(--ok)',
                border: '1px solid var(--ok)',
                fontWeight: 600,
              }}
            >
              <Check size={11} /> Approve
            </button>
            <button
              type="button"
              className="btn xs"
              onClick={onReject}
              disabled={anyBusy}
              aria-busy={busy}
              style={{
                background: 'color-mix(in oklab, var(--nato-hostile) 18%, var(--bg-2))',
                color: 'var(--nato-hostile)',
                border: '1px solid var(--nato-hostile)',
                fontWeight: 600,
              }}
            >
              <X size={11} /> Reject
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function StatusTag({ status }: { status: IdentificationCandidate['status'] }) {
  const meta = STATUS_META[status];
  return (
    <span
      className="mono"
      style={{
        fontSize: 9.5,
        letterSpacing: '.08em',
        textTransform: 'uppercase',
        padding: '2px 7px',
        color: meta.color,
        background: `color-mix(in oklab, ${meta.color} 14%, var(--bg-2))`,
        border: `1px solid color-mix(in oklab, ${meta.color} 55%, var(--line))`,
        borderRadius: 2,
      }}
    >
      {meta.label}
    </span>
  );
}

const STATUS_META: Record<
  IdentificationCandidate['status'],
  { label: string; color: string }
> = {
  pending: { label: 'Pending', color: 'var(--ink-2)' },
  auto_applied: { label: 'Auto-applied', color: 'var(--accent)' },
  approved: { label: 'Approved', color: 'var(--ok)' },
  rejected: { label: 'Rejected', color: 'var(--nato-hostile)' },
};

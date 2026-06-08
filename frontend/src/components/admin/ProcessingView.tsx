/**
 * ProcessingView — Admin · Processing tab.
 *
 * Extracted from the monolithic AdminScreen.tsx. Single responsibility:
 * list analytics + training jobs, filter by status, surface clickable
 * cross-nav buttons for jobs that produced detections.
 *
 * Polling cadence:
 *   - Tight (3 s) while any job is running / queued; relaxed (15 s) otherwise.
 *   - One interval, owned for the component's lifetime — same pattern as Shell.
 */

import axios from 'axios';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Film, Map as MapIcon, RefreshCw,
} from 'lucide-react';
import ViewHeader from './ViewHeader';
import { relativeTime } from './time';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

const FAST_TICK = 3000;
const SLOW_TICK = 15000;

type JobRow = {
  id: string | number;
  title: string;
  model: string;
  stage: string;
  status: string;
  created_at?: string | null;
  pct?: number;
  raw_source: 'analytics' | 'training';
  /** When the analytics job produced a detection, surfaces a click target. */
  detection_id?: number;
  fmv_clip_id?: number;
};

type Filter = 'all' | 'running' | 'queued' | 'done' | 'failed';

type Props = {
  onCount: (n: number) => void;
  onOpenOnMap?: (detectionId: number, className?: string) => void;
  onOpenInFmv?: (detectionId: number) => void;
};

export default function ProcessingView({ onCount, onOpenOnMap, onOpenInFmv }: Props) {
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [filter, setFilter] = useState<Filter>('all');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const activeRef = useRef(0);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [a, t] = await Promise.allSettled([
        axios.get<{ jobs?: any[] }>(`${API_URL}/api/analytics/jobs`),
        axios.get<{ jobs?: any[] }>(`${API_URL}/api/training/jobs`),
      ]);
      const aRows: JobRow[] = a.status === 'fulfilled'
        ? (a.value.data.jobs ?? []).map((j) => ({
            id: j.id,
            title: j.input?.title || j.job_type || `analytics:${j.id}`,
            model: j.job_type || 'analytics',
            stage: j.status || 'queued',
            status: j.status || 'queued',
            created_at: j.created_at,
            pct: j.status === 'completed' || j.status === 'done' ? 1 : j.status === 'running' ? 0.5 : 0,
            raw_source: 'analytics' as const,
            detection_id: j.output?.detection_id,
            fmv_clip_id: j.output?.fmv_clip_id,
          }))
        : [];
      const tRows: JobRow[] = t.status === 'fulfilled'
        ? (t.value.data.jobs ?? []).map((j) => ({
            id: j.id,
            title: j.dataset_name || `training:${j.id}`,
            model: 'training',
            stage: j.status || 'queued',
            status: j.status || 'queued',
            created_at: j.created_at,
            pct: j.status === 'completed' || j.status === 'done' ? 1 : j.status === 'running' ? 0.5 : 0,
            raw_source: 'training' as const,
          }))
        : [];
      const all = [...aRows, ...tRows];
      activeRef.current = all.filter((j) => j.status === 'running' || j.status === 'queued').length;
      setJobs(all);
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  /** Adaptive polling — single timer, re-schedules itself. */
  useEffect(() => {
    let cancelled = false;
    let id: number | undefined;
    const tick = async () => {
      if (cancelled) return;
      await load();
      if (cancelled) return;
      const cadence = activeRef.current > 0 ? FAST_TICK : SLOW_TICK;
      id = window.setTimeout(tick, cadence);
    };
    tick();
    return () => {
      cancelled = true;
      if (id != null) window.clearTimeout(id);
    };
  }, [load]);

  useEffect(() => { onCount(activeRef.current); }, [jobs, onCount]);

  const visible = useMemo(() => {
    if (filter === 'all') return jobs;
    return jobs.filter((j) => {
      if (filter === 'done') return j.status === 'completed' || j.status === 'done';
      if (filter === 'failed') return j.status === 'failed' || j.status === 'error';
      return j.status === filter;
    });
  }, [jobs, filter]);

  return (
    <>
      <ViewHeader
        title="Processing jobs"
        sub={`${jobs.length} jobs across analytics + training`}
        actions={
          <>
            <div className="seg" role="tablist" aria-label="Status filter">
              {(['all', 'running', 'queued', 'done', 'failed'] as Filter[]).map((f) => (
                <button
                  key={f}
                  role="tab"
                  aria-selected={filter === f}
                  className={filter === f ? 'on' : ''}
                  onClick={() => setFilter(f)}
                  type="button"
                >
                  {f.toUpperCase()}
                </button>
              ))}
            </div>
            <button
              className="btn sm" onClick={load} type="button"
              title="Refresh" aria-label="Refresh jobs"
              aria-busy={loading}
            >
              <RefreshCw size={12}/>
            </button>
          </>
        }
      />
      <div
        className="scroll admin-jobs-list"
        style={{
          flex: 1, padding: 18,
          display: 'flex', flexDirection: 'column', gap: 8,
          containerType: 'inline-size', containerName: 'jobs-list',
        }}
      >
        {err && (
          <div className="card" role="alert" style={{ padding: 14, borderLeft: '3px solid var(--crit)' }}>
            <div style={{ color: 'var(--crit)', fontSize: 12 }}>Failed to load jobs: {err}</div>
          </div>
        )}
        {!err && !loading && visible.length === 0 && (
          <div className="mono" style={{ color: 'var(--ink-2)', padding: 12, fontSize: 11 }}>
            No jobs in this view.
          </div>
        )}
        {visible.map((j) => {
          const color =
            j.status === 'running' ? 'var(--accent)' :
            j.status === 'completed' || j.status === 'done' ? 'var(--ok)' :
            j.status === 'failed' || j.status === 'error' ? 'var(--nato-hostile)' : 'var(--ink-2)';
          const pct = j.pct ?? 0;
          return (
            <div key={`${j.raw_source}-${j.id}`} className="card admin-job-card" style={{ padding: 14 }}>
              <div className="admin-job-row" style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 10 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>
                      {j.raw_source}#{j.id}
                    </span>
                    <span style={{ fontSize: 13, fontWeight: 500 }}>{j.title}</span>
                  </div>
                  <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 3 }}>
                    {j.model} · {j.stage} · {relativeTime(j.created_at)}
                  </div>
                </div>
                <div className="admin-job-actions" style={{
                  textAlign: 'right',
                  display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end',
                }}>
                  {j.detection_id != null && onOpenOnMap && (
                    <button
                      className="btn xs" type="button"
                      onClick={() => onOpenOnMap(j.detection_id!)}
                      title="Open detection on GEOINT map"
                    >
                      <MapIcon size={11}/> Map
                    </button>
                  )}
                  {j.fmv_clip_id != null && onOpenInFmv && (
                    <button
                      className="btn xs" type="button"
                      onClick={() => onOpenInFmv(j.detection_id ?? j.fmv_clip_id!)}
                      title="Open in FMV player"
                    >
                      <Film size={11}/> FMV
                    </button>
                  )}
                  <span className="mono" style={{ fontSize: 11, color, letterSpacing: '.08em' }}>
                    {j.status.toUpperCase()}
                  </span>
                </div>
              </div>
              <div style={{ marginTop: 10, height: 3, background: 'var(--bg-3)' }} aria-hidden>
                <div style={{ width: `${pct * 100}%`, height: '100%', background: color }}/>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

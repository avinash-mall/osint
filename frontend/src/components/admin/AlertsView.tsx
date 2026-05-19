/**
 * AlertsView — Admin · Health alerts tab.
 *
 * Extracted from the monolithic AdminScreen.tsx. Live-polls /api/alerts.
 */

import axios from 'axios';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import ViewHeader from './ViewHeader';
import { relativeTime } from './time';
import { StatusDot } from '../atoms';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

const TICK_MS = 15000;

type AlertRow = {
  id: string;
  severity: 'high' | 'medium' | 'low' | string;
  title: string;
  source: string;
  detail?: string;
  at: string;
};

type Props = { onCount: (n: number) => void };

export default function AlertsView({ onCount }: Props) {
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setErr(null);
    try {
      const r = await axios.get<{ alerts?: AlertRow[] }>(`${API_URL}/api/alerts`);
      setAlerts(r.data.alerts ?? []);
    } catch (e: any) {
      setErr(e?.message ?? String(e));
      setAlerts([]);
    } finally {
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    load();
    const id = window.setInterval(() => { if (!cancelled) load(); }, TICK_MS);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [load]);

  useEffect(() => { onCount(alerts.length); }, [alerts.length, onCount]);

  return (
    <>
      <ViewHeader
        title="Health alerts"
        sub={`${alerts.length} active · derived from /api/health + ingest failures`}
        actions={
          <button className="btn sm" type="button" onClick={load} aria-label="Refresh alerts">
            <RefreshCw size={12}/>
          </button>
        }
      />
      <div
        className="scroll admin-alerts-list"
        style={{
          flex: 1, padding: 18,
          display: 'flex', flexDirection: 'column', gap: 8,
          containerType: 'inline-size', containerName: 'alerts-list',
        }}
        role="region" aria-live="polite"
      >
        {err && (
          <div className="card" role="alert" style={{ padding: 14, borderLeft: '3px solid var(--crit)' }}>
            <div style={{ color: 'var(--crit)', fontSize: 12 }}>Failed to load alerts: {err}</div>
          </div>
        )}
        {!err && alerts.length === 0 && (
          <div className="card" style={{
            padding: 14, borderLeft: '3px solid var(--ok)',
            display: 'flex', alignItems: 'center', gap: 10,
          }}>
            <StatusDot tone="ok" pulse/>
            <div style={{ fontSize: 13 }}>All systems nominal · no active alerts.</div>
          </div>
        )}
        {alerts.map((a) => {
          const tone =
            a.severity === 'high' ? 'var(--nato-hostile)' :
            a.severity === 'medium' ? 'var(--nato-unknown)' : 'var(--ink-2)';
          return (
            <div key={a.id} className="card admin-alert-card" style={{ padding: 14, borderLeft: `3px solid ${tone}` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <AlertTriangle size={16} style={{ color: tone }} aria-hidden/>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{a.title}</div>
                  <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>
                    {a.source} · {relativeTime(a.at)}
                  </div>
                  {a.detail && (
                    <div className="mono" style={{
                      fontSize: 10.5, color: 'var(--ink-2)',
                      marginTop: 6, whiteSpace: 'pre-wrap',
                    }}>
                      {a.detail}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

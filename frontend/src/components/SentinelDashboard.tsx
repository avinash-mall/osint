import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Activity, AlertTriangle, Bot, Database, FileText } from 'lucide-react';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080';

function Metric({ label, value, detail, tone = 'text-sentinel-accent', className = '' }: { label: string; value: any; detail?: string; tone?: string; className?: string }) {
  return (
    <div className={`sentinel-panel p-4 min-w-0 ${className}`}>
      <div className="sentinel-label">{label}</div>
      <div className={`mt-1 font-mono text-2xl font-semibold ${tone}`}>{value ?? 0}</div>
      {detail && <div className="mt-1 font-mono text-[10px] text-sentinel-muted truncate">{detail}</div>}
    </div>
  );
}

export default function SentinelDashboard() {
  const [summary, setSummary] = useState<any>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const load = async () => {
      try {
        const response = await axios.get(`${API_URL}/api/dashboard/summary`);
        setSummary(response.data);
        setError('');
      } catch {
        setError('Dashboard context unavailable.');
      }
    };
    load();
    const id = window.setInterval(load, 30000);
    return () => window.clearInterval(id);
  }, []);

  const counts = summary?.counts || {};
  const sourceMix = useMemo(() => summary?.observations_by_domain || [], [summary]);
  const maxSource = Math.max(1, ...sourceMix.map((item: any) => Number(item.count || 0)));

  return (
    <div className="h-full overflow-auto bg-sentinel-bg p-1">
      <div className="grid min-h-full grid-cols-12 gap-px bg-sentinel-line">
        <Metric className="col-span-6 lg:col-span-2" label="Active Targets" value={counts.targets} detail={`${counts.high_priority_targets || 0} high priority`} tone="text-sentinel-accent" />
        <Metric className="col-span-6 lg:col-span-2" label="Events / 24H" value={counts.recent_events} detail="timeline events" tone="text-sentinel-info" />
        <Metric className="col-span-6 lg:col-span-2" label="Feeds Up" value={counts.active_sources} detail="enabled sources" tone="text-sentinel-ok" />
        <Metric className="col-span-6 lg:col-span-2" label="Uploads" value={counts.uploads} detail="cataloged jobs" tone="text-slate-100" />
        <Metric className="col-span-6 lg:col-span-2" label="AI Actions" value={counts.pending_actions} detail="pending approval" tone="text-sentinel-warn" />
        <Metric className="col-span-6 lg:col-span-2" label="Models" value={summary?.models?.length || 0} detail={summary?.ai?.model || 'model unavailable'} tone="text-sentinel-ok" />

        <section className="sentinel-panel col-span-12 xl:col-span-8 min-h-80">
          <div className="sentinel-panel-header">
            <Activity className="h-4 w-4" />
            <span>Global Activity</span>
            <span className="ml-auto font-mono text-[10px] text-sentinel-muted">{counts.observations || 0} observations</span>
          </div>
          <div className="relative h-80 overflow-hidden bg-[#0a0d10]">
            <div className="sentinel-grid" />
            <svg viewBox="0 0 1000 350" preserveAspectRatio="none" className="absolute inset-0 h-full w-full opacity-70">
              <g fill="#1a2128" stroke="#2a3540" strokeWidth=".9">
                <path d="M120,140 L260,110 L320,160 L300,230 L240,260 L150,250 L100,200 Z" />
                <path d="M340,150 L470,130 L520,200 L490,290 L420,310 L360,270 L320,210 Z" />
                <path d="M540,120 L720,100 L820,150 L840,220 L760,280 L630,290 L560,240 Z" />
                <path d="M780,260 L880,250 L900,320 L820,335 Z" />
                <path d="M180,290 L260,280 L290,330 L220,340 Z" />
              </g>
            </svg>
            {(summary?.priority_targets || []).map((target: any, index: number) => {
              const lat = Number(target.properties?.latitude || 0);
              const lon = Number(target.properties?.longitude || 0);
              const x = ((lon + 180) / 360) * 100;
              const y = (1 - (lat + 85) / 170) * 100;
              return (
                <div key={target.id || index} className="absolute -translate-x-1/2 -translate-y-1/2" style={{ left: `${x}%`, top: `${y}%` }}>
                  <div className="h-4 w-4 border border-sentinel-crit text-sentinel-crit">
                    <div className="m-[5px] h-1 w-1 bg-current" />
                  </div>
                  <div className="mt-1 whitespace-nowrap border border-sentinel-line bg-sentinel-panel px-2 py-1 font-mono text-[10px] text-slate-200">
                    {target.properties?.name || target.id}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="sentinel-panel col-span-12 xl:col-span-4">
          <div className="sentinel-panel-header">
            <AlertTriangle className="h-4 w-4" />
            <span>Recent Timeline</span>
          </div>
          <div className="sentinel-scroll max-h-80">
            {(summary?.timeline || []).slice(0, 10).map((event: any) => (
              <div key={event.id} className="sentinel-row grid-cols-[80px_1fr]">
                <span className="font-mono text-[10px] text-sentinel-muted">{event.domain || 'SYS'}</span>
                <div className="min-w-0">
                  <div className="truncate text-xs text-slate-200">{event.title || event.event_type}</div>
                  <div className="truncate font-mono text-[10px] text-sentinel-muted">{event.event_type}</div>
                </div>
              </div>
            ))}
            {error && <div className="p-4 text-xs text-sentinel-warn">{error}</div>}
          </div>
        </section>

        <section className="sentinel-panel col-span-12 xl:col-span-4">
          <div className="sentinel-panel-header"><Database className="h-4 w-4" /><span>Source Mix</span></div>
          <div className="p-4">
            {sourceMix.map((item: any) => (
              <div key={item.domain} className="mb-3">
                <div className="mb-1 flex justify-between text-xs">
                  <span>{item.domain}</span>
                  <span className="font-mono text-sentinel-muted">{item.count}</span>
                </div>
                <div className="h-1.5 bg-sentinel-bg">
                  <div className="h-full bg-sentinel-accent" style={{ width: `${(Number(item.count || 0) / maxSource) * 100}%` }} />
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="sentinel-panel col-span-12 xl:col-span-4">
          <div className="sentinel-panel-header"><Bot className="h-4 w-4" /><span>Models</span></div>
          {(summary?.models || []).map((model: any) => (
            <div key={model.id} className="sentinel-row grid-cols-[1fr_auto]">
              <div className="min-w-0">
                <div className="truncate text-xs text-slate-200">{model.name}</div>
                <div className="font-mono text-[10px] text-sentinel-muted">{model.version || 'version n/a'}</div>
              </div>
              <span className={`sentinel-tag ${model.promoted ? 'ok' : ''}`}>{model.status}</span>
            </div>
          ))}
        </section>

        <section className="sentinel-panel col-span-12 xl:col-span-4">
          <div className="sentinel-panel-header"><FileText className="h-4 w-4" /><span>Queues</span></div>
          <div className="grid grid-cols-2 gap-px bg-sentinel-line">
            <Metric label="Reports" value={counts.reports} />
            <Metric label="Training" value={counts.training_jobs} tone="text-sentinel-warn" />
            <Metric label="FMV Clips" value={counts.fmv_clips} tone="text-sentinel-info" />
            <Metric label="Sources" value={counts.active_sources} tone="text-sentinel-ok" />
          </div>
        </section>
      </div>
    </div>
  );
}

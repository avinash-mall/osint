import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { AlertTriangle, CheckCircle2, Clock3, Crosshair, RadioTower, Send, Target } from 'lucide-react';
import { useEventStream } from '../hooks/useEventStream';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080';

export default function SentinelWatch() {
  const [summary, setSummary] = useState<any>(null);
  const [ops, setOps] = useState<any>({ targets: [], summary: {} });
  const [tasks, setTasks] = useState<any[]>([]);
  const [selectedEventId, setSelectedEventId] = useState<number | string>('');

  const refresh = useCallback(async () => {
    const [dashboardResponse, opsResponse, tasksResponse] = await Promise.all([
      axios.get(`${API_URL}/api/dashboard/summary`),
      axios.get(`${API_URL}/api/ops/targets`),
      axios.get(`${API_URL}/api/collection/tasks`),
    ]);
    setSummary(dashboardResponse.data);
    setOps(opsResponse.data || { targets: [], summary: {} });
    setTasks(tasksResponse.data.tasks || []);
    setSelectedEventId((current) => current || dashboardResponse.data?.timeline?.[0]?.id || '');
  }, []);

  useEffect(() => {
    refresh().catch(() => undefined);
  }, [refresh]);

  useEventStream('ops', useCallback(() => {
    refresh().catch(() => undefined);
  }, [refresh]));

  const events = summary?.timeline || [];
  const selectedEvent = events.find((event: any) => event.id === selectedEventId) || events[0] || null;
  const priorityTargets = useMemo(() => ops.targets.filter((target: any) => target.properties?.priority === 'High').slice(0, 8), [ops.targets]);

  return (
    <div className="grid h-full min-h-0 grid-cols-[320px_minmax(0,1fr)_340px] gap-px bg-sentinel-line">
      <section className="sentinel-panel min-h-0 border-0">
        <div className="sentinel-panel-header">
          <AlertTriangle className="h-4 w-4" />
          <span>Alert Queue</span>
          <span className="sentinel-tag warn ml-auto">{events.length}</span>
        </div>
        <div className="sentinel-scroll">
          {events.map((event: any) => (
            <button
              key={event.id}
              type="button"
              onClick={() => setSelectedEventId(event.id)}
              className={`sentinel-row w-full grid-cols-[54px_1fr_auto] text-left ${selectedEvent?.id === event.id ? 'selected' : ''}`}
            >
              <span className="sentinel-tag info">{event.domain || 'SYS'}</span>
              <span className="min-w-0">
                <span className="block truncate text-xs text-slate-200">{event.title || event.event_type}</span>
                <span className="block truncate font-mono text-[10px] text-sentinel-muted">{event.event_type}</span>
              </span>
              <Clock3 className="h-3.5 w-3.5 text-sentinel-muted" />
            </button>
          ))}
        </div>
      </section>

      <section className="sentinel-panel min-h-0 border-0">
        <div className="sentinel-panel-header">
          <span className="sentinel-tag acc">WATCH</span>
          <span>{selectedEvent?.event_type || 'No event selected'}</span>
          <span className="ml-auto font-mono text-[10px] text-sentinel-muted">targets {ops.summary?.total || 0}</span>
        </div>
        <div className="sentinel-scroll p-4">
          {selectedEvent ? (
            <div className="space-y-4">
              <div>
                <div className="sentinel-label">Event Detail</div>
                <h2 className="mt-2 text-xl font-semibold text-slate-100">{selectedEvent.title || selectedEvent.event_type}</h2>
                <div className="mt-2 flex flex-wrap gap-2">
                  <span className="sentinel-tag info">DOMAIN {selectedEvent.domain || 'WORKFLOW'}</span>
                  <span className="sentinel-tag">ID {selectedEvent.id}</span>
                  <span className="sentinel-tag">{new Date(selectedEvent.occurred_at || selectedEvent.created_at || Date.now()).toLocaleString()}</span>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-px bg-sentinel-line">
                <div className="sentinel-panel p-4">
                  <div className="sentinel-label">Ready Targets</div>
                  <div className="mt-1 font-mono text-2xl text-sentinel-ok">{ops.summary?.ready || 0}</div>
                </div>
                <div className="sentinel-panel p-4">
                  <div className="sentinel-label">Tasked</div>
                  <div className="mt-1 font-mono text-2xl text-sentinel-accent">{ops.summary?.tasked || 0}</div>
                </div>
                <div className="sentinel-panel p-4">
                  <div className="sentinel-label">Open Tasks</div>
                  <div className="mt-1 font-mono text-2xl text-sentinel-warn">{tasks.length}</div>
                </div>
              </div>

              <div className="sentinel-panel">
                <div className="sentinel-panel-header">
                  <Target className="h-4 w-4" />
                  <span>High Priority Targets</span>
                </div>
                <div className="grid grid-cols-2 gap-px bg-sentinel-line">
                  {priorityTargets.map((target: any) => (
                    <div key={target.id} className="bg-sentinel-panel p-3">
                      <div className="flex items-center gap-2">
                        <Crosshair className="h-4 w-4 text-sentinel-crit" />
                        <span className="truncate text-xs font-semibold text-slate-100">{target.properties?.name || target.id}</span>
                        <span className="sentinel-tag crit ml-auto">HIGH</span>
                      </div>
                      <div className="mt-2 font-mono text-[10px] text-sentinel-muted">
                        {target.queue} / {target.readiness} / tasks {target.task_count}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="grid h-full place-items-center text-xs text-sentinel-muted">No watch events available.</div>
          )}
        </div>
      </section>

      <section className="sentinel-panel min-h-0 border-0">
        <div className="sentinel-panel-header">
          <RadioTower className="h-4 w-4" />
          <span>Tasking Queue</span>
        </div>
        <div className="sentinel-scroll">
          {tasks.slice(0, 14).map((task) => (
            <div key={task.id} className="border-b border-sentinel-line p-3">
              <div className="flex items-center gap-2">
                <span className={`sentinel-tag ${task.priority === 'High' ? 'crit' : task.priority === 'Medium' ? 'warn' : 'info'}`}>{task.priority || 'TASK'}</span>
                <span className="font-mono text-[10px] text-sentinel-muted">TSK-{task.id}</span>
                <span className={`sentinel-tag ml-auto ${task.status === 'complete' ? 'ok' : ''}`}>{task.status}</span>
              </div>
              <div className="mt-2 text-xs text-slate-200">{task.target_name || task.target_id}</div>
              <div className="mt-1 font-mono text-[10px] text-sentinel-muted">{task.asset_type} / {task.queue || 'queue n/a'}</div>
            </div>
          ))}
          <div className="p-3">
            <button type="button" className="sentinel-btn primary w-full justify-center">
              <Send className="h-3.5 w-3.5" /> Task From Target Workspace
            </button>
          </div>
          <div className="sentinel-panel-header border-t border-sentinel-line">
            <CheckCircle2 className="h-4 w-4" />
            <span>Watch Status</span>
          </div>
          <div className="p-3 text-xs text-sentinel-muted">
            Live operations queue mirrors existing collection task data. Detailed task creation remains in the operations and target workspaces.
          </div>
        </div>
      </section>
    </div>
  );
}

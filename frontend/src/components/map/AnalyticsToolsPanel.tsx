/**
 * Terrain analytics tools panel: Viewshed, Line-of-Sight, and Routes.
 *
 * Renders all three tools stacked in one scrollable panel. Each tool keeps
 * its own observer/destination/parameter state, so picking an observer for
 * Viewshed does not blow away a different observer staged for Line of Sight.
 *
 * Picks are orchestrated by the parent map: a pick request flows out via
 * `onRequestPick`, and the resulting click point flows back in via
 * `lastMapClick` (the `pickFor` field tells us which tool/slot to fill).
 * Results POST to the matching backend endpoint and hand the GeoJSON back
 * through `onResult`; the parent owns layer-toggle state.
 */

import { Crosshair, Eye, EyeOff, Route as RouteIcon, Spline, Sparkles } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import {
  getCapabilities,
  runLineOfSight,
  runRoutes,
  runViewshed,
  type AnalyticsCapabilities,
  type AnalyticsMode,
  type AnalyticsResponse,
  type LatLon,
} from '../../services/analytics';

// Phase 7.34: when the backend returns a fixture mode (no DEM, no graph, or
// fully offline) the geometry returned looks plausible on the map but is NOT
// real terrain/routing output. Surface this to the analyst as a red banner so
// they don't act on canned shapes.
const FALLBACK_MODES = new Set<string>([
  'fixture_no_dem',
  'fixture_no_passes',
  'fixture_no_graph',
  'offline_fixture',
]);
function isFallbackMode(mode: AnalyticsMode | undefined): boolean {
  return mode != null && FALLBACK_MODES.has(String(mode));
}

export type AnalyticsKind = 'viewshed' | 'los' | 'routes';

export type AnalyticsPick = 'viewshed.observer' | 'los.observer' | 'los.target' | 'routes.start' | 'routes.end';

export type AnalyticsLayerStatus = { on: boolean; disabled: boolean };

type Props = {
  /** Pending pick the parent map should react to (sets cursor crosshair). */
  pendingPick: AnalyticsPick | null;
  /** Called when this panel begins / cancels a pick request. */
  onRequestPick: (pick: AnalyticsPick | null) => void;
  /** Last point the user clicked on the map; the parent clears it after we read it. */
  lastMapClick: { lat: number; lon: number; pickFor: AnalyticsPick | null } | null;
  /** Notify parent of fresh results so it can render + toggle the layer. */
  onResult: (kind: AnalyticsKind, response: AnalyticsResponse | null) => void;
  /** Per-tool layer toggle status. */
  layers: Record<AnalyticsKind, AnalyticsLayerStatus>;
  /** Flip a specific tool's map-layer visibility. */
  onToggleLayer: (kind: AnalyticsKind) => void;
};

type ToolState = {
  observer: LatLon | null;
  destination: LatLon | null;
  radius: number;
  observerHeight: number;
  targetHeight: number;
  strategy: 'shortest' | 'least_exposure' | 'balanced' | 'all';
  busy: boolean;
  error: string | null;
};

const initialTool: ToolState = {
  observer: null,
  destination: null,
  radius: 5000,
  observerHeight: 1.8,
  targetHeight: 0,
  strategy: 'all',
  busy: false,
  error: null,
};

type ToolStates = Record<AnalyticsKind, ToolState>;

const initialToolStates: ToolStates = {
  viewshed: { ...initialTool },
  los: { ...initialTool },
  routes: { ...initialTool },
};

function toolForPick(pick: AnalyticsPick): { kind: AnalyticsKind; slot: 'observer' | 'destination' } {
  switch (pick) {
    case 'viewshed.observer': return { kind: 'viewshed', slot: 'observer' };
    case 'los.observer':      return { kind: 'los',      slot: 'observer' };
    case 'los.target':        return { kind: 'los',      slot: 'destination' };
    case 'routes.start':      return { kind: 'routes',   slot: 'observer' };
    case 'routes.end':        return { kind: 'routes',   slot: 'destination' };
  }
}

function pickLabel(point: LatLon | null, placeholder: string): string {
  if (!point) return placeholder;
  return `${point.latitude.toFixed(4)}, ${point.longitude.toFixed(4)}`;
}

export default function AnalyticsToolsPanel({
  pendingPick,
  onRequestPick,
  lastMapClick,
  onResult,
  layers,
  onToggleLayer,
}: Props) {
  const [tools, setTools] = useState<ToolStates>(initialToolStates);
  const [capabilities, setCapabilities] = useState<AnalyticsCapabilities | null>(null);

  useEffect(() => {
    let cancelled = false;
    getCapabilities()
      .then((c) => { if (!cancelled) setCapabilities(c); })
      .catch(() => { if (!cancelled) setCapabilities({ dem: false, routing_graph: false }); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!lastMapClick || !lastMapClick.pickFor) return;
    const { lat, lon, pickFor } = lastMapClick;
    const { kind, slot } = toolForPick(pickFor);
    setTools((prev) => ({
      ...prev,
      [kind]: { ...prev[kind], [slot]: { latitude: lat, longitude: lon } },
    }));
    onRequestPick(null);
  }, [lastMapClick, onRequestPick]);

  const startPick = useCallback((slot: AnalyticsPick) => {
    onRequestPick(pendingPick === slot ? null : slot);
  }, [onRequestPick, pendingPick]);

  const updateTool = useCallback((kind: AnalyticsKind, patch: Partial<ToolState>) => {
    setTools((prev) => ({ ...prev, [kind]: { ...prev[kind], ...patch } }));
  }, []);

  const [lastModeByKind, setLastModeByKind] = useState<Record<AnalyticsKind, AnalyticsMode | undefined>>({
    viewshed: undefined,
    los: undefined,
    routes: undefined,
  });

  const recordMode = useCallback((kind: AnalyticsKind, res: AnalyticsResponse | null) => {
    setLastModeByKind((prev) => ({ ...prev, [kind]: res?.result?.mode }));
  }, []);

  const onRun = useCallback(async (kind: AnalyticsKind) => {
    const s = tools[kind];
    updateTool(kind, { busy: true, error: null });
    try {
      if (kind === 'viewshed') {
        if (!s.observer) throw new Error('Pick an observer first');
        const res = await runViewshed({
          observer: s.observer,
          radius_m: s.radius,
          observer_height_m: s.observerHeight,
          target_height_m: s.targetHeight,
        });
        onResult('viewshed', res);
        recordMode('viewshed', res);
      } else if (kind === 'los') {
        if (!s.observer || !s.destination) throw new Error('Pick both observer and target');
        const res = await runLineOfSight({
          observer: s.observer,
          destination: s.destination,
          observer_height_m: s.observerHeight,
          target_height_m: s.targetHeight,
        });
        onResult('los', res);
        recordMode('los', res);
      } else {
        if (!s.observer || !s.destination) throw new Error('Pick both start and end');
        const res = await runRoutes({
          observer: s.observer,
          destination: s.destination,
          strategy: s.strategy === 'all' ? undefined : s.strategy,
        });
        onResult('routes', res);
        recordMode('routes', res);
      }
    } catch (e: any) {
      updateTool(kind, { error: e?.message ?? String(e) });
      onResult(kind, null);
      recordMode(kind, null);
    } finally {
      updateTool(kind, { busy: false });
    }
  }, [tools, updateTool, onResult, recordMode]);

  const onClear = useCallback((kind: AnalyticsKind) => {
    updateTool(kind, { observer: null, destination: null });
    onResult(kind, null);
    recordMode(kind, null);
  }, [updateTool, onResult, recordMode]);

  const vs = tools.viewshed;
  const los = tools.los;
  const rt = tools.routes;

  return (
    <div className="bg-sentinel-panel-2 px-3 py-2 space-y-2">
      <ToolCard
        icon={<Eye className="h-3.5 w-3.5" />}
        title="Viewshed"
        busy={vs.busy}
        error={vs.error}
        onRun={() => onRun('viewshed')}
        onClear={() => onClear('viewshed')}
        runDisabled={!vs.observer || vs.busy}
        layerOn={layers.viewshed.on}
        layerDisabled={layers.viewshed.disabled}
        onToggleLayer={() => onToggleLayer('viewshed')}
        fallbackMode={isFallbackMode(lastModeByKind.viewshed) ? lastModeByKind.viewshed : undefined}
      >
        <FieldPick
          label="Observer"
          value={pickLabel(vs.observer, 'Click on map…')}
          active={pendingPick === 'viewshed.observer'}
          onPick={() => startPick('viewshed.observer')}
        />
        <FieldRange
          label="Radius"
          value={vs.radius}
          min={500}
          max={20000}
          step={500}
          suffix="m"
          onChange={(v) => updateTool('viewshed', { radius: v })}
        />
        <FieldRange
          label="Height"
          value={vs.observerHeight}
          min={0}
          max={50}
          step={0.5}
          suffix="m"
          onChange={(v) => updateTool('viewshed', { observerHeight: v })}
        />
      </ToolCard>

      <ToolCard
        icon={<Spline className="h-3.5 w-3.5" />}
        title="Line of sight"
        busy={los.busy}
        error={los.error}
        onRun={() => onRun('los')}
        onClear={() => onClear('los')}
        runDisabled={!los.observer || !los.destination || los.busy}
        layerOn={layers.los.on}
        layerDisabled={layers.los.disabled}
        onToggleLayer={() => onToggleLayer('los')}
        fallbackMode={isFallbackMode(lastModeByKind.los) ? lastModeByKind.los : undefined}
      >
        <FieldPick
          label="OBS"
          value={pickLabel(los.observer, 'Click on map…')}
          active={pendingPick === 'los.observer'}
          onPick={() => startPick('los.observer')}
        />
        <FieldPick
          label="TGT"
          value={pickLabel(los.destination, 'pick on map…')}
          active={pendingPick === 'los.target'}
          onPick={() => startPick('los.target')}
        />
      </ToolCard>

      <ToolCard
        icon={<RouteIcon className="h-3.5 w-3.5" />}
        title="Routes"
        busy={rt.busy}
        error={rt.error}
        onRun={() => onRun('routes')}
        onClear={() => onClear('routes')}
        runDisabled={!rt.observer || !rt.destination || rt.busy}
        layerOn={layers.routes.on}
        layerDisabled={layers.routes.disabled}
        onToggleLayer={() => onToggleLayer('routes')}
        fallbackMode={isFallbackMode(lastModeByKind.routes) ? lastModeByKind.routes : undefined}
      >
        <FieldPick
          label="START"
          value={pickLabel(rt.observer, 'OBSERVER')}
          active={pendingPick === 'routes.start'}
          onPick={() => startPick('routes.start')}
        />
        <FieldPick
          label="END"
          value={pickLabel(rt.destination, 'pick on map…')}
          active={pendingPick === 'routes.end'}
          onPick={() => startPick('routes.end')}
        />
        <div className="flex items-center justify-between gap-2 py-1">
          <span className="text-[10px] text-sentinel-muted uppercase tracking-wider">Strategy</span>
          <select
            value={rt.strategy}
            onChange={(e) => updateTool('routes', { strategy: e.target.value as ToolState['strategy'] })}
            className="bg-sentinel-bg text-xs text-slate-200 border border-sentinel-line-2 px-1.5 py-0.5"
          >
            <option value="all">COVER · DEM</option>
            <option value="shortest">Shortest</option>
            <option value="least_exposure">Least exposure</option>
            <option value="balanced">Balanced</option>
          </select>
        </div>
      </ToolCard>

      <div className="flex items-center justify-center gap-3 pt-1 font-mono text-[9px] uppercase tracking-wider">
        <Sparkles className="h-3 w-3 text-sentinel-accent" />
        <span style={{ color: capabilities?.dem ? '#5ee0a0' : '#e0a05e' }}>
          DEM · {capabilities?.dem ? 'OK' : 'NONE'}
        </span>
        <span className="text-sentinel-muted">·</span>
        <span style={{ color: capabilities?.routing_graph ? '#5ee0a0' : '#e0a05e' }}>
          ROUTING GRAPH · {capabilities?.routing_graph ? 'OK' : 'NONE'}
        </span>
      </div>
    </div>
  );
}

function FieldPick({
  label,
  value,
  active,
  onPick,
}: {
  label: string;
  value: string;
  active: boolean;
  onPick: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2 py-1">
      <span className="text-[10px] text-sentinel-muted uppercase tracking-wider w-16">{label}</span>
      <button
        type="button"
        onClick={onPick}
        className={`sentinel-btn h-6 flex-1 truncate text-left flex items-center gap-1 ${active ? 'primary' : ''}`}
        title="Click then pick a point on the map"
      >
        <Crosshair className="h-3 w-3 shrink-0" />
        <span className="font-mono text-[10px] truncate">{value}</span>
      </button>
    </div>
  );
}

function FieldRange({
  label,
  value,
  min,
  max,
  step,
  suffix,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix: string;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center gap-2 py-1">
      <span className="text-[10px] text-sentinel-muted uppercase tracking-wider w-16">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="flex-1"
      />
      <span className="font-mono text-[10px] text-slate-200 w-14 text-right">
        {value.toLocaleString()}{suffix}
      </span>
    </div>
  );
}

function ToolCard({
  icon,
  title,
  busy,
  error,
  onRun,
  onClear,
  runDisabled,
  layerOn,
  layerDisabled,
  onToggleLayer,
  fallbackMode,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  busy: boolean;
  error: string | null;
  onRun: () => void;
  onClear: () => void;
  runDisabled: boolean;
  layerOn: boolean;
  layerDisabled: boolean;
  onToggleLayer: () => void;
  fallbackMode?: AnalyticsMode;
  children: React.ReactNode;
}) {
  const layerActive = layerOn && !layerDisabled;
  const fallbackReason = (() => {
    switch (fallbackMode) {
      case 'fixture_no_dem': return 'NO DEM — showing canned shape';
      case 'fixture_no_passes': return 'NO IMAGERY PASS — showing canned shape';
      case 'fixture_no_graph': return 'NO ROUTING GRAPH — showing canned shape';
      case 'offline_fixture': return 'OFFLINE FIXTURE — not real analysis';
      default: return null;
    }
  })();
  return (
    <div className="border border-sentinel-line-2 bg-sentinel-bg p-2">
      <div className="flex items-center gap-2 pb-1">
        <span className="text-sentinel-accent">{icon}</span>
        <span className="flex-1 text-[11px] font-bold uppercase tracking-wider text-slate-200">{title}</span>
        <button
          type="button"
          onClick={onToggleLayer}
          disabled={layerDisabled}
          className={`sentinel-btn h-6 flex items-center gap-1 ${layerActive ? 'primary' : ''} ${layerDisabled ? 'opacity-40 cursor-not-allowed' : ''}`}
          title={layerDisabled ? 'Run the tool first to enable this layer' : layerActive ? 'Hide on map' : 'Show on map'}
        >
          {layerActive ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
          <span className="font-mono text-[10px]">SHOW</span>
        </button>
      </div>
      {children}
      <div className="flex items-center gap-2 pt-2">
        <button
          type="button"
          onClick={onRun}
          disabled={runDisabled}
          className="sentinel-btn primary h-6 flex-1 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {busy ? 'RUNNING…' : 'RUN'}
        </button>
        <button
          type="button"
          onClick={onClear}
          className="sentinel-btn h-6"
        >
          CLEAR
        </button>
      </div>
      {fallbackReason && (
        <div
          role="alert"
          className="mt-2 font-mono text-[10px] uppercase tracking-wider"
          style={{
            border: '1px solid #ff5c5c',
            color: '#ff8b8b',
            background: 'rgba(255, 92, 92, 0.08)',
            padding: '4px 6px',
          }}
        >
          ⚠ {fallbackReason}
        </div>
      )}
      {error && (
        <div className="mt-2 font-mono text-[10px] text-sentinel-crit">{error}</div>
      )}
    </div>
  );
}

/**
 * Terrain analytics tools panel: Viewshed, Line-of-Sight, and Routes. Each
 * tool stages observer/destination picks via map clicks (orchestrated by the
 * parent GaiaMap, which forwards click locations through `pendingPick`), then
 * POSTs to the matching backend endpoint and hands the resulting GeoJSON
 * back via the `onResult` callbacks. The parent owns layer-toggle state.
 */

import { Crosshair, Eye, EyeOff, Route as RouteIcon, Spline, Sparkles } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import {
  getCapabilities,
  runLineOfSight,
  runRoutes,
  runViewshed,
  type AnalyticsCapabilities,
  type AnalyticsResponse,
  type LatLon,
} from '../../services/analytics';

export type AnalyticsKind = 'viewshed' | 'los' | 'routes';

export type AnalyticsPick = 'viewshed.observer' | 'los.observer' | 'los.target' | 'routes.start' | 'routes.end';

type Props = {
  /** Which tool to render — driven by the parent's tab strip. */
  active: AnalyticsKind;
  /** Pending pick the parent map should react to (sets cursor crosshair). */
  pendingPick: AnalyticsPick | null;
  /** Called when this panel begins / cancels a pick request. */
  onRequestPick: (pick: AnalyticsPick | null) => void;
  /** Last point the user clicked on the map; the parent clears it after we read it. */
  lastMapClick: { lat: number; lon: number; pickFor: AnalyticsPick | null } | null;
  /** Notify parent of fresh results so it can render + toggle the layer. */
  onResult: (kind: AnalyticsKind, response: AnalyticsResponse | null) => void;
  /** Whether the active tool's map layer is currently visible. */
  layerOn: boolean;
  /** True when no result exists yet — the toggle is non-interactive. */
  layerDisabled: boolean;
  /** Flip the active tool's map layer visibility. */
  onToggleLayer: () => void;
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

const initial: ToolState = {
  observer: null,
  destination: null,
  radius: 5000,
  observerHeight: 1.8,
  targetHeight: 0,
  strategy: 'all',
  busy: false,
  error: null,
};

function pickLabel(point: LatLon | null, placeholder: string): string {
  if (!point) return placeholder;
  return `${point.latitude.toFixed(4)}, ${point.longitude.toFixed(4)}`;
}

export default function AnalyticsToolsPanel({
  active,
  pendingPick,
  onRequestPick,
  lastMapClick,
  onResult,
  layerOn,
  layerDisabled,
  onToggleLayer,
}: Props) {
  const [state, setState] = useState<ToolState>(initial);
  const [capabilities, setCapabilities] = useState<AnalyticsCapabilities | null>(null);

  useEffect(() => {
    let cancelled = false;
    getCapabilities()
      .then((c) => { if (!cancelled) setCapabilities(c); })
      .catch(() => { if (!cancelled) setCapabilities({ dem: false, routing_graph: false }); });
    return () => { cancelled = true; };
  }, []);

  // Consume the last map click — depending on the pending pick slot.
  useEffect(() => {
    if (!lastMapClick || !lastMapClick.pickFor) return;
    const { lat, lon, pickFor } = lastMapClick;
    setState((prev) => {
      const next = { ...prev };
      const ll = { latitude: lat, longitude: lon };
      if (pickFor === 'viewshed.observer') next.observer = ll;
      if (pickFor === 'los.observer') next.observer = ll;
      if (pickFor === 'los.target') next.destination = ll;
      if (pickFor === 'routes.start') next.observer = ll;
      if (pickFor === 'routes.end') next.destination = ll;
      return next;
    });
    onRequestPick(null);
  }, [lastMapClick, onRequestPick]);

  const startPick = useCallback((slot: AnalyticsPick) => {
    onRequestPick(pendingPick === slot ? null : slot);
  }, [onRequestPick, pendingPick]);

  const onRun = useCallback(async (kind: AnalyticsKind) => {
    setState((p) => ({ ...p, busy: true, error: null }));
    try {
      if (kind === 'viewshed') {
        if (!state.observer) throw new Error('Pick an observer first');
        const res = await runViewshed({
          observer: state.observer,
          radius_m: state.radius,
          observer_height_m: state.observerHeight,
          target_height_m: state.targetHeight,
        });
        onResult('viewshed', res);
      } else if (kind === 'los') {
        if (!state.observer || !state.destination) throw new Error('Pick both observer and target');
        const res = await runLineOfSight({
          observer: state.observer,
          destination: state.destination,
          observer_height_m: state.observerHeight,
          target_height_m: state.targetHeight,
        });
        onResult('los', res);
      } else {
        if (!state.observer || !state.destination) throw new Error('Pick both start and end');
        const res = await runRoutes({
          observer: state.observer,
          destination: state.destination,
          strategy: state.strategy === 'all' ? undefined : state.strategy,
        });
        onResult('routes', res);
      }
    } catch (e: any) {
      setState((p) => ({ ...p, error: e?.message ?? String(e) }));
      onResult(kind, null);
    } finally {
      setState((p) => ({ ...p, busy: false }));
    }
  }, [state, onResult]);

  const onClear = useCallback((kind: AnalyticsKind) => {
    if (kind === 'viewshed') {
      setState((p) => ({ ...p, observer: null }));
    } else if (kind === 'los') {
      setState((p) => ({ ...p, observer: null, destination: null }));
    } else {
      setState((p) => ({ ...p, observer: null, destination: null }));
    }
    onResult(kind, null);
  }, [onResult]);

  return (
    <div className="bg-sentinel-panel-2 px-3 py-2">
      <div className="flex items-center gap-2 pb-2">
        <Sparkles className="h-3.5 w-3.5 text-sentinel-accent" />
        <span className="sentinel-label flex-1">Terrain analytics</span>
        {capabilities && (
          <span
            className="font-mono text-[9px]"
            style={{ color: capabilities.dem ? '#5ee0a0' : '#e0a05e' }}
            title={
              capabilities.dem
                ? 'DEM mounted — real terrain results'
                : 'No DEM mounted at DEM_PATH — falling back to fixtures'
            }
          >
            DEM {capabilities.dem ? 'OK' : 'NONE'}
          </span>
        )}
        {capabilities && (
          <span
            className="font-mono text-[9px]"
            style={{ color: capabilities.routing_graph ? '#5ee0a0' : '#e0a05e' }}
            title={
              capabilities.routing_graph
                ? 'Routing graph available'
                : 'No routing graph at ROUTING_GRAPH_PATH — routes will be fixtures'
            }
          >
            GRAPH {capabilities.routing_graph ? 'OK' : 'NONE'}
          </span>
        )}
      </div>

      {active === 'viewshed' && (
        <ToolCard
          icon={<Eye className="h-3.5 w-3.5" />}
          title="Viewshed"
          busy={state.busy}
          error={state.error}
          onRun={() => onRun('viewshed')}
          onClear={() => onClear('viewshed')}
          runDisabled={!state.observer || state.busy}
          layerOn={layerOn}
          layerDisabled={layerDisabled}
          onToggleLayer={onToggleLayer}
        >
          <FieldPick
            label="Observer"
            value={pickLabel(state.observer, 'Click on map…')}
            active={pendingPick === 'viewshed.observer'}
            onPick={() => startPick('viewshed.observer')}
          />
          <FieldRange
            label="Radius"
            value={state.radius}
            min={500}
            max={20000}
            step={500}
            suffix="m"
            onChange={(v) => setState((p) => ({ ...p, radius: v }))}
          />
          <FieldRange
            label="Observer h"
            value={state.observerHeight}
            min={0}
            max={50}
            step={0.5}
            suffix="m"
            onChange={(v) => setState((p) => ({ ...p, observerHeight: v }))}
          />
        </ToolCard>
      )}

      {active === 'los' && (
        <ToolCard
          icon={<Spline className="h-3.5 w-3.5" />}
          title="Line of sight"
          busy={state.busy}
          error={state.error}
          onRun={() => onRun('los')}
          onClear={() => onClear('los')}
          runDisabled={!state.observer || !state.destination || state.busy}
          layerOn={layerOn}
          layerDisabled={layerDisabled}
          onToggleLayer={onToggleLayer}
        >
          <FieldPick
            label="Observer"
            value={pickLabel(state.observer, 'Click on map…')}
            active={pendingPick === 'los.observer'}
            onPick={() => startPick('los.observer')}
          />
          <FieldPick
            label="Target"
            value={pickLabel(state.destination, 'Click on map…')}
            active={pendingPick === 'los.target'}
            onPick={() => startPick('los.target')}
          />
          <FieldRange
            label="Target h"
            value={state.targetHeight}
            min={0}
            max={50}
            step={0.5}
            suffix="m"
            onChange={(v) => setState((p) => ({ ...p, targetHeight: v }))}
          />
        </ToolCard>
      )}

      {active === 'routes' && (
        <ToolCard
          icon={<RouteIcon className="h-3.5 w-3.5" />}
          title="Routes"
          busy={state.busy}
          error={state.error}
          onRun={() => onRun('routes')}
          onClear={() => onClear('routes')}
          runDisabled={!state.observer || !state.destination || state.busy}
          layerOn={layerOn}
          layerDisabled={layerDisabled}
          onToggleLayer={onToggleLayer}
        >
          <FieldPick
            label="Start"
            value={pickLabel(state.observer, 'Click on map…')}
            active={pendingPick === 'routes.start'}
            onPick={() => startPick('routes.start')}
          />
          <FieldPick
            label="End"
            value={pickLabel(state.destination, 'Click on map…')}
            active={pendingPick === 'routes.end'}
            onPick={() => startPick('routes.end')}
          />
          <div className="flex items-center justify-between gap-2 py-1">
            <span className="text-[10px] text-sentinel-muted uppercase tracking-wider">Strategy</span>
            <select
              value={state.strategy}
              onChange={(e) => setState((p) => ({ ...p, strategy: e.target.value as ToolState['strategy'] }))}
              className="bg-sentinel-bg text-xs text-slate-200 border border-sentinel-line-2 px-1.5 py-0.5"
            >
              <option value="all">All options</option>
              <option value="shortest">Shortest</option>
              <option value="least_exposure">Least exposure</option>
              <option value="balanced">Balanced</option>
            </select>
          </div>
        </ToolCard>
      )}
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
  children: React.ReactNode;
}) {
  const layerActive = layerOn && !layerDisabled;
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
      {error && (
        <div className="mt-2 font-mono text-[10px] text-sentinel-crit">{error}</div>
      )}
    </div>
  );
}

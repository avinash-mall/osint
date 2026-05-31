/**
 * Satellites overpass panel: import TLEs (air-gap), predict overpasses over a
 * picked observer point, and request a ground track for the map.
 *
 * Self-contained: it fetches its own TLE list and posts predictions; the only
 * coupling to the map is `onGroundTrack`, which hands a [lon,lat][] polyline up
 * to the parent to render as a Leaflet layer. Mirrors AnalyticsToolsPanel's
 * styling so it slots into the same tool rail. Fully offline — see
 * docs/backend-routers/satellites-router.md.
 */

import { Satellite, Crosshair, Upload } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import {
  getGroundTrack,
  importTle,
  listTles,
  predictPasses,
  type OverpassResponse,
  type StoredTle,
} from '../../services/satellites';

type Props = {
  /** Observer point picked on the map (lat/lon), or null. */
  observer: { lat: number; lon: number } | null;
  /** Begin a map pick for the observer point. */
  onRequestPick: () => void;
  pickActive: boolean;
  /** Hand a ground track ([lon,lat] pairs) up for the parent to draw, or null to clear. */
  onGroundTrack: (coords: [number, number][] | null, label: string) => void;
};

export default function SatellitesPanel({ observer, onRequestPick, pickActive, onGroundTrack }: Props) {
  const [tles, setTles] = useState<StoredTle[]>([]);
  const [result, setResult] = useState<OverpassResponse | null>(null);
  const [hours, setHours] = useState(24);
  const [minElev, setMinElev] = useState(10);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importText, setImportText] = useState('');
  const [showImport, setShowImport] = useState(false);

  const refreshTles = useCallback(() => {
    listTles().then(setTles).catch(() => setTles([]));
  }, []);

  useEffect(() => { refreshTles(); }, [refreshTles]);

  const onPredict = useCallback(async () => {
    if (!observer) { setError('Pick an observer point first'); return; }
    setBusy(true);
    setError(null);
    try {
      const res = await predictPasses({
        lat: observer.lat,
        lon: observer.lon,
        hours,
        min_elevation_deg: minElev,
      });
      setResult(res);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? String(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }, [observer, hours, minElev]);

  const onImport = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const { imported } = await importTle(importText, 'manual-import');
      setImportText('');
      setShowImport(false);
      refreshTles();
      setError(imported ? null : 'No valid TLE sets found');
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }, [importText, refreshTles]);

  const onTrack = useCallback(async (noradId: number, name: string) => {
    try {
      const t = await getGroundTrack(noradId, hours <= 6 ? hours : 1.5);
      onGroundTrack(t.coordinates, name);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? String(e));
    }
  }, [hours, onGroundTrack]);

  return (
    <div data-tour="satellites-panel" className="bg-sentinel-panel-2 px-3 py-2 space-y-2">
      <div className="border border-sentinel-line-2 bg-sentinel-bg p-2">
        <div className="flex items-center gap-2 pb-1">
          <span className="text-sentinel-accent"><Satellite className="h-3.5 w-3.5" /></span>
          <span className="flex-1 text-[11px] font-bold uppercase tracking-wider text-slate-200">Overpasses</span>
          <button
            type="button"
            onClick={() => setShowImport((v) => !v)}
            className={`sentinel-btn h-6 flex items-center gap-1 ${showImport ? 'primary' : ''}`}
            title="Import TLEs (air-gap)"
          >
            <Upload className="h-3.5 w-3.5" />
            <span className="font-mono text-[10px]">TLE</span>
          </button>
        </div>

        {showImport && (
          <div className="space-y-1 pb-2">
            <textarea
              value={importText}
              onChange={(e) => setImportText(e.target.value)}
              placeholder={'Paste 2-/3-line TLE text…'}
              rows={4}
              className="w-full bg-sentinel-bg text-[10px] font-mono text-slate-200 border border-sentinel-line-2 px-1.5 py-1"
            />
            <button
              type="button"
              onClick={onImport}
              disabled={busy || !importText.trim()}
              className="sentinel-btn primary h-6 w-full disabled:opacity-40 disabled:cursor-not-allowed"
            >
              IMPORT
            </button>
          </div>
        )}

        <div className="flex items-center justify-between gap-2 py-1">
          <span className="text-[10px] text-sentinel-muted uppercase tracking-wider w-16">Observer</span>
          <button
            type="button"
            onClick={onRequestPick}
            className={`sentinel-btn h-6 flex-1 truncate text-left flex items-center gap-1 ${pickActive ? 'primary' : ''}`}
            title="Click then pick a point on the map"
          >
            <Crosshair className="h-3 w-3 shrink-0" />
            <span className="font-mono text-[10px] truncate">
              {observer ? `${observer.lat.toFixed(4)}, ${observer.lon.toFixed(4)}` : 'Click on map…'}
            </span>
          </button>
        </div>

        <div className="flex items-center gap-2 py-1">
          <span className="text-[10px] text-sentinel-muted uppercase tracking-wider w-16">Window</span>
          <input type="range" min={1} max={72} step={1} value={hours}
            onChange={(e) => setHours(Number(e.target.value))} className="flex-1" />
          <span className="font-mono text-[10px] text-slate-200 w-14 text-right">{hours}h</span>
        </div>
        <div className="flex items-center gap-2 py-1">
          <span className="text-[10px] text-sentinel-muted uppercase tracking-wider w-16">Min elev</span>
          <input type="range" min={0} max={45} step={1} value={minElev}
            onChange={(e) => setMinElev(Number(e.target.value))} className="flex-1" />
          <span className="font-mono text-[10px] text-slate-200 w-14 text-right">{minElev}°</span>
        </div>

        <div className="flex items-center gap-2 pt-2">
          <button
            type="button"
            onClick={onPredict}
            disabled={busy || !observer}
            className="sentinel-btn primary h-6 flex-1 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {busy ? 'PREDICTING…' : 'PREDICT'}
          </button>
          <span className="font-mono text-[9px] text-sentinel-muted uppercase">{tles.length} TLE</span>
        </div>

        {error && <div className="mt-2 font-mono text-[10px] text-sentinel-crit">{error}</div>}
      </div>

      {result && result.satellites.length === 0 && (
        <div className="font-mono text-[10px] text-sentinel-muted uppercase tracking-wider px-1">
          No passes in window
        </div>
      )}

      {result && result.satellites.map((sat) => (
        <div key={sat.norad_id} className="border border-sentinel-line-2 bg-sentinel-bg p-2">
          <div className="flex items-center gap-2 pb-1">
            <span className="flex-1 text-[10px] font-bold uppercase tracking-wider text-slate-200 truncate">
              {sat.name || `NORAD ${sat.norad_id}`}
            </span>
            <button
              type="button"
              onClick={() => onTrack(sat.norad_id, sat.name || String(sat.norad_id))}
              className="sentinel-btn h-5 font-mono text-[9px]"
              title="Draw ground track on the map"
            >
              TRACK
            </button>
          </div>
          <div className="space-y-0.5">
            {sat.passes.map((p, i) => (
              <div key={i} className="flex items-center justify-between gap-2 font-mono text-[10px] text-slate-300">
                <span>{new Date(p.aos).toLocaleString()}</span>
                <span className="text-sentinel-accent">{p.max_elevation_deg.toFixed(0)}°</span>
                <span className="text-sentinel-muted">{Math.round(p.duration_s / 60)}m</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

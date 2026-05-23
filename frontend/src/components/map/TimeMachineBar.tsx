/**
 * Map+ time-machine scrubber.
 *
 * Diamond markers on a horizontal rail represent imagery acquisitions in the
 * current window. The playhead snaps to a normalized [0,1] position and the
 * caller maps that back to ISO timestamps for the imagery filter.
 */

import { Pause, Play, RotateCcw, SplitSquareHorizontal, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { ModalityBadge, Panel } from '../atoms';

export type ImageryPass = {
  id: number;
  acquisition_time?: string | null;
  sensor_type?: string | null;
  name?: string | null;
};

type Range = '24h' | '7d' | '30d';

const RANGE_HOURS: Record<Range, number> = { '24h': 24, '7d': 24 * 7, '30d': 24 * 30 };

function sensorToModality(sensor: string | null | undefined): 'rgb' | 'multispectral' | 'sar' | 'hsi' | 'fmv' {
  const s = (sensor || '').toLowerCase();
  if (s.includes('sar')) return 'sar';
  if (s.includes('multi')) return 'multispectral';
  if (s.includes('hyper')) return 'hsi';
  if (s.includes('fmv') || s.includes('video') || s.includes('eo/ir')) return 'fmv';
  return 'rgb';
}

export default function TimeMachineBar({
  passes,
  range,
  value,
  playing,
  onRangeChange,
  onValueChange,
  onTogglePlay,
  onRecenter,
  isoNow,
  confidence,
  onConfidenceChange,
  activePassId = null,
  comparePassId = null,
  onPassPin,
  onClearCompare,
}: {
  passes: ImageryPass[];
  range: Range;
  value: number; // [0..1] across the range window ending "now"
  playing: boolean;
  onRangeChange: (r: Range) => void;
  onValueChange: (v: number) => void;
  onTogglePlay: () => void;
  onRecenter: () => void;
  isoNow: string;
  confidence: number; // 0..1 — hide detections below this floor
  onConfidenceChange: (v: number) => void;
  /** ID of the imagery pass currently rendered as the primary layer. */
  activePassId?: number | null;
  /** ID of the imagery pass pinned for side-by-side comparison. */
  comparePassId?: number | null;
  /** Alt-click on a pass diamond, or click the chip's pin button. Toggles a pass into the compare slot. */
  onPassPin?: (id: number) => void;
  /** Clear the compare slot. */
  onClearCompare?: () => void;
}) {
  const comparePass = comparePassId != null ? passes.find((p) => p.id === comparePassId) : null;
  const ms = RANGE_HOURS[range] * 3600_000;
  const end = Date.parse(isoNow);
  const start = end - ms;

  const dots = useMemo(() => {
    return passes
      .map((p) => {
        if (!p.acquisition_time) return null;
        const t = Date.parse(p.acquisition_time);
        if (Number.isNaN(t)) return null;
        if (t < start || t > end) return null;
        return { ...p, t, frac: (t - start) / Math.max(1, end - start) };
      })
      .filter(Boolean) as Array<ImageryPass & { t: number; frac: number }>;
  }, [passes, start, end]);

  const playheadIso = new Date(start + value * (end - start)).toISOString();

  // UX-AUDIT F15 — surface the exact ISO timestamp under the playhead while
  // the operator hovers or keyboard-focuses the scrubber.
  const [showTip, setShowTip] = useState(false);

  return (
    <Panel style={{ padding: 8 }}>
      <div className="time-machine-header" style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <button
          type="button"
          className="btn icon sm"
          style={{ borderRadius: 999 }}
          onClick={onTogglePlay}
          title={playing ? 'Pause' : 'Play time-machine'}
        >
          {playing ? <Pause size={12} /> : <Play size={12} />}
        </button>
        <button
          type="button"
          className="btn icon sm"
          style={{ borderRadius: 999 }}
          onClick={onRecenter}
          title="Recenter playhead at now"
        >
          <RotateCcw size={12} />
        </button>
        <span style={{ fontSize: 11.5, fontWeight: 500 }}>Time-machine</span>
        <span className="time-machine-stamp mono" style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>
          {new Date(playheadIso).toUTCString().replace('GMT', 'Z')} · {range} window
        </span>
        <div className="time-machine-spacer" style={{ flex: 1 }} />
        <div className="seg" style={{ borderRadius: 999, overflow: 'hidden' }}>
          {(['24h', '7d', '30d'] as Range[]).map((w) => (
            <button
              key={w}
              type="button"
              className={range === w ? 'on' : ''}
              onClick={() => onRangeChange(w)}
            >
              {w}
            </button>
          ))}
        </div>
        <span
          className="time-machine-confidence mono"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 10.5,
            color: 'var(--ink-2)',
            flexShrink: 0,
          }}
          title="Hide detections below this confidence"
        >
          <span style={{ color: 'var(--ink-3)' }}>CONF</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={confidence}
            onChange={(e) => onConfidenceChange(Number(e.target.value))}
            aria-label="Detection confidence threshold"
            style={{ width: 140, accentColor: 'var(--accent)' }}
          />
          <span style={{ color: 'var(--accent)', minWidth: 28, textAlign: 'right' }}>
            {Math.round(confidence * 100)}%
          </span>
        </span>
        <span className="mono" style={{ fontSize: 10.5, color: 'var(--accent)' }}>
          {dots.length} passes
        </span>
        {(() => {
          if (comparePass) {
            return (
              <span
                title="Side-by-side compare active — drag the divider on the map"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '2px 6px',
                  border: '1px solid var(--accent)',
                  color: 'var(--accent)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 10,
                  textTransform: 'uppercase',
                  letterSpacing: '.06em',
                }}
              >
                <SplitSquareHorizontal size={10} />
                vs Pass {comparePass.id}
                <button
                  type="button"
                  onClick={() => onClearCompare?.()}
                  title="Exit compare"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    padding: 0,
                    background: 'transparent',
                    color: 'inherit',
                    border: 'none',
                    cursor: 'pointer',
                  }}
                >
                  <X size={10} />
                </button>
              </span>
            );
          }
          if (activePassId == null || !onPassPin) return null;
          // Offer a one-click "compare previous" button: pick the closest
          // pass earlier in time than the active one and pin it.
          const ordered = passes
            .filter((p) => p.acquisition_time)
            .sort((a, b) => Date.parse(b.acquisition_time as string) - Date.parse(a.acquisition_time as string));
          const idx = ordered.findIndex((p) => p.id === activePassId);
          const prev = idx >= 0 ? ordered[idx + 1] : null;
          if (!prev) return null;
          return (
            <button
              type="button"
              onClick={() => onPassPin(prev.id)}
              title={`Compare against Pass ${prev.id}`}
              className="btn xs"
              style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
            >
              <SplitSquareHorizontal size={10} />
              Compare
            </button>
          );
        })()}
      </div>

      <div
        style={{ position: 'relative', height: 22 }}
        onMouseEnter={() => setShowTip(true)}
        onMouseLeave={() => setShowTip(false)}
      >
        {showTip && (
          <div className="timeline-tip" style={{ left: `${value * 100}%` }}>
            {playheadIso}
          </div>
        )}
        {/* Track */}
        <div
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            top: 9,
            height: 4,
            background: 'var(--bg-3)',
            borderRadius: 2,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: 0,
            top: 9,
            width: `${value * 100}%`,
            height: 4,
            background: 'color-mix(in oklab, var(--accent) 40%, transparent)',
            borderRadius: 2,
          }}
        />
        {/* Diamonds for each pass */}
        {dots.map((p) => {
          const mod = sensorToModality(p.sensor_type);
          const c =
            mod === 'rgb' ? '#9bd1ff'
            : mod === 'multispectral' ? '#a78bfa'
            : mod === 'sar' ? '#fca56a'
            : mod === 'hsi' ? '#ff79c6'
            : '#5ee0a0';
          const active = Math.abs(p.frac - value) < 0.04;
          return (
            <div
              key={p.id}
              title={`${p.name || `pass ${p.id}`} · ${p.sensor_type || 'sensor'} · ${p.acquisition_time}`}
              style={{
                position: 'absolute',
                left: `calc(${p.frac * 100}% - 6px)`,
                top: 5,
                width: 12,
                height: 12,
                background: c,
                transform: 'rotate(45deg)',
                border: `1.5px solid ${active ? '#fff' : 'rgba(255,255,255,.2)'}`,
                boxShadow: active ? `0 0 10px ${c}` : 'none',
                cursor: 'pointer',
              }}
              onClick={(e) => {
                if ((e.altKey || e.shiftKey) && onPassPin) {
                  onPassPin(p.id);
                  return;
                }
                onValueChange(p.frac);
              }}
            />
          );
        })}
        {/* Playhead */}
        <div
          style={{
            position: 'absolute',
            left: `calc(${value * 100}% - 1px)`,
            top: 0,
            bottom: 0,
            width: 2,
            background: 'var(--accent)',
            boxShadow: '0 0 6px var(--accent)',
          }}
        />
        {/* Clickable rail (transparent range input on top) */}
        <input
          type="range"
          min="0"
          max="1"
          step="0.005"
          value={value}
          onChange={(e) => onValueChange(Number(e.target.value))}
          onFocus={() => setShowTip(true)}
          onBlur={() => setShowTip(false)}
          aria-label="Time-machine scrubber"
          aria-valuetext={playheadIso}
          style={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            opacity: 0,
            cursor: 'pointer',
          }}
        />
      </div>

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          marginTop: 2,
          fontSize: 9.5,
          color: 'var(--ink-3)',
          fontFamily: 'var(--font-mono)',
        }}
      >
        <span>{range} ago</span>
        <span>50%</span>
        <span>now</span>
      </div>

      {/* Legend strip */}
      <div className="time-machine-legend" style={{ display: 'flex', gap: 14, marginTop: 4, fontSize: 10.5 }}>
        <LegendDot color="#9bd1ff" label="RGB" m="rgb" />
        <LegendDot color="#a78bfa" label="MSI" m="multispectral" />
        <LegendDot color="#fca56a" label="SAR" m="sar" />
        <LegendDot color="#ff79c6" label="HSI" m="hsi" />
      </div>
    </Panel>
  );
}

function LegendDot({
  color,
  m,
}: {
  color: string;
  label: string;
  m: 'rgb' | 'multispectral' | 'sar' | 'hsi';
}) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span
        style={{
          width: 8,
          height: 8,
          background: color,
          transform: 'rotate(45deg)',
          display: 'inline-block',
        }}
      />
      <ModalityBadge m={m} size="xs" />
    </span>
  );
}

import { useMemo } from 'react';

/**
 * Compact time-window picker for the Link Graph Investigation mode.
 *
 * Models the same idea as the GaiaMap timeline strip — a sliding window with
 * a small histogram and presets — but stripped to what the graph needs:
 * pick a start/end window, scale by preset, see roughly how many timestamps
 * sit in the window.
 *
 * GaiaMap is intentionally NOT modified here; if the map's scrubber UX
 * eventually converges with this one we'll refactor both onto this component.
 * See [docs/architecture/link-graph-redesign.md#phase-1-investigation-mode-shell]
 * for the design intent.
 */
export interface TimeRange {
  start: string;  // ISO 8601
  end: string;    // ISO 8601
}

interface TimeScrubberProps {
  /** Current selection. */
  value: TimeRange;
  onChange: (next: TimeRange) => void;
  /** Optional timestamps (ms epoch) used to build a small density histogram. */
  histogramTimestamps?: number[];
  /** Preset windows in hours, rendered as a row of buttons. */
  presets?: { label: string; hours: number }[];
}

const DEFAULT_PRESETS = [
  { label: '1H', hours: 1 },
  { label: '24H', hours: 24 },
  { label: '7D', hours: 24 * 7 },
  { label: '30D', hours: 24 * 30 },
];
const BUCKET_COUNT = 24;

export function TimeScrubber({
  value,
  onChange,
  histogramTimestamps = [],
  presets = DEFAULT_PRESETS,
}: TimeScrubberProps) {
  const startMs = useMemo(() => new Date(value.start).getTime(), [value.start]);
  const endMs = useMemo(() => new Date(value.end).getTime(), [value.end]);
  const buckets = useMemo(() => {
    if (!histogramTimestamps.length || endMs <= startMs) {
      return new Array(BUCKET_COUNT).fill(0);
    }
    const out = new Array(BUCKET_COUNT).fill(0);
    const span = endMs - startMs;
    histogramTimestamps.forEach((ts) => {
      if (ts < startMs || ts > endMs) return;
      const idx = Math.min(BUCKET_COUNT - 1, Math.floor(((ts - startMs) / span) * BUCKET_COUNT));
      out[idx] += 1;
    });
    return out;
  }, [histogramTimestamps, startMs, endMs]);

  const maxBucket = useMemo(() => Math.max(1, ...buckets), [buckets]);
  const inWindow = histogramTimestamps.reduce(
    (count, ts) => count + (ts >= startMs && ts <= endMs ? 1 : 0),
    0,
  );

  const applyPreset = (hours: number) => {
    const end = new Date();
    const start = new Date(end.getTime() - hours * 60 * 60 * 1000);
    onChange({ start: start.toISOString(), end: end.toISOString() });
  };

  const formatLabel = (iso: string) => {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  };

  return (
    <div className="time-scrubber border border-sentinel-line bg-sentinel-panel p-2">
      <div className="flex items-center gap-2 mb-2">
        <span className="sentinel-label text-[10px]">Window</span>
        <div className="flex border border-sentinel-line-2 h-6 ml-auto">
          {presets.map((p) => (
            <button
              key={p.label}
              type="button"
              onClick={() => applyPreset(p.hours)}
              className="px-2 text-[10px] font-mono uppercase border-l first:border-l-0 border-sentinel-line-2 text-sentinel-muted hover:text-sentinel-text"
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex items-end gap-px h-7 border border-sentinel-line-2 bg-sentinel-bg p-1">
        {buckets.map((value, idx) => (
          <span
            key={idx}
            className="flex-1 bg-sentinel-accent"
            style={{
              height: `${Math.max(6, (value / maxBucket) * 100)}%`,
              opacity: 0.35 + (value / maxBucket) * 0.55,
            }}
          />
        ))}
      </div>
      <div className="mt-1 flex items-center gap-2 text-[10px] text-sentinel-muted font-mono">
        <span>{formatLabel(value.start)}</span>
        <span className="opacity-50">→</span>
        <span>{formatLabel(value.end)}</span>
        <span className="ml-auto">
          {inWindow}{histogramTimestamps.length ? `/${histogramTimestamps.length}` : ''} in window
        </span>
      </div>
    </div>
  );
}

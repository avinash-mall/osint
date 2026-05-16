/**
 * Shared design atoms for the GEOINT Workstation: NATO APP-6 affiliation glyphs and small
 * inline SVG primitives.  Larger functional icons are sourced from lucide-react in-place.
 */

import type { CSSProperties, ReactNode } from 'react';

export type Affiliation = 'friend' | 'hostile' | 'neutral' | 'unknown';

export function natoColor(aff: Affiliation | string | undefined): string {
  switch (aff) {
    case 'hostile': return 'var(--nato-hostile)';
    case 'friend':  return 'var(--nato-friend)';
    case 'neutral': return 'var(--nato-neutral)';
    default:        return 'var(--nato-unknown)';
  }
}

export function natoTagClass(aff: Affiliation | string | undefined): string {
  switch (aff) {
    case 'hostile': return 'hostile';
    case 'friend':  return 'friend';
    case 'neutral': return 'neutral';
    default:        return 'unknown';
  }
}

export function threatColor(threat: string | undefined): string {
  switch (threat) {
    case 'critical': return 'var(--nato-hostile)';
    case 'high':     return 'var(--accent)';
    case 'medium':   return 'var(--nato-unknown)';
    default:         return 'var(--ink-2)';
  }
}

type AffGlyphProps = {
  aff: Affiliation | string;
  size?: number;
  filled?: boolean;
  style?: CSSProperties;
};

/** NATO APP-6 affiliation glyph: circle (friend) / diamond (hostile) / square (neutral) / clover (unknown). */
export function AffGlyph({ aff, size = 18, filled = true, style }: AffGlyphProps) {
  const color = natoColor(aff);
  const sw = 2;
  const fill = filled ? `color-mix(in oklab, ${color} 22%, transparent)` : 'none';
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      style={{ display: 'block', ...style }}
      aria-hidden
    >
      {aff === 'friend' && (
        <circle cx="12" cy="12" r="8" fill={fill} stroke={color} strokeWidth={sw} />
      )}
      {aff === 'hostile' && (
        <path d="M12 4 20 12 12 20 4 12Z" fill={fill} stroke={color} strokeWidth={sw} />
      )}
      {aff === 'neutral' && (
        <rect x="5" y="5" width="14" height="14" fill={fill} stroke={color} strokeWidth={sw} />
      )}
      {(aff === 'unknown' || (aff !== 'friend' && aff !== 'hostile' && aff !== 'neutral')) && (
        <path
          d="M12 3a4 4 0 0 1 4 4 4 4 0 0 1 4 4 4 4 0 0 1-4 4 4 4 0 0 1-4 4 4 4 0 0 1-4-4 4 4 0 0 1-4-4 4 4 0 0 1 4-4 4 4 0 0 1 4-4Z"
          fill={fill}
          stroke={color}
          strokeWidth={sw}
        />
      )}
    </svg>
  );
}

type SparkProps = { values: number[]; color?: string; w?: number; h?: number };
export function Spark({ values, color = 'currentColor', w = 80, h = 18 }: SparkProps) {
  if (!values.length) return null;
  const max = Math.max(...values, 1);
  const step = w / (values.length - 1 || 1);
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(h - (v / max) * h).toFixed(1)}`).join(' ');
  return (
    <svg width={w} height={h} style={{ display: 'block' }} aria-hidden>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.25} />
    </svg>
  );
}

type BarsProps = { values: number[]; w?: number; h?: number; color?: string };
export function Bars({ values, w = 220, h = 36, color = 'var(--accent)' }: BarsProps) {
  const max = Math.max(...values, 1);
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-end',
        gap: 1,
        width: w,
        height: h,
        background: 'var(--bg-2)',
        padding: 2,
      }}
    >
      {values.map((v, i) => (
        <div
          key={i}
          style={{
            flex: 1,
            background: v > 0 ? color : 'var(--line-2)',
            opacity: 0.35 + 0.65 * (v / max),
            height: `${Math.max(6, (v / max) * 100)}%`,
          }}
        />
      ))}
    </div>
  );
}

type StatusDotProps = { tone: 'ok' | 'warn' | 'crit' | 'info' | 'muted'; size?: number; pulse?: boolean };
export function StatusDot({ tone, size = 8, pulse = false }: StatusDotProps) {
  const color =
    tone === 'ok'   ? 'var(--ok)' :
    tone === 'warn' ? 'var(--warn)' :
    tone === 'crit' ? 'var(--crit)' :
    tone === 'info' ? 'var(--info)' : 'var(--ink-3)';
  return (
    <span
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: 999,
        background: color,
        animation: pulse ? 'pulse 1.6s infinite' : undefined,
        flexShrink: 0,
      }}
    />
  );
}

type LabelMonoProps = { children: ReactNode; style?: CSSProperties };
export function LabelMono({ children, style }: LabelMonoProps) {
  return (
    <div className="label-mono" style={style}>
      {children}
    </div>
  );
}


/** Modality badge — color-coded for the five sensor families.
 *  Ported from /tmp/sentinel-design-v2/test1/project/components/features.jsx:7 */
export type Modality = 'rgb' | 'multispectral' | 'sar' | 'hsi' | 'fmv';
const MODALITY_META: Record<Modality, { label: string; color: string }> = {
  rgb:           { label: 'RGB',  color: '#9bd1ff' },
  multispectral: { label: 'MSI',  color: '#a78bfa' },
  sar:           { label: 'SAR',  color: '#fca56a' },
  hsi:           { label: 'HSI',  color: '#ff79c6' },
  fmv:           { label: 'FMV',  color: '#5ee0a0' },
};
export function ModalityBadge({
  m = 'rgb',
  size = 'sm',
}: {
  m?: Modality | string;
  size?: 'xs' | 'sm';
}) {
  const meta = MODALITY_META[(m as Modality)] || MODALITY_META.rgb;
  const fz = size === 'xs' ? 9 : 10;
  return (
    <span
      className="mono"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: size === 'xs' ? '1px 5px' : '2px 7px',
        fontSize: fz,
        letterSpacing: '.08em',
        color: meta.color,
        border: `1px solid ${meta.color}`,
        background: `color-mix(in oklab, ${meta.color} 12%, transparent)`,
        borderRadius: 2,
        textTransform: 'uppercase',
      }}
      title={`Sensor modality: ${meta.label}`}
    >
      {meta.label}
    </span>
  );
}

/** Embedding badge — surfaces which DINOv3 head produced the embedding.
 *  Ported from features.jsx:26 */
export type EmbeddingKind = 'sat' | 'lvd' | 'terramind' | 'none';
const EMBED_META: Record<EmbeddingKind, { label: string; color: string }> = {
  sat:       { label: 'DINOv3-SAT', color: '#9bd1ff' },
  lvd:       { label: 'DINOv3-LVD', color: '#5ee0a0' },
  terramind: { label: 'TERRAMIND',  color: '#a78bfa' },
  none:      { label: '—',          color: 'var(--ink-3)' },
};
export function EmbeddingBadge({ kind = 'sat' }: { kind?: EmbeddingKind | string }) {
  const meta = EMBED_META[(kind as EmbeddingKind)] || EMBED_META.none;
  return (
    <span
      className="mono"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '2px 7px',
        fontSize: 9.5,
        letterSpacing: '.08em',
        color: meta.color,
        background: `color-mix(in oklab, ${meta.color} 14%, transparent)`,
        border: `1px solid color-mix(in oklab, ${meta.color} 50%, transparent)`,
        borderRadius: 2,
      }}
      title={`Embedding head: ${meta.label}`}
    >
      {meta.label}
    </span>
  );
}

/** Card-style panel with optional title/sub header. Used by Admin views and
 *  Map+/FMV+ overlay panels. Pure presentational. */
export function Panel({
  title,
  sub,
  right,
  children,
  style,
}: {
  title?: ReactNode;
  sub?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div
      className="card responsive-panel"
      style={{
        background: 'var(--bg-1)',
        border: '1px solid var(--line)',
        borderRadius: 10,
        ...style,
      }}
    >
      {(title || sub || right) && (
        <div className="panel-title-row" style={{ alignItems: 'baseline', marginBottom: 12 }}>
          {title && <span style={{ fontSize: 13, fontWeight: 600 }}>{title}</span>}
          {sub && <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>{sub}</span>}
          <span style={{ flex: 1 }} />
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

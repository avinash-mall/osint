/**
 * Shared design atoms.
 *
 * Re-export taxonomy primitives from utils/objectMetadata so component code
 * has one import for "threat color" / "affiliation glyph" / "cursor readout".
 *
 * Adds `CursorReadout` (moved out of App.tsx) and `ContainerCard` — the canonical
 * panel wrapper that establishes a CSS container so children can use
 * @container queries to adapt without media queries.
 */

import type { CSSProperties, ReactNode } from 'react';
import {
  natoColor as taxonomyNatoColor,
  threatColor as taxonomyThreatColor,
  threatLevel as taxonomyThreatLevel,
  type AffiliationId,
} from '../utils/objectMetadata';

/* ─── Re-exports so existing callers keep working ─────────────────────── */
export { THREAT_LEVELS, AFFILIATIONS, threatLevel, affiliation, threatColor, natoColor } from '../utils/objectMetadata';
export type { ObjectDetails, ThreatLevelId, AffiliationId } from '../utils/objectMetadata';

export type Affiliation = AffiliationId;

export function natoTagClass(aff: string | undefined): string {
  switch (aff) {
    case 'hostile': return 'hostile';
    case 'friend':  return 'friend';
    case 'neutral': return 'neutral';
    default:        return 'unknown';
  }
}

/* ─── NATO APP-6 affiliation glyph ────────────────────────────────────── */

type AffGlyphProps = {
  aff: string;
  size?: number;
  filled?: boolean;
  style?: CSSProperties;
};

export function AffGlyph({ aff, size = 18, filled = true, style }: AffGlyphProps) {
  const color = taxonomyNatoColor(aff);
  const sw = 2;
  const fill = filled ? `color-mix(in oklab, ${color} 22%, transparent)` : 'none';
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" style={{ display: 'block', ...style }} aria-hidden>
      {aff === 'friend' && <circle cx="12" cy="12" r="8" fill={fill} stroke={color} strokeWidth={sw} />}
      {aff === 'hostile' && <path d="M12 4 20 12 12 20 4 12Z" fill={fill} stroke={color} strokeWidth={sw} />}
      {aff === 'neutral' && <rect x="5" y="5" width="14" height="14" fill={fill} stroke={color} strokeWidth={sw} />}
      {(aff === 'unknown' || (aff !== 'friend' && aff !== 'hostile' && aff !== 'neutral')) && (
        <path
          d="M12 3a4 4 0 0 1 4 4 4 4 0 0 1 4 4 4 4 0 0 1-4 4 4 4 0 0 1-4 4 4 4 0 0 1-4-4 4 4 0 0 1-4-4 4 4 0 0 1 4-4 4 4 0 0 1 4-4Z"
          fill={fill} stroke={color} strokeWidth={sw}
        />
      )}
    </svg>
  );
}

/* ─── Sparkline ───────────────────────────────────────────────────────── */

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

/* ─── Bars ────────────────────────────────────────────────────────────── */

type BarsProps = { values: number[]; w?: number; h?: number; color?: string };
export function Bars({ values, w = 220, h = 36, color = 'var(--accent)' }: BarsProps) {
  const max = Math.max(...values, 1);
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 1, width: w, height: h, background: 'var(--bg-2)', padding: 2 }}>
      {values.map((v, i) => (
        <div key={i} style={{
          flex: 1,
          background: v > 0 ? color : 'var(--line-2)',
          opacity: 0.35 + 0.65 * (v / max),
          height: `${Math.max(6, (v / max) * 100)}%`,
        }}/>
      ))}
    </div>
  );
}

/* ─── Status dot ──────────────────────────────────────────────────────── */

type StatusDotProps = { tone: 'ok' | 'warn' | 'crit' | 'info' | 'muted'; size?: number; pulse?: boolean };
export function StatusDot({ tone, size = 8, pulse = false }: StatusDotProps) {
  const color =
    tone === 'ok'   ? 'var(--ok)' :
    tone === 'warn' ? 'var(--warn)' :
    tone === 'crit' ? 'var(--crit)' :
    tone === 'info' ? 'var(--info)' : 'var(--ink-3)';
  return (
    <span style={{
      display: 'inline-block',
      width: size, height: size, borderRadius: 999,
      background: color,
      animation: pulse ? 'pulse 1.6s infinite' : undefined,
      flexShrink: 0,
    }}/>
  );
}

/* ─── LabelMono ───────────────────────────────────────────────────────── */

type LabelMonoProps = { children: ReactNode; style?: CSSProperties };
export function LabelMono({ children, style }: LabelMonoProps) {
  return <div className="label-mono" style={style}>{children}</div>;
}

/* ─── ThreatBadge (single source — was duplicated in ObjectDetailsForm) ── */

export function ThreatBadge({ level }: { level?: string }) {
  const color = taxonomyThreatColor(level);
  const lvl = taxonomyThreatLevel(level);
  return (
    <span
      className="mono threat-badge"
      style={{
        fontSize: 10,
        letterSpacing: '.08em',
        padding: '2px 8px',
        background: `color-mix(in oklab, ${color} 22%, var(--bg-2))`,
        color,
        border: `1px solid ${color}`,
      }}
    >
      {lvl.label}
    </span>
  );
}

/* ─── ModalityBadge ───────────────────────────────────────────────────── */

export type Modality = 'rgb' | 'multispectral' | 'sar' | 'hsi' | 'fmv';
const MODALITY_META: Record<Modality, { label: string; color: string }> = {
  rgb:           { label: 'RGB',  color: '#9bd1ff' },
  multispectral: { label: 'MSI',  color: '#a78bfa' },
  sar:           { label: 'SAR',  color: '#fca56a' },
  hsi:           { label: 'HSI',  color: '#ff79c6' },
  fmv:           { label: 'FMV',  color: '#5ee0a0' },
};
export function ModalityBadge({ m = 'rgb', size = 'sm' }: { m?: Modality | string; size?: 'xs' | 'sm' }) {
  const meta = MODALITY_META[(m as Modality)] || MODALITY_META.rgb;
  const fz = size === 'xs' ? 9 : 10;
  return (
    <span className="mono" style={{
      display: 'inline-flex', alignItems: 'center',
      padding: size === 'xs' ? '1px 5px' : '2px 7px',
      fontSize: fz, letterSpacing: '.08em',
      color: meta.color,
      border: `1px solid ${meta.color}`,
      background: `color-mix(in oklab, ${meta.color} 12%, transparent)`,
      borderRadius: 2, textTransform: 'uppercase',
    }} title={`Sensor modality: ${meta.label}`}>{meta.label}</span>
  );
}

/* ─── EmbeddingBadge ──────────────────────────────────────────────────── */

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
    <span className="mono" style={{
      display: 'inline-flex', alignItems: 'center',
      padding: '2px 7px', fontSize: 9.5, letterSpacing: '.08em',
      color: meta.color,
      background: `color-mix(in oklab, ${meta.color} 14%, transparent)`,
      border: `1px solid color-mix(in oklab, ${meta.color} 50%, transparent)`,
      borderRadius: 2,
    }} title={`Embedding head: ${meta.label}`}>{meta.label}</span>
  );
}

/* ─── CursorReadout (extracted from App.tsx) ──────────────────────────── */

export type CursorPos = { lat: number; lon: number } | null;

/**
 * Compact lat/lon readout sized to fit Shell's statusBar.
 * Renders an em-dash placeholder when `cursor` is null so the row's layout
 * stays stable as the user moves between workspaces.
 */
export function CursorReadout({ cursor }: { cursor: CursorPos }) {
  if (!cursor) {
    return (
      <span className="mono cursor-readout cursor-readout--empty" title="Hover the map for coordinates" aria-hidden>
        <span style={{ color: 'var(--ink-3)' }}>LAT</span>
        <span style={{ color: 'var(--ink-3)' }}>—</span>
        <span style={{ width: 1, height: 12, background: 'var(--line-2)' }}/>
        <span style={{ color: 'var(--ink-3)' }}>LON</span>
        <span style={{ color: 'var(--ink-3)' }}>—</span>
      </span>
    );
  }
  return (
    <span
      className="mono cursor-readout"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 10.5,
        color: 'var(--ink-1)',
        fontVariantNumeric: 'tabular-nums',
      }}
      title="Cursor latitude / longitude (WGS84)"
      aria-live="off"
    >
      <span style={{ color: 'var(--ink-2)' }}>LAT</span>
      <span style={{ color: 'var(--ink-0)', minInlineSize: '4.4rem', textAlign: 'right' }}>
        {cursor.lat.toFixed(4)}° {cursor.lat >= 0 ? 'N' : 'S'}
      </span>
      <span style={{ width: 1, height: 12, background: 'var(--line-2)' }}/>
      <span style={{ color: 'var(--ink-2)' }}>LON</span>
      <span style={{ color: 'var(--ink-0)', minInlineSize: '4.4rem', textAlign: 'right' }}>
        {Math.abs(cursor.lon).toFixed(4)}° {cursor.lon >= 0 ? 'E' : 'W'}
      </span>
    </span>
  );
}

/* ─── Panel (now a CSS container) ─────────────────────────────────────── */

/**
 * Card panel with optional title/sub header.
 *
 * Establishes a CSS container (`container-type: inline-size`) so children can
 * adapt with @container (max-width: ...) rules rather than the page-level
 * media query. Use the `containerName` prop when nesting multiple panels.
 */
export function Panel({
  title, sub, right, children, style, containerName,
}: {
  title?: ReactNode;
  sub?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  style?: CSSProperties;
  containerName?: string;
}) {
  return (
    <div
      className="card responsive-panel container-panel"
      style={{
        background: 'var(--bg-1)',
        border: '1px solid var(--line)',
        borderRadius: 10,
        containerType: 'inline-size',
        containerName: containerName as any,
        ...style,
      }}
    >
      {(title || sub || right) && (
        <div className="panel-title-row" style={{ alignItems: 'baseline', marginBottom: 12 }}>
          {title && <span style={{ fontSize: 13, fontWeight: 600 }}>{title}</span>}
          {sub && <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>{sub}</span>}
          <span style={{ flex: 1 }}/>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

/**
 * Shell — top-level workstation chrome.
 *
 * Layout:  collapsible icon-rail sidebar + topbar (workspace + breadcrumb,
 *          ⌘K command palette, alerts, analyst chip) + status bar.
 *
 * Wired to /api/health and /api/ingest/uploads so the status reflects real
 * backend state.
 *
 * Changes vs previous revision:
 *   1. Polling interval is created ONCE on mount — cadence is selected
 *      inside the tick callback so we don't re-create the interval on every
 *      `activeUploads.length` change (which was causing effect-cleanup races).
 *   2. ⌘K replaced window.prompt with a real CommandPalette popover.
 *      Cmd/Ctrl+K binds globally; Esc closes; arrow keys navigate; Enter
 *      activates. Supports workspace jumps and DET-1234 detection lookups.
 *   3. CursorReadout extracted to atoms.tsx — App.tsx renders the readout
 *      via statusRight prop; Shell no longer knows about cursor positions.
 *   4. shell-grid / shell-body / shell-topbar / shell-statusbar all become
 *      CSS containers (see index.css) so the analyst chip name, the upload
 *      indicator, and the context line collapse independently rather than
 *      being driven by viewport media queries.
 *   5. Topbar Bell button has aria-label; alerts navigation uses CustomEvent.
 *
 * `CursorReadout` is no longer defined here — pass it in via `statusRight`.
 */

import {
  useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import type { CSSProperties, ReactNode } from 'react';
import axios from 'axios';
import {
  Bell, ChevronDown, Crosshair, Film, GitBranch, LogOut, Map as MapIcon, Search, UploadCloud,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { StatusDot } from './atoms';
import { useAuth } from '../hooks/useAuth';
import {
  type UploadJob,
  isUploadActive,
  uploadMessage,
  uploadMetadata,
  uploadProgress,
  uploadStage,
} from '../utils/uploadProgress';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

export type WorkspaceKey = 'ingest' | 'map' | 'fmv' | 'graph' | 'admin';

type NavItem = { key: WorkspaceKey; label: string; short: string; Icon: LucideIcon };

const NAV: NavItem[] = [
  { key: 'ingest', label: 'Ingest',      short: 'ING', Icon: UploadCloud },
  { key: 'map',    label: 'Geoint',      short: 'GEO', Icon: MapIcon },
  { key: 'fmv',    label: 'Drone Video', short: 'FMV', Icon: Film },
  { key: 'graph',  label: 'Link Graph',  short: 'LNK', Icon: Crosshair },
  { key: 'admin',  label: 'Admin',       short: 'ADM', Icon: GitBranch },
];

const FAST_TICK_MS = 2000;
const SLOW_TICK_MS = 15000;

type Health = { healthy?: boolean; neo4j?: string; postgis?: string };

function useClock() {
  const [t, setT] = useState(new Date());
  useEffect(() => {
    const id = window.setInterval(() => setT(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return t;
}

function useSystemStatus() {
  const [health, setHealth] = useState<Health>({});
  const [activeUploads, setActiveUploads] = useState<UploadJob[]>([]);
  const activeRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;

    const tick = async () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      try {
        const [h, u] = await Promise.all([
          axios.get<Health>(`${API_URL}/api/health`),
          axios.get<{ uploads?: UploadJob[] }>(`${API_URL}/api/ingest/uploads`),
        ]);
        if (cancelled) return;
        setHealth(h.data ?? {});
        const active = (u.data?.uploads ?? []).filter(isUploadActive);
        activeRef.current = active.length;
        setActiveUploads(active);
      } catch {
        if (!cancelled) setHealth({ healthy: false });
      } finally {
        inFlight = false;
      }
    };

    tick();

    // Single interval that re-checks at fast cadence when uploads are running,
    // slow otherwise — without restarting the timer on every state change.
    let id: number | undefined;
    const reschedule = () => {
      if (id != null) window.clearInterval(id);
      const cadence = activeRef.current > 0 ? FAST_TICK_MS : SLOW_TICK_MS;
      id = window.setInterval(async () => {
        await tick();
        reschedule();
      }, cadence);
    };
    reschedule();

    return () => {
      cancelled = true;
      if (id != null) window.clearInterval(id);
    };
  }, []);

  const activeImageryJob = useMemo(
    () => activeUploads.find((job) => job.media_type === 'imagery') || null,
    [activeUploads],
  );
  return { health, uploadCount: activeUploads.length, activeImageryJob };
}

type ShellProps = {
  active: WorkspaceKey;
  onNavigate: (key: WorkspaceKey) => void;
  children: ReactNode;
  contextLine?: string;
  /** Right-side content slotted into the status bar (cursor readout, etc). */
  statusRight?: ReactNode;
};

export function Shell({ active, onNavigate, children, contextLine, statusRight }: ShellProps) {
  const [hover, setHover] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const activeNav = useMemo(() => NAV.find((n) => n.key === active) ?? NAV[0], [active]);
  const { health, uploadCount, activeImageryJob } = useSystemStatus();
  const clock = useClock();

  // Global ⌘K / Ctrl+K
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setPaletteOpen(true);
      } else if (e.key === 'Escape') {
        setPaletteOpen(false);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const services = (() => {
    let up = 1;
    let total = 3;
    if (health.neo4j === 'ok') up += 1;
    if (health.postgis === 'ok') up += 1;
    if (health.healthy === false) up = 0;
    return { up, total };
  })();
  const allOk = services.up === services.total;

  return (
    <div
      data-shell="modern"
      className="shell-grid"
      style={{
        height: '100%',
        display: 'grid',
        background: 'var(--bg-0)',
        color: 'var(--ink-0)',
        fontFamily: 'var(--font-sans)',
        fontSize: 'var(--text-sm)',
        overflow: 'hidden',
        position: 'relative',
      }}
    >
      <div
        className="shell-rail"
        style={{ position: 'relative', height: '100%', zIndex: 1000 }}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
      >
        <aside
          className="shell-aside"
          style={{
            ['--rail-width' as any]: hover ? 'var(--rail-expanded)' : 'var(--rail-collapsed)',
            position: 'absolute', top: 0, left: 0, bottom: 0,
            background: 'var(--bg-1)',
            borderRight: '1px solid var(--line)',
            display: 'flex',
            flexDirection: 'column',
            padding: hover ? 'var(--space-3)' : 'var(--space-3) var(--space-2)',
            gap: 'var(--space-3)',
            transition: 'width .18s ease, padding .18s ease, box-shadow .18s ease',
            boxShadow: hover ? '10px 0 28px rgba(0,0,0,.40)' : 'none',
            overflow: 'hidden',
          }}
          aria-label="Workspace navigation"
        >
          <Brand expanded={hover}/>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <div className="label-mono shell-rail-section" style={{
              padding: '4px 6px', fontSize: 10, opacity: hover ? 1 : 0, transition: 'opacity .1s', whiteSpace: 'nowrap',
            }}>
              Workspaces
            </div>
            {NAV.map((n) => (
              <NavButton key={n.key} item={n} active={active === n.key} expanded={hover} onClick={() => onNavigate(n.key)} />
            ))}
          </div>
          <div style={{ flex: 1 }}/>
          <SidebarFooter expanded={hover} allOk={allOk} services={services}/>
        </aside>
      </div>

      <div className="shell-body" style={{ minWidth: 0, display: 'grid' }}>
        <Topbar
          workspaceLabel={activeNav.label}
          contextLine={contextLine ?? `AOR · Live · UTC ${clock.toISOString().slice(11, 19)}`}
          onNavigate={onNavigate}
          onOpenPalette={() => setPaletteOpen(true)}
        />

        <main
          className="shell-main"
          style={{ minWidth: 0, minHeight: 0, overflow: 'hidden', background: 'var(--bg-0)' }}
        >
          {children}
        </main>

        <StatusBar
          uploadCount={uploadCount}
          activeImageryJob={activeImageryJob}
          allOk={allOk}
          clock={clock}
          statusRight={statusRight}
        />
      </div>

      {paletteOpen && (
        <CommandPalette
          onClose={() => setPaletteOpen(false)}
          onNavigate={(k) => { setPaletteOpen(false); onNavigate(k); }}
        />
      )}
    </div>
  );
}

/* ── Brand ────────────────────────────────────────────────────────────── */

function Brand({ expanded }: { expanded: boolean }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: expanded ? '0 4px 12px' : '0 0 12px',
      borderBottom: '1px solid var(--line)',
      justifyContent: expanded ? 'flex-start' : 'center',
    }}>
      <div style={{
        width: 30, height: 30, display: 'grid', placeItems: 'center', flexShrink: 0,
        background: 'color-mix(in oklab, var(--accent) 18%, var(--bg-2))',
        border: '1px solid color-mix(in oklab, var(--accent) 60%, transparent)',
        color: 'var(--accent)', borderRadius: 6, fontWeight: 700, fontFamily: 'var(--font-mono)', fontSize: 13,
      }} aria-hidden>S</div>
      <div style={{
        display: 'flex', flexDirection: 'column', lineHeight: 1.2,
        opacity: expanded ? 1 : 0,
        transition: 'opacity .12s ease .04s',
        whiteSpace: 'nowrap', overflow: 'hidden',
      }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>Sentinel</span>
        <span className="mono" style={{ color: 'var(--ink-2)', fontSize: 10, letterSpacing: '.06em' }}>GEOINT WORKSTATION</span>
      </div>
    </div>
  );
}

/* ── Nav button ──────────────────────────────────────────────────────── */

function NavButton({ item, active, expanded, onClick }: { item: NavItem; active: boolean; expanded: boolean; onClick: () => void }) {
  const { Icon } = item;
  const style: CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 12,
    height: 38,
    padding: expanded ? '0 12px' : '0',
    justifyContent: expanded ? 'flex-start' : 'center',
    border: '1px solid ' + (active ? 'var(--line-2)' : 'transparent'),
    background: active ? 'var(--bg-2)' : 'transparent',
    color: active ? 'var(--ink-0)' : 'var(--ink-1)',
    borderRadius: 8, cursor: 'pointer', textAlign: 'left', fontSize: 12.5,
    position: 'relative', overflow: 'hidden',
  };
  return (
    <button title={item.label} onClick={onClick} style={style} type="button" aria-current={active ? 'page' : undefined}>
      {active && (
        <span style={{
          position: 'absolute', left: 0, top: 8, bottom: 8, width: 3,
          background: 'var(--accent)', borderRadius: '0 3px 3px 0',
        }}/>
      )}
      <Icon size={17} style={{ flexShrink: 0, color: active ? 'var(--accent)' : undefined }} aria-hidden/>
      {expanded && <span style={{ flex: 1, whiteSpace: 'nowrap' }}>{item.label}</span>}
    </button>
  );
}

/* ── Sidebar footer ──────────────────────────────────────────────────── */

function SidebarFooter({ expanded, allOk, services }: { expanded: boolean; allOk: boolean; services: { up: number; total: number } }) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 6,
      padding: expanded ? '10px 4px' : '10px 0',
      borderTop: '1px solid var(--line)',
      alignItems: expanded ? 'stretch' : 'center',
    }}>
      {expanded ? (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11 }}>
            <StatusDot tone={allOk ? 'ok' : 'crit'} pulse={allOk}/>
            <span style={{ color: 'var(--ink-1)' }}>{allOk ? 'All systems nominal' : 'System degraded'}</span>
          </div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>{services.up}/{services.total} services</div>
        </>
      ) : <StatusDot tone={allOk ? 'ok' : 'crit'} pulse={allOk}/>}
    </div>
  );
}

/* ── Topbar ──────────────────────────────────────────────────────────── */

function Topbar({ workspaceLabel, contextLine, onNavigate, onOpenPalette }: {
  workspaceLabel: string;
  contextLine: string;
  onNavigate: (k: WorkspaceKey) => void;
  onOpenPalette: () => void;
}) {
  return (
    <header
      className="shell-topbar"
      style={{
        display: 'flex', alignItems: 'center',
        gap: 'var(--space-3)',
        paddingInline: 'var(--space-4)',
        borderBottom: '1px solid var(--line)',
        background: 'var(--bg-1)',
      }}
    >
      <div className="shell-topbar-title" style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.15, minWidth: 0 }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>{workspaceLabel}</span>
        <span className="mono shell-context-line" style={{
          fontSize: 10, color: 'var(--ink-2)', letterSpacing: '.06em',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {contextLine}
        </span>
      </div>
      <div style={{ flex: 1 }}/>
      <button
        className="btn ghost sm rounded shell-jump"
        style={{ gap: 8, height: 30, border: '1px solid var(--line)' }}
        type="button"
        onClick={onOpenPalette}
        title="Command palette (⌘K)"
        aria-haspopup="dialog"
      >
        <Search size={13} aria-hidden/>
        <span className="shell-jump-label" style={{ color: 'var(--ink-2)' }}>Jump to anything…</span>
        <span className="kbd">⌘K</span>
      </button>
      <button
        className="btn sm rounded icon"
        type="button"
        title="View health alerts"
        aria-label="View health alerts"
        onClick={() => {
          onNavigate('admin');
          window.dispatchEvent(new CustomEvent('sentinel:admin-tab', { detail: { tab: 'alerts' } }));
        }}
      >
        <Bell size={13} aria-hidden/>
      </button>
      <AnalystChip/>
    </header>
  );
}

/* ── Analyst chip ────────────────────────────────────────────────────── */

function AnalystChip() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const initials = (user?.display_name || user?.username || 'AN')
    .split(/[\s.]+/)
    .map((s) => s[0]?.toUpperCase() || '')
    .join('')
    .slice(0, 2) || 'AN';
  const accent = user?.role === 'admin' ? 'var(--accent)' : 'var(--nato-friend)';
  return (
    <div className="analyst-chip" style={{ position: 'relative' }}>
      <button
        type="button" onClick={() => setOpen((o) => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '4px 10px 4px 4px',
          border: '1px solid var(--line)', borderRadius: 999,
          background: 'var(--bg-2)', cursor: 'pointer', color: 'inherit',
        }}
        title={user?.username || 'profile'}
        aria-haspopup="menu" aria-expanded={open}
      >
        <div style={{
          width: 24, height: 24, borderRadius: 999,
          background: `color-mix(in oklab, ${accent} 30%, var(--bg-3))`,
          display: 'grid', placeItems: 'center',
          color: accent, fontWeight: 600, fontSize: 11,
        }} aria-hidden>{initials}</div>
        <span className="analyst-chip-name" style={{ fontSize: 11.5 }}>
          {user?.display_name || user?.username || 'Operator'}
        </span>
        <span className="analyst-chip-role mono" style={{ color: 'var(--ink-2)', fontSize: 10 }}>
          · {(user?.role || 'analyst').toUpperCase()}
        </span>
        <ChevronDown size={12} style={{ color: 'var(--ink-3)' }} aria-hidden/>
      </button>
      {open && (
        <div
          onMouseLeave={() => setOpen(false)}
          role="menu"
          style={{
            position: 'absolute', top: 'calc(100% + 6px)', right: 0,
            minWidth: 200, zIndex: 1500,
            background: 'var(--bg-1)',
            border: '1px solid var(--line)',
            boxShadow: '0 8px 28px rgba(0,0,0,.45)',
            padding: 8,
            display: 'flex', flexDirection: 'column', gap: 4,
          }}
        >
          <div style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)' }}>
            <div style={{ fontSize: 12, fontWeight: 600 }}>{user?.display_name || user?.username}</div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
              {user?.email || user?.username} · {(user?.role || 'analyst').toUpperCase()}
            </div>
          </div>
          <button
            type="button" role="menuitem"
            onClick={async () => { setOpen(false); await logout(); }}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '8px 10px', border: 0, background: 'transparent',
              color: 'var(--ink-1)', cursor: 'pointer', fontSize: 12, textAlign: 'left',
            }}
          >
            <LogOut size={13} aria-hidden/> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

/* ── Imagery job indicator (statusbar) ───────────────────────────────── */

function formatEta(s: number): string {
  if (!Number.isFinite(s) || s <= 0) return '';
  if (s < 60) return `≈ ${Math.round(s)}s`;
  return `≈ ${Math.round(s / 60)}m`;
}

function ImageryJobIndicator({ job }: { job: UploadJob }) {
  const progress = uploadProgress(job);
  const meta = uploadMetadata(job);
  const processed = Number(meta.processed_chips);
  const total = Number(meta.total_chips ?? meta.planned_chips);
  const createdAt = job.created_at ? new Date(job.created_at).getTime() : NaN;
  let eta = '';
  if (Number.isFinite(createdAt) && Number.isFinite(processed) && Number.isFinite(total) && processed > 0 && processed < total) {
    const elapsedSec = (Date.now() - createdAt) / 1000;
    eta = formatEta((elapsedSec / processed) * (total - processed));
  }
  return (
    <span
      className="imagery-job-indicator"
      role="status" aria-live="polite"
      style={{ display: 'inline-flex', alignItems: 'center', gap: 8, minWidth: 0 }}
    >
      <span className="imagery-job-filename mono">{job.filename}</span>
      <span className="mono imagery-job-stage" style={{ color: 'var(--ink-2)' }}>{uploadStage(job)}</span>
      <span className="imagery-job-message mono" style={{ color: 'var(--ink-3)' }}>{uploadMessage(job)}</span>
      <span aria-hidden style={{
        width: 96, height: 4, background: 'var(--line-2)',
        border: '1px solid var(--line)', position: 'relative', flexShrink: 0,
      }}>
        <span style={{ position: 'absolute', inset: 0, width: `${progress}%`, background: 'var(--accent)', transition: 'width 400ms linear' }}/>
      </span>
      <span className="mono" style={{ color: 'var(--ink-2)', fontVariantNumeric: 'tabular-nums' }}>{progress}%</span>
      {eta && <span className="mono imagery-job-eta" style={{ color: 'var(--ink-3)' }}>{eta}</span>}
    </span>
  );
}

/* ── Status bar ──────────────────────────────────────────────────────── */

function StatusBar({ uploadCount, activeImageryJob, allOk, clock, statusRight }: {
  uploadCount: number;
  activeImageryJob: UploadJob | null;
  allOk: boolean;
  clock: Date;
  statusRight?: ReactNode;
}) {
  return (
    <footer
      className="shell-statusbar"
      style={{
        display: 'flex', alignItems: 'center',
        gap: 'var(--space-3)',
        paddingInline: 'var(--space-4)',
        borderTop: '1px solid var(--line)',
        background: 'var(--bg-1)',
        fontSize: 'var(--text-2xs)',
        color: 'var(--ink-2)',
      }}
      role="contentinfo"
    >
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <StatusDot tone={allOk ? 'ok' : 'crit'} size={6} pulse={allOk}/>
        <span style={{ color: allOk ? 'var(--ok)' : 'var(--crit)' }}>{allOk ? 'Connected' : 'Degraded'}</span>
      </span>
      <span className="mono">{uploadCount} upload{uploadCount === 1 ? '' : 's'} active</span>
      {activeImageryJob && <ImageryJobIndicator job={activeImageryJob}/>}
      <div style={{ flex: 1 }}/>
      {statusRight}
      <span className="mono">{clock.toISOString().slice(0, 19)}Z</span>
    </footer>
  );
}

/* ── Command palette ─────────────────────────────────────────────────── */

type Command = {
  id: string;
  group: string;
  label: string;
  hint?: string;
  run: () => void;
};

function CommandPalette({ onClose, onNavigate }: { onClose: () => void; onNavigate: (k: WorkspaceKey) => void }) {
  const [q, setQ] = useState('');
  const [idx, setIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const baseCommands: Command[] = useMemo(() => [
    ...NAV.map<Command>((n) => ({
      id: `nav-${n.key}`, group: 'Workspace', label: `Go to ${n.label}`, hint: n.short,
      run: () => onNavigate(n.key),
    })),
    {
      id: 'alerts', group: 'Quick action', label: 'Open health alerts',
      run: () => { onNavigate('admin'); window.dispatchEvent(new CustomEvent('sentinel:admin-tab', { detail: { tab: 'alerts' } })); },
    },
  ], [onNavigate]);

  const filtered = useMemo(() => {
    const cmds = [...baseCommands];
    const m = q.trim().match(/^(?:det[-_])?(\d+)$/i);
    if (m) {
      const id = Number(m[1]);
      cmds.unshift({
        id: 'jump-det', group: 'Jump',
        label: `Jump to DET-${id}`,
        hint: 'Opens in Geoint',
        run: () => {
          onNavigate('map');
          window.dispatchEvent(new CustomEvent('sentinel:jump-to-detection', { detail: { id } }));
        },
      });
    }
    if (!q.trim()) return cmds;
    const s = q.toLowerCase();
    return cmds.filter((c) => c.label.toLowerCase().includes(s) || (c.hint || '').toLowerCase().includes(s));
  }, [q, baseCommands, onNavigate]);

  useEffect(() => { setIdx(0); }, [q]);

  const handleKey = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(i + 1, filtered.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      const cmd = filtered[idx];
      if (cmd) { cmd.run(); onClose(); }
    } else if (e.key === 'Escape') {
      e.preventDefault(); onClose();
    }
  }, [filtered, idx, onClose]);

  return (
    <div
      role="dialog" aria-modal="true" aria-label="Command palette"
      style={{
        position: 'fixed', inset: 0, zIndex: 2000,
        display: 'grid', placeItems: 'start center',
        paddingTop: '12vh',
        background: 'color-mix(in oklab, var(--bg-0) 60%, transparent)',
        backdropFilter: 'blur(4px)',
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 'min(560px, calc(100vw - 32px))',
          background: 'var(--bg-1)',
          border: '1px solid var(--line)',
          boxShadow: '0 24px 48px rgba(0,0,0,.55)',
          borderRadius: 12, overflow: 'hidden',
          display: 'flex', flexDirection: 'column',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 14px', borderBottom: '1px solid var(--line)' }}>
          <Search size={14} style={{ color: 'var(--ink-2)' }} aria-hidden/>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Workspace, action, or DET-1234…"
            aria-label="Command palette input"
            style={{
              flex: 1, border: 0, outline: 'none', background: 'transparent',
              color: 'var(--ink-0)', fontSize: 14, fontFamily: 'inherit',
            }}
          />
          <span className="kbd">Esc</span>
        </div>
        <div
          role="listbox" aria-label="Command results"
          style={{ maxHeight: 320, overflowY: 'auto', padding: 6 }}
        >
          {filtered.length === 0 && (
            <div className="mono" style={{ padding: 14, fontSize: 11, color: 'var(--ink-3)' }}>
              No matches.
            </div>
          )}
          {filtered.map((c, i) => (
            <button
              key={c.id}
              role="option"
              aria-selected={i === idx}
              onMouseEnter={() => setIdx(i)}
              onClick={() => { c.run(); onClose(); }}
              style={{
                width: '100%',
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '8px 12px',
                background: i === idx ? 'var(--bg-2)' : 'transparent',
                border: 0, color: 'var(--ink-0)',
                fontSize: 12.5, textAlign: 'left', cursor: 'pointer', borderRadius: 6,
              }}
            >
              <span className="mono" style={{ fontSize: 9.5, color: 'var(--ink-3)', minWidth: 70 }}>
                {c.group}
              </span>
              <span style={{ flex: 1 }}>{c.label}</span>
              {c.hint && <span className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>{c.hint}</span>}
            </button>
          ))}
        </div>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          padding: '8px 12px', borderTop: '1px solid var(--line)',
          fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-3)',
        }}>
          <span><span className="kbd">↑↓</span> navigate</span>
          <span><span className="kbd">↵</span> select</span>
          <span><span className="kbd">Esc</span> close</span>
        </div>
      </div>
    </div>
  );
}

export default Shell;

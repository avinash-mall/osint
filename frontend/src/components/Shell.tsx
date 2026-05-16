/**
 * ShellModern — top-level workstation chrome.
 *
 * Layout:  64px icon-rail sidebar (expands to 224px on hover, floats over canvas) +
 *          52px topbar (workspace + AOR breadcrumb, ⌘K search, alerts, analyst chip) +
 *          28px status bar (live health + uploads + Zulu time).
 *
 * Wired to /api/health and /api/ingest/uploads so the status reflects real backend state.
 */

import { useEffect, useMemo, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import axios from 'axios';
import {
  Bell,
  ChevronDown,
  Crosshair,
  Film,
  GitBranch,
  LogOut,
  Map as MapIcon,
  Search,
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

export type WorkspaceKey = 'map' | 'fmv' | 'graph' | 'admin';

type NavItem = {
  key: WorkspaceKey;
  label: string;
  short: string;
  Icon: LucideIcon;
  badge?: number | string;
};

const NAV: NavItem[] = [
  { key: 'map',   label: 'Geoint',      short: 'GEO', Icon: MapIcon },
  { key: 'fmv',   label: 'Drone Video', short: 'FMV', Icon: Film },
  { key: 'graph', label: 'Link Graph',  short: 'LNK', Icon: Crosshair },
  { key: 'admin', label: 'Admin',       short: 'ADM', Icon: GitBranch },
];

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
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const [h, u] = await Promise.all([
          axios.get<Health>(`${API_URL}/api/health`),
          axios.get<{ uploads?: UploadJob[] }>(`${API_URL}/api/ingest/uploads`),
        ]);
        if (cancelled) return;
        setHealth(h.data ?? {});
        setActiveUploads((u.data?.uploads ?? []).filter(isUploadActive));
      } catch {
        if (!cancelled) setHealth({ healthy: false });
      }
    };
    refresh();
    // Fast polling while an upload is in flight so the status-bar progress
    // tracks chip-by-chip movement; slow otherwise.
    const fastTickWindow = activeUploads.length > 0 ? 2000 : 15000;
    const id = window.setInterval(refresh, fastTickWindow);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [activeUploads.length]);
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
  /** Optional right-side hint for the topbar (e.g. AOR/UTC line). */
  contextLine?: string;
  /** Optional right-side content slotted into the status bar. */
  statusRight?: ReactNode;
};

export function Shell({ active, onNavigate, children, contextLine, statusRight }: ShellProps) {
  const [hover, setHover] = useState(false);
  const activeNav = useMemo(() => NAV.find((n) => n.key === active) ?? NAV[0], [active]);
  const { health, uploadCount, activeImageryJob } = useSystemStatus();
  const clock = useClock();

  const services = (() => {
    let up = 1; // API itself responded
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
      {/* Reserved column — the floating aside expands on hover.
          z-index has to beat the map workspace's floating glass panels
          (zIndex 500) so the sidebar overlays them instead of getting
          covered when it expands. */}
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
            position: 'absolute',
            top: 0,
            left: 0,
            bottom: 0,
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
        >
          <Brand expanded={hover} />

          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <div
              className="label-mono"
              style={{
                padding: '4px 6px',
                fontSize: 10,
                opacity: hover ? 1 : 0,
                transition: 'opacity .1s',
                whiteSpace: 'nowrap',
              }}
            >
              Workspaces
            </div>
            {NAV.map((n) => (
              <NavButton key={n.key} item={n} active={active === n.key} expanded={hover} onClick={() => onNavigate(n.key)} />
            ))}
          </div>

          <div style={{ flex: 1 }} />

          <SidebarFooter expanded={hover} allOk={allOk} services={services} />
        </aside>
      </div>

      <div className="shell-body" style={{ minWidth: 0, display: 'grid' }}>
        <Topbar
          workspaceLabel={activeNav.label}
          contextLine={contextLine ?? `AOR · Live · UTC ${clock.toISOString().slice(11, 19)}`}
          onNavigate={onNavigate}
        />

        <main className="shell-main" style={{ minWidth: 0, minHeight: 0, overflow: 'hidden', background: 'var(--bg-0)' }}>
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
    </div>
  );
}

function Brand({ expanded }: { expanded: boolean }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: expanded ? '0 4px 12px' : '0 0 12px',
        borderBottom: '1px solid var(--line)',
        justifyContent: expanded ? 'flex-start' : 'center',
      }}
    >
      <div
        style={{
          width: 30,
          height: 30,
          display: 'grid',
          placeItems: 'center',
          flexShrink: 0,
          background: 'color-mix(in oklab, var(--accent) 18%, var(--bg-2))',
          border: '1px solid color-mix(in oklab, var(--accent) 60%, transparent)',
          color: 'var(--accent)',
          borderRadius: 6,
          fontWeight: 700,
          fontFamily: 'var(--font-mono)',
          fontSize: 13,
        }}
      >
        S
      </div>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          lineHeight: 1.2,
          opacity: expanded ? 1 : 0,
          transition: 'opacity .12s ease .04s',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
        }}
      >
        <span style={{ fontWeight: 600, fontSize: 13 }}>Sentinel</span>
        <span
          className="mono"
          style={{ color: 'var(--ink-2)', fontSize: 10, letterSpacing: '.06em' }}
        >
          GEOINT WORKSTATION
        </span>
      </div>
    </div>
  );
}

function NavButton({
  item,
  active,
  expanded,
  onClick,
}: {
  item: NavItem;
  active: boolean;
  expanded: boolean;
  onClick: () => void;
}) {
  const { Icon } = item;
  const style: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    height: 38,
    padding: expanded ? '0 12px' : '0',
    justifyContent: expanded ? 'flex-start' : 'center',
    border: '1px solid ' + (active ? 'var(--line-2)' : 'transparent'),
    background: active ? 'var(--bg-2)' : 'transparent',
    color: active ? 'var(--ink-0)' : 'var(--ink-1)',
    borderRadius: 8,
    cursor: 'pointer',
    textAlign: 'left',
    fontSize: 12.5,
    position: 'relative',
    overflow: 'hidden',
  };
  return (
    <button title={item.label} onClick={onClick} style={style} type="button">
      {active && (
        <span
          style={{
            position: 'absolute',
            left: 0,
            top: 8,
            bottom: 8,
            width: 3,
            background: 'var(--accent)',
            borderRadius: '0 3px 3px 0',
          }}
        />
      )}
      <Icon size={17} style={{ flexShrink: 0, color: active ? 'var(--accent)' : undefined }} />
      {expanded && (
        <>
          <span style={{ flex: 1, whiteSpace: 'nowrap' }}>{item.label}</span>
          {item.badge != null && (
            <span className="mono" style={{ color: 'var(--ink-2)', fontSize: 10 }}>
              {item.badge}
            </span>
          )}
        </>
      )}
    </button>
  );
}

function SidebarFooter({
  expanded,
  allOk,
  services,
}: {
  expanded: boolean;
  allOk: boolean;
  services: { up: number; total: number };
}) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        padding: expanded ? '10px 4px' : '10px 0',
        borderTop: '1px solid var(--line)',
        alignItems: expanded ? 'stretch' : 'center',
      }}
    >
      {expanded ? (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11 }}>
            <StatusDot tone={allOk ? 'ok' : 'crit'} pulse={allOk} />
            <span style={{ color: 'var(--ink-1)' }}>
              {allOk ? 'All systems nominal' : 'System degraded'}
            </span>
          </div>
          <div className="mono" style={{ fontSize: 10, color: 'var(--ink-2)' }}>
            {services.up}/{services.total} services
          </div>
        </>
      ) : (
        <StatusDot tone={allOk ? 'ok' : 'crit'} pulse={allOk} />
      )}
    </div>
  );
}

function Topbar({
  workspaceLabel,
  contextLine,
  onNavigate,
}: {
  workspaceLabel: string;
  contextLine: string;
  onNavigate: (key: WorkspaceKey) => void;
}) {
  const handleJump = () => {
    const raw = window.prompt('Jump to · enter detection id (e.g. DET-1234) or workspace key (map/fmv/graph/admin):');
    if (!raw) return;
    const v = raw.trim().toLowerCase();
    if (['map', 'fmv', 'graph', 'admin'].includes(v)) {
      onNavigate(v as WorkspaceKey);
      return;
    }
    const m = v.match(/(?:det[-_])?(\d+)/);
    if (m) {
      const id = Number(m[1]);
      if (!Number.isNaN(id)) {
        // Dispatch a window event the workspaces can listen to; lightweight
        // alternative to plumbing a separate handler through every layer.
        onNavigate('map');
        window.dispatchEvent(new CustomEvent('sentinel:jump-to-detection', { detail: { id } }));
        return;
      }
    }
    alert(`Couldn't interpret "${raw}". Try a workspace key or "DET-1234".`);
  };
  return (
    <header
      className="shell-topbar"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-3)',
        paddingInline: 'var(--space-4)',
        borderBottom: '1px solid var(--line)',
        background: 'var(--bg-1)',
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.15 }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>{workspaceLabel}</span>
        <span
          className="mono"
          style={{ fontSize: 10, color: 'var(--ink-2)', letterSpacing: '.06em' }}
        >
          {contextLine}
        </span>
      </div>
      <div style={{ flex: 1 }} />
      <button
        className="btn ghost sm rounded shell-jump"
        style={{ gap: 8, height: 30, border: '1px solid var(--line)' }}
        type="button"
        onClick={handleJump}
        title="Jump to a detection or workspace"
      >
        <Search size={13} />
        <span style={{ color: 'var(--ink-2)' }}>Jump to anything…</span>
        <span className="kbd">⌘K</span>
      </button>
      <button
        className="btn sm rounded icon"
        type="button"
        title="View health alerts"
        onClick={() => {
          onNavigate('admin');
          window.dispatchEvent(new CustomEvent('sentinel:admin-tab', { detail: { tab: 'alerts' } }));
        }}
      >
        <Bell size={13} />
      </button>
      <AnalystChip />
    </header>
  );
}

function AnalystChip() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const initials = (user?.display_name || user?.username || 'AN')
    .split(/[\s.]+/)
    .map((s) => s[0]?.toUpperCase() || '')
    .join('')
    .slice(0, 2) || 'AN';
  const accent =
    user?.role === 'admin' ? 'var(--accent)' : 'var(--nato-friend)';
  return (
    <div className="analyst-chip" style={{ position: 'relative' }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '4px 10px 4px 4px',
          border: '1px solid var(--line)',
          borderRadius: 999,
          background: 'var(--bg-2)',
          cursor: 'pointer',
          color: 'inherit',
        }}
        title={user?.username || 'profile'}
      >
        <div
          style={{
            width: 24,
            height: 24,
            borderRadius: 999,
            background: `color-mix(in oklab, ${accent} 30%, var(--bg-3))`,
            display: 'grid',
            placeItems: 'center',
            color: accent,
            fontWeight: 600,
            fontSize: 11,
          }}
        >
          {initials}
        </div>
        <span className="analyst-chip-name" style={{ fontSize: 11.5 }}>{user?.display_name || user?.username || 'Operator'}</span>
        <span className="analyst-chip-role mono" style={{ color: 'var(--ink-2)', fontSize: 10 }}>
          · {(user?.role || 'analyst').toUpperCase()}
        </span>
        <ChevronDown size={12} style={{ color: 'var(--ink-3)' }} />
      </button>
      {open && (
        <div
          onMouseLeave={() => setOpen(false)}
          style={{
            position: 'absolute',
            top: 'calc(100% + 6px)',
            right: 0,
            minWidth: 200,
            zIndex: 1500,
            background: 'var(--bg-1)',
            border: '1px solid var(--line)',
            boxShadow: '0 8px 28px rgba(0,0,0,.45)',
            padding: 8,
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}
        >
          <div style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)' }}>
            <div style={{ fontSize: 12, fontWeight: 600 }}>{user?.display_name || user?.username}</div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>
              {user?.email || user?.username} · {(user?.role || 'analyst').toUpperCase()}
            </div>
          </div>
          <button
            type="button"
            onClick={async () => {
              setOpen(false);
              await logout();
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '8px 10px',
              border: 0,
              background: 'transparent',
              color: 'var(--ink-1)',
              cursor: 'pointer',
              fontSize: 12,
              textAlign: 'left',
            }}
          >
            <LogOut size={13} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

function formatEta(secondsRemaining: number): string {
  if (!Number.isFinite(secondsRemaining) || secondsRemaining <= 0) return '';
  if (secondsRemaining < 60) return `≈ ${Math.round(secondsRemaining)}s`;
  const minutes = Math.round(secondsRemaining / 60);
  return `≈ ${minutes}m`;
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
  const stage = uploadStage(job);
  const message = uploadMessage(job);
  return (
    <span
      className="imagery-job-indicator"
      role="status"
      aria-live="polite"
      style={{ display: 'inline-flex', alignItems: 'center', gap: 8, minWidth: 0 }}
    >
      <span className="imagery-job-filename mono" style={{ color: 'var(--ink-1)', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {job.filename}
      </span>
      <span className="mono" style={{ color: 'var(--ink-2)' }}>
        {stage}
      </span>
      <span className="imagery-job-message mono" style={{ color: 'var(--ink-3)', maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {message}
      </span>
      <span
        aria-hidden
        style={{
          width: 96,
          height: 4,
          background: 'var(--line-2)',
          border: '1px solid var(--line)',
          position: 'relative',
        }}
      >
        <span
          style={{
            position: 'absolute',
            inset: 0,
            width: `${progress}%`,
            background: 'var(--accent)',
            transition: 'width 400ms linear',
          }}
        />
      </span>
      <span className="mono" style={{ color: 'var(--ink-2)' }}>
        {progress}%
      </span>
      {eta && (
        <span className="mono" style={{ color: 'var(--ink-3)' }}>
          {eta}
        </span>
      )}
    </span>
  );
}

function StatusBar({
  uploadCount,
  activeImageryJob,
  allOk,
  clock,
  statusRight,
}: {
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
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-3)',
        paddingInline: 'var(--space-4)',
        borderTop: '1px solid var(--line)',
        background: 'var(--bg-1)',
        fontSize: 'var(--text-2xs)',
        color: 'var(--ink-2)',
      }}
    >
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <StatusDot tone={allOk ? 'ok' : 'crit'} size={6} pulse={allOk} />
        <span style={{ color: allOk ? 'var(--ok)' : 'var(--crit)' }}>
          {allOk ? 'Connected' : 'Degraded'}
        </span>
      </span>
      <span className="mono">
        {uploadCount} upload{uploadCount === 1 ? '' : 's'} active
      </span>
      {activeImageryJob && <ImageryJobIndicator job={activeImageryJob} />}
      <div style={{ flex: 1 }} />
      {statusRight}
      <span className="mono">{clock.toISOString().slice(0, 19)}Z</span>
    </footer>
  );
}

export default Shell;
